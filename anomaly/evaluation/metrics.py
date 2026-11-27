"""
Faza 7: metryki ewaluacji (METODOLOGIA D2, D9, D10, D21) dla jednego detektora w jednym wariancie.

Wejście: tabela score'ów z score_frozen.score_split (+ składniki RiskScore) i incidents_<split>.csv.
Wyjście: wiersze w formacie długim {scope, metric, value}; scope = overall | class:<c> |
intensity:<i> | class_intensity:<c>|<i> | incidents | incidents:<c> | errors | ranking.
"""
from __future__ import annotations

from typing import Dict, List, Set, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from anomaly.evaluation.risk import risk_score
from anomaly.models.scoring import _mcc

WINDOW_MINUTES = 30
RANK_K = (10, 20, 50)
ORDERS = ("chronological", "score", "risk")


# --- pomocnicze ------------------------------------------------------------------------

def d8_rank_score(norm: np.ndarray, excess: np.ndarray) -> np.ndarray:
    """Score zgodny z kolejnością D8 (norm, potem nadwyżka ogona); identyczne pary mają równy score."""
    norm, excess = np.asarray(norm, float), np.asarray(excess, float)
    order = np.lexsort((excess, norm))                          # rosnąco
    n_s, e_s = norm[order], excess[order]
    new_group = np.r_[True, (np.diff(n_s) != 0) | (np.diff(e_s) != 0)]
    out = np.empty(len(norm))
    out[order] = np.cumsum(new_group)
    return out


def binary_metrics(y: np.ndarray, alarm: np.ndarray) -> Dict[str, float]:
    y, a = np.asarray(y).astype(bool), np.asarray(alarm).astype(bool)
    tp, fp = int((a & y).sum()), int((a & ~y).sum())
    fn, tn = int((~a & y).sum()), int((~a & ~y).sum())
    mcc = float(_mcc(np.array([tp], float), np.array([fp], float), np.array([fn], float), np.array([tn], float))[0])
    return {"precision": tp / (tp + fp) if tp + fp else 0.0, "recall": tp / (tp + fn) if tp + fn else 0.0,
            "fpr": fp / (fp + tn) if fp + tn else 0.0, "mcc": mcc, "tp": tp, "fp": fp}


def window_incidents(scores: pd.DataFrame) -> List[Set[str]]:
    return [set(x for x in str(ids).split(";") if x) for ids in scores["IncidentIds"]]


# --- metryki per okno ---------------------------------------------------------------------

def _subset_rows(scope: str, y: np.ndarray, rank: np.ndarray, alarm_main: np.ndarray,
                 alarm_r80: np.ndarray) -> List[dict]:
    rows = [{"scope": scope, "metric": "n_pos", "value": int(y.sum())},
            {"scope": scope, "metric": "n_neg", "value": int((~y).sum())}]
    if y.any() and (~y).any():
        rows.append({"scope": scope, "metric": "pr_auc", "value": float(average_precision_score(y, rank))})
    m = binary_metrics(y, alarm_main)
    rows += [{"scope": scope, "metric": f"main_{k}", "value": m[k]} for k in ("precision", "recall", "fpr", "mcc")]
    r = binary_metrics(y, alarm_r80)
    rows += [{"scope": scope, "metric": f"r80_{k}", "value": r[k]} for k in ("recall", "fpr")]
    return rows


def window_metrics(scores: pd.DataFrame, det: str, incidents: pd.DataFrame) -> List[dict]:
    """Łącznie, per klasa, per intensywność i per klasa × intensywność (D2: negatywy = okna normalne)."""
    rank = d8_rank_score(scores[f"{det}_norm"], scores[f"{det}_excess"])
    a_main = scores[f"{det}_alarm_main"].to_numpy() == 1
    a_r80 = scores[f"{det}_alarm_recall80"].to_numpy() == 1
    normal = scores["IsAnomaly"].to_numpy() == 0
    rows = _subset_rows("overall", ~normal, rank, a_main, a_r80)

    info = incidents.set_index("incident_id")[["anomaly_class", "intensity"]]
    win_inc = window_incidents(scores)
    attrs = [{(info.at[i, "anomaly_class"], info.at[i, "intensity"]) for i in inc if i in info.index}
             for inc in win_inc]
    classes = sorted(info["anomaly_class"].unique())
    intensities = sorted(info["intensity"].unique())
    subsets = [(f"class:{c}", lambda s, c=c: any(a[0] == c for a in s)) for c in classes]
    subsets += [(f"intensity:{i}", lambda s, i=i: any(a[1] == i for a in s)) for i in intensities]
    subsets += [(f"class_intensity:{c}|{i}", lambda s, c=c, i=i: (c, i) in s) for c in classes for i in intensities]
    for scope, pred in subsets:
        pos = np.array([pred(s) for s in attrs])
        keep = pos | normal
        rows += _subset_rows(scope, pos[keep], rank[keep], a_main[keep], a_r80[keep])
    return rows


# --- metryki per incydent (D9) --------------------------------------------------------------

