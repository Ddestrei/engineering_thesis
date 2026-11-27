"""
Testy Fazy 6: detektory (D12, D13, ML), normalizacja score'u (D8), progi (D7) i orkiestracja wariantów.
"""
import numpy as np
import pandas as pd
import pytest

from anomaly.features.windowing import FEATURE_GROUPS, ID_COLUMNS, LABEL_COLUMNS, feature_columns
from anomaly.models import training
from anomaly.models.detectors import (DETECTORS, IsolationForestDetector, LOFDetector, OneClassSVMDetector,
                                      RobustZScoreDetector, RuleBasedDetector, hyperparameter_grid, robust_scale)
from anomaly.models.scoring import (NO_ALARM, ScoreNormalizer, metrics_at, rank_key, select_main_threshold,
                                    select_recall_threshold)

RNG = np.random.default_rng(0)


# --- D13: reguły --------------------------------------------------------------------------

def test_rules_thresholds_from_train_percentiles_and_score_counts_rules():
    feats = ["operation_count", "hour_rarity", "rare_resource_score", "select_ratio", "insert_ratio",
             "update_ratio", "delete_ratio", "mean_duration"]
    X = np.column_stack([np.arange(1, 101), np.linspace(0, 3, 100), np.zeros(100),
                         np.full(100, 0.5), np.full(100, 0.5), np.zeros(100), np.zeros(100), RNG.normal(size=100)])
    det = RuleBasedDetector().fit(X, feats)
    assert det.upper_["operation_count"] == pytest.approx(np.percentile(np.arange(1, 101), 99))
    normal = X[50:51]
    spike = normal.copy(); spike[0, 0] = 500
    everything = spike.copy(); everything[0, 1] = 10; everything[0, 2] = 1; everything[0, 6] = 0.9; everything[0, 5] = 0.1
    assert det.score(normal)[0] == 0
    assert det.score(spike)[0] == 1
    assert det.score(everything)[0] == 4            # dwa udziały poza [P1, P99] to nadal jedna reguła


def test_rules_skip_rules_without_features():
    X = RNG.normal(size=(50, 2))
    det = RuleBasedDetector().fit(X, ["operation_count", "mean_duration"])
    assert set(det.upper_) == {"operation_count"} and det.ratio_bounds_ == {}
    assert det.score(X).max() <= 1


# --- D12: robust ---------------------------------------------------------------------------

@pytest.mark.parametrize("x, level, scale", [
    (np.r_[np.arange(10.0), 100.0], "mad", 1.4826 * 3),
    (np.r_[np.zeros(60), np.ones(40)], "iqr", 1 / 1.349),
    (np.r_[np.zeros(97), np.full(3, 5.0)], "range", max(1e-3, 0.01 * 5.0)),
    (np.r_[np.zeros(100), 5.0], "range", 1e-3),
    (np.zeros(50), "constant", 1.0),
    (np.full(50, np.sin(np.pi)) + np.r_[0, 1e-17 * np.arange(49)], "constant", 1.0),   # szum float, nie MAD
])
def test_robust_scale_cascade(x, level, scale):
    _, s, lvl = robust_scale(x)
    assert lvl == level and s == pytest.approx(scale)


def test_robust_score_is_mean_of_top3_and_ignores_binary_features():
    feats = ["a", "b", "c", "d", "is_weekend"]
    X = np.column_stack([RNG.normal(size=200) for _ in range(4)] + [np.zeros(200)])
    det = RobustZScoreDetector().fit(X, feats)
    assert [feats[i] for i in det.idx_] == ["a", "b", "c", "d"]
    probe = np.array([[10.0, 0.0, 5.0, 1.0, 1.0]])
    z = det.z(probe)[0]
    assert det.score(probe)[0] == pytest.approx(np.sort(z)[-3:].mean())


# --- ML ---------------------------------------------------------------------------------

@pytest.mark.parametrize("det", [IsolationForestDetector(n_estimators=100, max_samples=64, random_state=1),
                                 OneClassSVMDetector(nu=0.05, gamma_mult=1.0), LOFDetector(n_neighbors=10)])
def test_ml_score_is_higher_for_outliers(det):
    X = RNG.normal(size=(150, 4))
    det.fit(X, list("abcd"))
    s = det.score(np.array([[0, 0, 0, 0], [8, 8, -8, 8]], dtype=float))
    assert s[1] > s[0]


def test_hyperparameters_capped_to_unit_size():
    X = RNG.normal(size=(20, 3))
    assert IsolationForestDetector(max_samples=256).fit(X, list("abc")).effective_hp_["max_samples"] == 20
    assert LOFDetector(n_neighbors=35).fit(X, list("abc")).effective_hp_["n_neighbors"] == 19
    assert len(hyperparameter_grid("iforest", 1)) + len(hyperparameter_grid("ocsvm", 1)) \
        + len(hyperparameter_grid("lof", 1)) == 19


# --- D8: normalizacja ---------------------------------------------------------------------

def test_normalizer_ecdf_body_and_continuous_monotone_tail():
    ref = np.arange(1.0, 11.0)                                  # n = 10, s_max = 10
    norm = ScoreNormalizer.fit(ref)
    assert norm.transform(np.array([5.0]))[0] == pytest.approx(5 / 11)
    assert norm.transform(np.array([10.0]))[0] == pytest.approx(10 / 11)
    assert norm.transform(np.array([10.0 + 1e-9]))[0] == pytest.approx(10 / 11, abs=1e-8)   # ciągłość
    s = np.linspace(0, 40, 400)
    t = norm.transform(s)
    assert np.all(np.diff(t) >= 0) and t.max() < 1
    assert norm.sigma == pytest.approx(1.4826 * np.median(np.abs(ref - np.median(ref))))


