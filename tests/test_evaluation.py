"""
Testy Fazy 7: RiskScore (D10), metryki per okno / incydent / ranking (D21), statystyka (D6),
konfiguracje ablacji (D14) i zabezpieczenia scoringu z zamrożonej konfiguracji.
"""
import json

import numpy as np
import pandas as pd
import pytest

from anomaly.evaluation import score_frozen
from anomaly.evaluation.ablation import ABLATION_CONFIGS
from anomaly.evaluation.metrics import (binary_metrics, d8_rank_score, incident_metrics, incident_table,
                                        ranking_metrics, ranking_orders, window_metrics)
from anomaly.evaluation.risk import primary_devices, risk_components, risk_score
from anomaly.evaluation.stats import ci95, evaluate_hypotheses, paired
from anomaly.features.windowing import FEATURE_GROUPS


# --- RiskScore --------------------------------------------------------------------------

def test_risk_components_from_observable_window_data():
    t0 = pd.Timestamp("2026-04-01 10:00")
    train = pd.DataFrame({"UserId": ["U1"] * 3, "device": ["Office_PC", "Office_PC", "Home_PC"]})
    ev = pd.DataFrame({"UserId": ["U1"] * 3, "WindowStart": [t0, t0, t0 + pd.Timedelta(minutes=30)],
                       "target_table": ["Product", "Customer", "ProductCategory"],
                       "operation_type": ["UPDATE", "SELECT", "SELECT"],
                       "device": ["Office_PC", "Office_PC", "Home_PC"]})
    uw = pd.DataFrame({"UserId": ["U1", "U1"], "WindowStart": [t0, t0 + pd.Timedelta(minutes=30)],
                       "after_hours_flag": [0, 1], "ip_known_flag": [1, 0]})
    primary = primary_devices(train)
    assert primary == {"U1": "Office_PC"}
    c = risk_components(ev, uw, primary).set_index("WindowStart")
    assert c.loc[t0, "severity_weight"] == pytest.approx(max(0.4 * 1.0, 1.0 * 0.7))     # Product zapis vs Customer odczyt
    assert c.loc[t0, "context_weight"] == 0
    later = c.loc[t0 + pd.Timedelta(minutes=30)]
    assert later["severity_weight"] == pytest.approx(0.2 * 0.7)
    assert later["context_weight"] == pytest.approx(1.0)                                # po godzinach, inne urządzenie, nowe IP
    assert risk_score(np.array([1.0]), np.array([1.0]), np.array([1.0]))[0] == pytest.approx(1.0)


# --- metryki per okno ---------------------------------------------------------------------

def _scores(n_normal=40):
    t = pd.Timestamp("2026-04-06 09:00")
    rows = []
    for i in range(n_normal):
        rows.append({"UserId": f"U{i % 4}", "WindowStart": t + pd.Timedelta(minutes=30 * i), "IsAnomaly": 0,
                     "IncidentIds": "", "Severity": 0, "HardNormal": "HARD_NORMAL_SELECT_SPIKE" if i < 4 else "",
                     "d_norm": i / 100, "d_excess": 0.0})
    anomalies = [("INC-A", 0.95, 3), ("INC-A", 0.90, 3), ("INC-B", 0.20, 2), ("INC-C", 0.99, 5)]
    for k, (inc, s, sev) in enumerate(anomalies):
        rows.append({"UserId": "U9", "WindowStart": t + pd.Timedelta(days=1, minutes=30 * k), "IsAnomaly": 1,
                     "IncidentIds": inc, "Severity": sev, "HardNormal": "", "d_norm": s, "d_excess": 0.0})
    df = pd.DataFrame(rows)
    df["d_alarm_main"] = (df["d_norm"] >= 0.38).astype(int)        # alarmy: 2 normalne (0,38; 0,39) i 3 anomalie
    df["d_alarm_recall80"] = (df["d_norm"] >= 0.2).astype(int)
    df["d_risk"] = df["d_norm"] + 0.01 * df["Severity"]
    incidents = pd.DataFrame({"incident_id": ["INC-A", "INC-B", "INC-C"],
                              "anomaly_class": ["volume_spike", "unusual_time", "volume_spike"],
                              "intensity": ["strong", "weak", "strong"], "severity_level": [3, 2, 5],
                              "start": [str(t + pd.Timedelta(days=1, minutes=10)), str(t + pd.Timedelta(days=1, minutes=60)),
                                        str(t + pd.Timedelta(days=1, minutes=95))]})
    return df, incidents


def _value(rows, scope, metric):
    found = [r["value"] for r in rows if r["scope"] == scope and r["metric"] == metric]
    assert len(found) == 1, (scope, metric)
    return found[0]


def test_window_metrics_overall_and_per_class_use_normal_negatives():
    df, incidents = _scores()
    rows = window_metrics(df, "d", incidents)
    assert _value(rows, "overall", "main_recall") == pytest.approx(3 / 4)
    assert _value(rows, "overall", "main_fpr") == pytest.approx(2 / 40)
    assert _value(rows, "class:volume_spike", "n_pos") == 3 and _value(rows, "class:volume_spike", "n_neg") == 40
    assert _value(rows, "class:unusual_time", "main_recall") == 0
    assert _value(rows, "intensity:strong", "main_recall") == 1.0
    assert _value(rows, "class_intensity:unusual_time|weak", "n_pos") == 1