def incident_table(scores: pd.DataFrame, det: str, incidents: pd.DataFrame) -> pd.DataFrame:
    """Jeden wiersz na incydent: wykryty?, opóźnienie [min] = koniec 1. okna z alarmem − 1. zdarzenie."""
    win_inc = window_incidents(scores)
    alarm = scores[f"{det}_alarm_main"].to_numpy() == 1
    end = (scores["WindowStart"] + pd.Timedelta(minutes=WINDOW_MINUTES)).to_numpy()
    first_alarm: Dict[str, np.datetime64] = {}
    n_windows: Dict[str, int] = {}
    for k, inc in enumerate(win_inc):
        for i in inc:
            n_windows[i] = n_windows.get(i, 0) + 1
            if alarm[k] and (i not in first_alarm or end[k] < first_alarm[i]):
                first_alarm[i] = end[k]
    t = incidents[["incident_id", "anomaly_class", "intensity", "severity_level", "start"]].copy()
    t["start"] = pd.to_datetime(t["start"], format="ISO8601")
    t["n_windows"] = t["incident_id"].map(n_windows).fillna(0).astype(int)
    t["detected"] = t["incident_id"].isin(first_alarm)
    t["first_alarm_end"] = pd.to_datetime(t["incident_id"].map(first_alarm))
    t["delay_min"] = (t["first_alarm_end"] - t["start"]).dt.total_seconds() / 60
    return t


def incident_metrics(table: pd.DataFrame) -> List[dict]:
    rows = []
    for scope, part in [("incidents", table)] + [(f"incidents:{c}", g) for c, g in table.groupby("anomaly_class")]:
        det = part[part["detected"]]
        rows += [{"scope": scope, "metric": "inc_n", "value": int(len(part))},
                 {"scope": scope, "metric": "inc_recall", "value": float(part["detected"].mean()) if len(part) else 0.0},
                 {"scope": scope, "metric": "inc_undetected_share",
                  "value": float(1 - part["detected"].mean()) if len(part) else 0.0}]
        if len(det):
            rows += [{"scope": scope, "metric": "inc_delay_median_min", "value": float(det["delay_min"].median())},
                     {"scope": scope, "metric": "inc_delay_p90_min", "value": float(det["delay_min"].quantile(0.9))}]
    return rows


# --- analiza błędów (RQ1) ---------------------------------------------------------------------

def error_metrics(scores: pd.DataFrame, det: str) -> List[dict]:
    normal = scores["IsAnomaly"].to_numpy() == 0
    alarm = scores[f"{det}_alarm_main"].to_numpy() == 1
    hard = (scores["HardNormal"].astype(str) != "").to_numpy()
    fp = alarm & normal
    rows = [{"scope": "errors", "metric": "fp_n", "value": int(fp.sum())},
            {"scope": "errors", "metric": "normal_hard_share", "value": float(hard[normal].mean())},
            {"scope": "errors", "metric": "fpr_hard",
             "value": float(alarm[normal & hard].mean()) if (normal & hard).any() else 0.0},
            {"scope": "errors", "metric": "fpr_plain",
             "value": float(alarm[normal & ~hard].mean()) if (normal & ~hard).any() else 0.0}]
    if fp.any():
        rows.append({"scope": "errors", "metric": "fp_hard_share", "value": float(hard[fp].mean())})
        tags: Dict[str, int] = {}
        for tag_list in scores.loc[fp, "HardNormal"].astype(str):
            for tag in filter(None, tag_list.split(";")):
                tags[tag] = tags.get(tag, 0) + 1
        rows += [{"scope": "errors", "metric": f"fp_tag:{t}", "value": n} for t, n in sorted(tags.items())]
    return rows


# --- ranking kolejki alertów (H5) ---------------------------------------------------------------

def _dcg(rel: np.ndarray, k: int) -> float:
    rel = rel[:k]
    return float((rel / np.log2(np.arange(2, len(rel) + 2))).sum())


def ranking_orders(alerts: pd.DataFrame, det: str) -> Dict[str, np.ndarray]:
    """Kolejność kolejki alertów: chronologiczna, według score'u (D8) i według RiskScore (remisy: D8, czas)."""
    t = alerts["WindowStart"].to_numpy().astype("datetime64[ns]").astype(np.int64)
    u = pd.Categorical(alerts["UserId"]).codes
    d8 = d8_rank_score(alerts[f"{det}_norm"], alerts[f"{det}_excess"])
    risk = alerts[f"{det}_risk"].to_numpy()
    return {"chronological": np.lexsort((u, t)),
            "score": np.lexsort((u, t, -d8)),
            "risk": np.lexsort((u, t, -d8, -risk))}


def ranking_metrics(scores: pd.DataFrame, det: str, ks=RANK_K) -> List[dict]:
    alerts = scores[scores[f"{det}_alarm_main"] == 1].reset_index(drop=True)
    rel_all = alerts["Severity"].to_numpy(float)
    rows = [{"scope": "ranking", "metric": "n_alerts", "value": int(len(alerts))},
            {"scope": "ranking", "metric": "n_alerts_anomalous", "value": int((alerts["IsAnomaly"] == 1).sum())}]
    if alerts.empty:
        return rows
    ideal = np.sort(rel_all)[::-1]
    for name, order in ranking_orders(alerts, det).items():
        rel = rel_all[order]
        hit = (alerts["IsAnomaly"].to_numpy()[order] == 1).astype(float)
        for k in ks:
            rows.append({"scope": "ranking", "metric": f"{name}_p@{k}", "value": float(hit[:k].sum() / k)})
            idcg = _dcg(ideal, k)
            if idcg > 0:
                rows.append({"scope": "ranking", "metric": f"{name}_ndcg@{k}", "value": _dcg(rel, k) / idcg})
    return rows


def add_risk(scores: pd.DataFrame, det: str) -> None:
    scores[f"{det}_risk"] = risk_score(scores[f"{det}_norm"], scores["severity_weight"], scores["context_weight"])


def detector_metrics(scores: pd.DataFrame, det: str, incidents: pd.DataFrame) -> Tuple[List[dict], pd.DataFrame]:
    inc = incident_table(scores, det, incidents)
    rows = window_metrics(scores, det, incidents) + incident_metrics(inc) + error_metrics(scores, det) \
        + ranking_metrics(scores, det)
    return rows, inc