def test_normalizer_degenerate_reference_and_tie_breaking():
    norm = ScoreNormalizer.fit(np.zeros(30))                    # reguły: w normie zawsze 0
    assert norm.sigma_level == "one"
    s = np.array([0.0, 1.0, 2.0, 500.0, 900.0])
    t = norm.transform(s)
    assert t[1] < t[2] < 1 and t[3] == t[4] == 1.0              # zaokrąglenie float64 przy ogromnym x
    order = rank_key(t, norm.excess(s))
    assert list(order[:2]) == [4, 3]                            # remis rozstrzyga nadwyżka ogona


# --- D7: progi -----------------------------------------------------------------------------

def test_main_threshold_respects_fpr_and_maximises_mcc():
    y = np.r_[np.zeros(100), np.ones(10)].astype(int)
    s = np.r_[np.linspace(0, 0.9, 100), np.linspace(0.85, 1.0, 10)]
    m = select_main_threshold(y, s)
    assert m["fpr"] <= 0.05
    for t in np.unique(s):                                      # brak lepszego progu przy FPR <= 5%
        other = metrics_at(y, s, t)
        if other["fpr"] <= 0.05:
            assert other["mcc"] <= m["mcc"] + 1e-12


def test_main_threshold_can_be_no_alarm_and_recall_point():
    y = np.array([0, 0, 0, 1, 0, 1])
    s = np.array([0.9, 0.9, 0.9, 0.9, 0.1, 0.2])                # każdy próg z alarmem daje FPR > 5%
    assert select_main_threshold(y, s)["threshold"] == NO_ALARM
    r = select_recall_threshold(np.r_[np.zeros(10), np.ones(5)].astype(int), np.r_[np.zeros(10), np.arange(1, 6)])
    assert r["recall"] >= 0.8 and r["threshold"] == 2.0         # najwyższy próg z recall >= 0,8


# --- orkiestracja wariantów -----------------------------------------------------------------

def _windows(n_per_user, anomalies=False, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for role in ("SALES", "CFO"):
        for k in (1, 2):
            for i in range(n_per_user):
                row = {c: rng.normal() for c in feature_columns("global")}
                row.update({c: 0.0 for c in feature_columns("global") if c.startswith(("role_", "device_"))})
                row.update({f"role_{role}": 1.0, "device_Office_PC": 1.0, "is_weekend": 0.0})
                anom = anomalies and i % 8 == 0
                if anom:
                    row["operation_count"] += 8
                rows.append({"UserId": f"{role}_0{k}", "RoleId": role,
                             "WindowStart": pd.Timestamp("2026-01-05") + pd.Timedelta(minutes=30 * i),
                             "IsAnomaly": int(anom), "IncidentIds": "I" if anom else "", "AnomalyClasses": "",
                             "Intensities": "", "Severity": 0, "HardNormal": "", **row})
    return pd.DataFrame(rows)


def test_test_split_is_never_loaded():
    with pytest.raises(ValueError, match="nie czyta TEST"):
        training.load_windows("user", "test", 11)


@pytest.mark.parametrize("variant, n_models, groups", [("global", 1, {"ALL"}), ("role", 2, {"SALES", "CFO"}),
                                                        ("user", 4, {"SALES", "CFO"})])
def test_variants_units_and_threshold_groups(variant, n_models, groups):
    train, val = _windows(40), _windows(40, anomalies=True, seed=1)
    feats = feature_columns(variant)
    res = training.tune_detector("robust", variant, train, val, feats, seed=1)
    assert len(res["_models"]) == n_models
    assert set(res["thresholds"]) == groups                     # per użytkownik: próg wspólny dla roli
    assert res["val_metrics"]["pr_auc"] > 0.5
    for unit in res["units"].values():                          # normalizacja tylko z normalnych okien VAL
        assert unit["normalizer"]["n"] == len(val[val.IsAnomaly == 0]) // n_models


def test_user_with_few_train_windows_trains_on_role():
    train = _windows(40)
    train = train[~((train.UserId == "CFO_02") & (train.WindowStart > pd.Timestamp("2026-01-05 05:00")))]
    models = training.fit_units("rules", {}, train, feature_columns("user"), "user")
    small = train[train.UserId == "CFO_02"]
    role = train[train.RoleId == "CFO"]
    assert len(small) < training.MIN_USER_WINDOWS
    assert models["CFO_02"].upper_["operation_count"] == pytest.approx(np.percentile(role["operation_count"], 99))


def test_all_detectors_run_end_to_end_on_small_data():
    train, val = _windows(40), _windows(40, anomalies=True, seed=2)
    for det in DETECTORS:
        res = training.tune_detector(det, "role", train, val, feature_columns("role", ["volume", "time"]), seed=3)
        assert np.isfinite(res["_norm"]).all() and (res["_norm"] < 1 + 1e-12).all()
        assert res["grid"] and res["hyperparameters"] == max(res["grid"], key=lambda g: g["val_pr_auc"])["hyperparameters"] \
            or len(res["grid"]) == 1