def test_incident_detection_and_delay():
    df, incidents = _scores()
    table = incident_table(df, "d", incidents).set_index("incident_id")
    assert table.loc["INC-A", "detected"] and not table.loc["INC-B", "detected"]
    # INC-A: pierwsze okno z alarmem 09:00 następnego dnia (koniec 09:30), start incydentu 09:10 -> 20 min.
    assert table.loc["INC-A", "delay_min"] == pytest.approx(20)
    assert table.loc["INC-C", "delay_min"] == pytest.approx(30 * 4 - 95)
    rows = incident_metrics(table.reset_index())
    assert _value(rows, "incidents", "inc_recall") == pytest.approx(2 / 3)
    assert _value(rows, "incidents:volume_spike", "inc_recall") == 1.0


def test_d8_rank_score_keeps_ties_and_breaks_saturation_by_excess():
    r = d8_rank_score(np.array([0.5, 1.0, 1.0, 0.5]), np.array([0.0, 3.0, 7.0, 0.0]))
    assert r[0] == r[3] and r[2] > r[1] > r[0]


def test_binary_metrics():
    m = binary_metrics(np.array([1, 1, 0, 0]), np.array([1, 0, 1, 0]))
    assert (m["precision"], m["recall"], m["fpr"], m["mcc"]) == (0.5, 0.5, 0.5, 0.0)


# --- ranking ---------------------------------------------------------------------------

def test_ranking_orders_and_metrics():
    df, _ = _scores()
    alerts = df[df.d_alarm_main == 1].reset_index(drop=True)
    orders = ranking_orders(alerts, "d")
    assert list(alerts.loc[orders["chronological"], "WindowStart"]) == sorted(alerts["WindowStart"])
    assert alerts.loc[orders["score"][0], "IncidentIds"] == "INC-C"
    rows = ranking_metrics(df, "d", ks=(3, 10))
    assert _value(rows, "ranking", "n_alerts") == 5
    assert _value(rows, "ranking", "score_p@3") == 1.0
    assert _value(rows, "ranking", "chronological_p@3") == pytest.approx(1 / 3)    # 2 normalne okna są wcześniej
    assert _value(rows, "ranking", "score_p@10") == pytest.approx(3 / 10)          # brakujące pozycje = nietrafione
    assert _value(rows, "ranking", "risk_ndcg@3") == pytest.approx(1.0)
    assert _value(rows, "ranking", "chronological_ndcg@3") < 1.0


def test_ranking_without_relevant_alerts_has_no_ndcg():
    df, _ = _scores()
    df = df[df.IsAnomaly == 0]
    rows = ranking_metrics(df, "d", ks=(3,))
    assert not [r for r in rows if "ndcg" in r["metric"]]


# --- statystyka i hipotezy --------------------------------------------------------------

def test_ci95_t_student():
    c = ci95([1.0, 2.0, 3.0, 4.0, 5.0])
    assert c["mean"] == 3 and c["ci_low"] == pytest.approx(3 - 2.776445 * np.sqrt(2.5) / np.sqrt(5), rel=1e-5)


def test_paired_difference_verdict():
    long = pd.DataFrame([{"seed": s, "config": "full", "variant": "role", "detector": d, "scope": "overall",
                          "metric": "pr_auc", "value": v}
                         for s, base in zip(range(5), (0.40, 0.42, 0.38, 0.41, 0.39))
                         for d, v in (("ocsvm", base + 0.1), ("rules", base))])
    r = paired(long, {"config": "full", "variant": "role", "detector": "ocsvm"},
               {"config": "full", "variant": "role", "detector": "rules"}, "overall", "pr_auc")
    assert r["verdict"] == "a > b" and r["diff_mean"] == pytest.approx(0.1)
    hyp = evaluate_hypotheses(long, ["ocsvm", "rules"], ["role"])
    assert hyp["H3'"][0]["confirmed"] and "ocsvm:pr_auc" in hyp["H3'"][0]["better_than_rules"]


def test_ablation_configs_follow_d14():
    assert len(ABLATION_CONFIGS) == 14
    assert ABLATION_CONFIGS["full"] == list(FEATURE_GROUPS)
    assert ABLATION_CONFIGS["time+volume+context"] == ["time", "volume", "context"]
    for g in FEATURE_GROUPS:
        assert g not in ABLATION_CONFIGS[f"no_{g}"] and len(ABLATION_CONFIGS[f"no_{g}"]) == 5


# --- zamrożona konfiguracja ------------------------------------------------------------

def test_tampered_frozen_config_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "frozen_config.json"
    path.write_text(json.dumps({"results": {}}), encoding="utf-8")
    from anomaly.versioning import sha256_file
    path.with_suffix(".sha256").write_text(sha256_file(path))
    monkeypatch.setattr(score_frozen, "frozen_config_json", lambda seed, config: path)
    assert score_frozen.load_frozen(1, "full")[0] == {"results": {}}
    path.write_text(json.dumps({"results": {"changed": 1}}), encoding="utf-8")
    with pytest.raises(score_frozen.FrozenConfigError, match="SHA-256"):
        score_frozen.load_frozen(1, "full")
