"""
Faza 7: statystyka po ziarnach (METODOLOGIA D6) i kryteria hipotez (D21).

- mean ± std i 95% CI t-Studenta (n ziaren, n − 1 stopni swobody),
- różnica sparowana po ziarnach: „istotna”, gdy CI całkowicie powyżej (albo poniżej) 0,
  „porównywalna”, gdy CI zawiera 0.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats as st

KEY = ["config", "variant", "detector", "scope", "metric"]


def ci95(values) -> Dict[str, float]:
    v = np.asarray([x for x in values if pd.notna(x)], dtype=float)
    n = len(v)
    if n == 0:
        return {"n": 0, "mean": np.nan, "std": np.nan, "ci_low": np.nan, "ci_high": np.nan}
    mean = float(v.mean())
    if n == 1:
        return {"n": 1, "mean": mean, "std": np.nan, "ci_low": np.nan, "ci_high": np.nan}
    std = float(v.std(ddof=1))
    half = float(st.t.ppf(0.975, n - 1) * std / np.sqrt(n))
    return {"n": n, "mean": mean, "std": std, "ci_low": mean - half, "ci_high": mean + half}


def summarize(long: pd.DataFrame) -> pd.DataFrame:
    """Średnia, odchylenie i 95% CI po ziarnach dla każdej metryki."""
    rows = [{**dict(zip(KEY, k)), **ci95(g["value"])} for k, g in long.groupby(KEY, sort=True)]
    return pd.DataFrame(rows)


def paired(long: pd.DataFrame, a: dict, b: dict, scope: str, metric: str) -> Optional[dict]:
    """Różnica sparowana po ziarnach (a − b). a, b: {config, variant, detector}."""
    def sel(x):
        m = (long["scope"] == scope) & (long["metric"] == metric)
        for k, v in x.items():
            m &= long[k] == v
        return long[m].set_index("seed")["value"]
    va, vb = sel(a), sel(b)
    seeds = sorted(set(va.dropna().index) & set(vb.dropna().index))
    if len(seeds) < 2:
        return None
    diff = va.loc[seeds].to_numpy(float) - vb.loc[seeds].to_numpy(float)
    c = ci95(diff)
    return {"scope": scope, "metric": metric, "a": "/".join(map(str, a.values())), "b": "/".join(map(str, b.values())),
            "seeds": len(seeds), "mean_a": float(va.loc[seeds].mean()), "mean_b": float(vb.loc[seeds].mean()),
            "diff_mean": c["mean"], "diff_ci_low": c["ci_low"], "diff_ci_high": c["ci_high"],
            "verdict": "a > b" if c["ci_low"] > 0 else ("a < b" if c["ci_high"] < 0 else "porównywalne")}


# --- hipotezy (D21) ---------------------------------------------------------------------

ML = ("iforest", "ocsvm", "lof")
NON_RULES = ("robust",) + ML


def evaluate_hypotheses(long: pd.DataFrame, detectors: List[str], variants: List[str],
                        singles=("time", "volume", "context"), triple="time+volume+context") -> Dict[str, list]:
    full = long[long["config"] == "full"]
    out: Dict[str, list] = {"RQ1": [], "H1": [], "H2": [], "H3'": [], "H4'": [], "H5": [], "comparisons": []}

    def cmp(a, b, scope, metric):
        r = paired(long, a, b, scope, metric)
        if r is not None:
            out["comparisons"].append(r)
        return r

    # RQ1: średni FPR <= 5% i recall >= 0,50 przy głównym progu.
    for v in variants:
        for d in detectors:
            sel = full[(full.variant == v) & (full.detector == d) & (full.scope == "overall")]
            fpr = sel[sel.metric == "main_fpr"]["value"].mean()
            rec = sel[sel.metric == "main_recall"]["value"].mean()
            out["RQ1"].append({"variant": v, "detector": d, "fpr": fpr, "recall": rec,
                               "confirmed": bool(fpr <= 0.05 and rec >= 0.50)})

    # H1: per rola / per użytkownik vs global przy progu recall = 0,80.
    for d in detectors:
        for v in [x for x in variants if x != "global"]:
            if "global" not in variants:
                continue
            a, b = {"config": "full", "variant": v, "detector": d}, {"config": "full", "variant": "global", "detector": d}
            f, r = cmp(a, b, "overall", "r80_fpr"), cmp(a, b, "overall", "r80_recall")
            if f and r:
                out["H1"].append({"detector": d, "variant": v, "fpr_diff": f["diff_mean"], "fpr_ci": (f["diff_ci_low"], f["diff_ci_high"]),
                                  "recall_diff": r["diff_mean"],
                                  "confirmed": bool(f["diff_ci_high"] < 0 and abs(r["diff_mean"]) <= 0.05)})

    # H2: Czas+Wolumen+Kontekst vs każda grupa osobno (PR-AUC; łącznie i per klasa).
    scopes = sorted(s for s in long["scope"].unique() if s == "overall" or s.startswith("class:"))
    for v in variants:
        for d in detectors:
            for scope in scopes:
                res = [cmp({"config": triple, "variant": v, "detector": d}, {"config": s, "variant": v, "detector": d},
                           scope, "pr_auc") for s in singles]
                if all(res):
                    out["H2"].append({"variant": v, "detector": d, "scope": scope,
                                      "diffs": {s: r["diff_mean"] for s, r in zip(singles, res)},
                                      "confirmed": all(r["diff_ci_low"] > 0 for r in res)})

    # H3': co najmniej jeden model ML istotnie lepszy od reguł (PR-AUC lub MCC).
    for v in variants:
        better = []
        for m in [x for x in ML if x in detectors]:
            for metric in ("pr_auc", "main_mcc"):
                r = cmp({"config": "full", "variant": v, "detector": m}, {"config": "full", "variant": v, "detector": "rules"},
                        "overall", metric)
                if r and r["diff_ci_low"] > 0:
                    better.append(f"{m}:{metric}")
        out["H3'"].append({"variant": v, "better_than_rules": better, "confirmed": bool(better)})

    # H4': silne – MCC porównywalne z regułami; słabe – PR-AUC istotnie wyższe niż reguł.
    for v in variants:
        for m in [x for x in NON_RULES if x in detectors]:
            a, b = {"config": "full", "variant": v, "detector": m}, {"config": "full", "variant": v, "detector": "rules"}
            strong, weak = cmp(a, b, "intensity:strong", "main_mcc"), cmp(a, b, "intensity:weak", "pr_auc")
            if strong and weak:
                out["H4'"].append({"variant": v, "detector": m, "strong_mcc_diff": strong["diff_mean"],
                                   "strong_comparable": strong["verdict"] == "porównywalne",
                                   "weak_pr_auc_diff": weak["diff_mean"], "weak_better": weak["diff_ci_low"] > 0,
                                   "confirmed": bool(strong["verdict"] == "porównywalne" and weak["diff_ci_low"] > 0)})

    # H5: RiskScore vs chronologia i vs score (NDCG@K, P@K).
    for v in variants:
        for d in detectors:
            for k in (10, 20, 50):
                res = {}
                for other in ("chronological", "score"):
                    for kind in ("ndcg", "p"):
                        r = _rank_pair(long, v, d, k, other, kind)
                        if r:
                            out["comparisons"].append(r)
                            res[f"{kind}_vs_{other}"] = r
                if res:
                    out["H5"].append({"variant": v, "detector": d, "k": k,
                                      **{f"{n}_diff": r["diff_mean"] for n, r in res.items()},
                                      "confirmed": len(res) == 4 and all(r["diff_ci_low"] > 0 for r in res.values())})
    return out


def _rank_pair(long: pd.DataFrame, variant: str, detector: str, k: int, other: str, kind: str) -> Optional[dict]:
    """Różnica sparowana risk − other dla miary rankingu (ten sam detektor i wariant)."""
    base = long[(long.config == "full") & (long.variant == variant) & (long.detector == detector) & (long.scope == "ranking")]
    a = base[base.metric == f"risk_{kind}@{k}"].set_index("seed")["value"]
    b = base[base.metric == f"{other}_{kind}@{k}"].set_index("seed")["value"]
    seeds = sorted(set(a.dropna().index) & set(b.dropna().index))
    if len(seeds) < 2:
        return None
    c = ci95(a.loc[seeds].to_numpy(float) - b.loc[seeds].to_numpy(float))
    return {"scope": "ranking", "metric": f"{kind}@{k}", "a": f"full/{variant}/{detector}/risk",
            "b": f"full/{variant}/{detector}/{other}", "seeds": len(seeds), "mean_a": float(a.loc[seeds].mean()),
            "mean_b": float(b.loc[seeds].mean()), "diff_mean": c["mean"], "diff_ci_low": c["ci_low"],
            "diff_ci_high": c["ci_high"],
            "verdict": "a > b" if c["ci_low"] > 0 else ("a < b" if c["ci_high"] < 0 else "porównywalne")}
