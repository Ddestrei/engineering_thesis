"""
Testy Fazy 8: budowa tabel bazy wyników dla panelu (bez połączenia z SQL Server).
"""
import numpy as np
import pandas as pd
import pytest

from anomaly.export.results_db import alert_rows, feature_rows, model_row, threshold_rows, window_rows
from anomaly.models.detectors import RobustZScoreDetector, RuleBasedDetector

FEATURES = ["operation_count", "select_ratio", "is_weekend"]


def _windows(n, users, start="2026-03-02 09:00", seed=0):
    rng = np.random.default_rng(seed)
    t = pd.Timestamp(start)
    rows = []
    for i in range(n):
        for u, role in users:
            rows.append({"UserId": u, "RoleId": role, "WindowStart": t + pd.Timedelta(minutes=30 * i),
                         "operation_count": int(rng.integers(3, 8)), "select_ratio": rng.uniform(0.5, 0.9),
                         "is_weekend": 0})
    return pd.DataFrame(rows)


def test_feature_rows_profile_z_and_rules():
    users = [("U1", "SALES"), ("U2", "SALES")]
    train = _windows(60, users)
    test = _windows(2, users, start="2026-04-01 09:00", seed=1)
    test.loc[0, "operation_count"] = 50                       # wyraźne odchylenie U1 w pierwszym oknie
    X = train[FEATURES].to_numpy(float)
    robust = {"SALES": RobustZScoreDetector().fit(X, FEATURES)}
    rules = {"SALES": RuleBasedDetector().fit(X, FEATURES)}
    f = feature_rows(test, train, FEATURES, "role", robust, rules)

    assert len(f) == len(test) * len(FEATURES)
    row = f[(f.UserId == "U1") & (f.WindowStart == test.loc[0, "WindowStart"]) & (f.Feature == "operation_count")].iloc[0]
    assert row.Value == 50
    assert row.UnitMedian == pytest.approx(train.operation_count.median())
    assert row.UnitP99 == pytest.approx(np.percentile(train.operation_count, 99))
    assert row.UserMedian == pytest.approx(train[train.UserId == "U1"].operation_count.median())
    m = robust["SALES"]
    j = [m.features_[i] for i in m.idx_].index("operation_count")
    assert row.RobustZ == pytest.approx(abs(50 - m.median_[j]) / m.scale_[j])
    assert row.RuleViolated == 1.0                            # operation_count > P99
    assert row.FeatureGroup == "volume"
    # cecha binarna: bez z (D12) i bez reguły
    binary = f[f.Feature == "is_weekend"]
    assert binary.RobustZ.isna().all() and binary.RuleViolated.isna().all()


def test_alert_rows_only_main_alarms_with_group_threshold():
    scores = pd.DataFrame({
        "UserId": ["U1", "U2", "U3"], "RoleId": ["SALES", "CFO", "SALES"],
        "WindowStart": pd.to_datetime(["2026-04-01 09:00"] * 3),
        "ocsvm_raw": [1.0, 2.0, 3.0], "ocsvm_norm": [0.97, 0.99, 0.5], "ocsvm_excess": [0.0, 0.0, 0.0],
        "ocsvm_risk": [0.8, 0.9, 0.4], "ocsvm_alarm_main": [1, 1, 0], "ocsvm_alarm_recall80": [1, 1, 1]})
    thr = {"SALES": {"main": {"threshold": 0.96}}, "CFO": {"main": {"threshold": 0.98}}}
    a = alert_rows(scores, "ocsvm", "user", thr)
    assert list(a.UserId) == ["U1", "U2"]
    assert list(a.ThresholdGroup) == ["SALES", "CFO"]
    assert list(a.MainThreshold) == [0.96, 0.98]
    g = alert_rows(scores, "ocsvm", "global", {"ALL": {"main": {"threshold": 0.95}}})
    assert set(g.ThresholdGroup) == {"ALL"}


def test_window_rows_requires_feature_windows():
    keys = {"UserId": ["U1"], "WindowStart": pd.to_datetime(["2026-04-01 09:00"])}
    scores = pd.DataFrame({**keys, "RoleId": ["SALES"], "IsAnomaly": [0], "IncidentIds": [""], "AnomalyClasses": [""],
                           "Intensities": [""], "Severity": [0], "HardNormal": [""], "severity_weight": [0.4],
                           "ctx_non_primary_device": [0], "ctx_after_hours": [0], "ctx_unknown_ip": [0],
                           "context_weight": [0.0]})
    ok = window_rows(scores, pd.DataFrame({**keys, "operation_count": [5]}))
    assert ok.operation_count.iloc[0] == 5
    with pytest.raises(Exception, match="bez okna"):
        window_rows(scores, pd.DataFrame({"UserId": ["U2"], "WindowStart": keys["WindowStart"], "operation_count": [5]}))


def test_model_and_threshold_rows():
    res = {"variant": "role", "detector": "lof", "hyperparameters": {"n_neighbors": 5},
           "val_metrics": {"pr_auc": 0.5, "main": {"fpr": 0.02, "recall": 0.4, "mcc": 0.45}},
           "thresholds": {"CEO": {"main": {"threshold": 0.97, "fpr": 0.01, "recall": 0.3},
                                  "recall80": {"threshold": 0.6}}}}
    m = model_row(res, 28, 70)
    assert m["Hyperparameters"] == '{"n_neighbors": 5}' and m["ValPrAuc"] == 0.5 and m["AlertCount"] == 70
    t = threshold_rows(res["thresholds"])
    assert t == [{"GroupKey": "CEO", "MainThreshold": 0.97, "Recall80Threshold": 0.6, "ValFpr": 0.01, "ValRecall": 0.3}]
