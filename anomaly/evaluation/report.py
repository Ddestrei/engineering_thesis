"""
Faza 7: raport zbiorczy RAPORT.md z wyników po ziarnach (średnia ± połowa 95% CI, D6).
Tabele są generowane z metrics_summary.csv i hypotheses.json; nie zawierają ręcznie wpisanych liczb.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

VARIANT_LABEL = {"global": "global", "role": "per rola", "user": "per użytkownik"}
DET_LABEL = {"rules": "reguły", "robust": "robust", "iforest": "IF", "ocsvm": "OCSVM", "lof": "LOF"}
DET_ORDER = ["rules", "robust", "iforest", "ocsvm", "lof"]
VAR_ORDER = ["global", "role", "user"]


def _fmt(mean, low, high, digits=3) -> str:
    if pd.isna(mean):
        return "–"
    if pd.isna(low):
        return f"{mean:.{digits}f}"
    return f"{mean:.{digits}f} ± {(high - low) / 2:.{digits}f}"


def _table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def _get(summary: pd.DataFrame, config, variant, detector, scope, metric):
    r = summary[(summary.config == config) & (summary.variant == variant) & (summary.detector == detector)
                & (summary.scope == scope) & (summary.metric == metric)]
    return (np.nan, np.nan, np.nan) if r.empty else tuple(r.iloc[0][["mean", "ci_low", "ci_high"]])


def _yes(x) -> str:
    return "TAK" if x else "nie"


def write_report(path: Path, split: str, summary: pd.DataFrame, hyp: dict, perf: pd.DataFrame, meta: dict) -> None:
    s = summary
    dets = [d for d in DET_ORDER if d in set(s.detector)]
    vars_ = [v for v in VAR_ORDER if v in set(s.variant)]
    out = [f"# Wyniki ewaluacji: {split.upper()}", ""]
    if split == "validation":
        out += ["> **VAL: kontrola łańcucha, nie wyniki pracy.** Na VAL stroiliśmy hiperparametry i progi.", ""]
    out += [f"Ziarna: {', '.join(map(str, meta['seeds']))}. Wartości: średnia ± połowa 95% CI t-Studenta (D6). "
            f"Wersja kodu: `{meta['code_version']['commit'][:7]}`{' (dirty)' if meta['code_version']['dirty'] else ''}. "
            "Protokół: METODOLOGIA D21.", ""]

    # Udział anomalii.
    pos = _get(s, "full", vars_[0], dets[0], "overall", "n_pos")[0]
    neg = _get(s, "full", vars_[0], dets[0], "overall", "n_neg")[0]
    out += [f"**Udział okien anomalnych:** {pos / (pos + neg):.1%} (średnio {pos:.0f} z {pos + neg:.0f} okien; "
            "poziom losowy PR-AUC).", ""]

    # Główna tabela.
    rows = []
    for v in vars_:
        for d in dets:
            g = lambda m, sc="overall": _fmt(*_get(s, "full", v, d, sc, m))
            rows.append([VARIANT_LABEL[v], DET_LABEL[d], g("pr_auc"), g("main_mcc"), g("main_fpr"), g("main_recall"),
                         g("main_precision"), g("r80_fpr"), g("r80_recall")])
    out += ["## Wyniki per okno (konfiguracja pełna)", "",
            _table(pd.DataFrame(rows, columns=["Wariant", "Detektor", "PR-AUC", "MCC", "FPR", "Recall", "Precision",
                                               "FPR @ próg recall 0,8", "Recall @ próg recall 0,8"])), ""]

    # Per klasa.
    classes = sorted(x.split(":", 1)[1] for x in s.scope.unique() if x.startswith("class:"))
    rows = [[VARIANT_LABEL[v], DET_LABEL[d]] + [_fmt(*_get(s, "full", v, d, f"class:{c}", "pr_auc")) for c in classes]
            for v in vars_ for d in dets]
    out += ["## PR-AUC per klasa anomalii (RQ5)", "", _table(pd.DataFrame(rows, columns=["Wariant", "Detektor"] + classes)), ""]
    rows = [[VARIANT_LABEL[v], DET_LABEL[d]] + [_fmt(*_get(s, "full", v, d, f"intensity:{i}", m))
                                                for i in ("strong", "weak") for m in ("main_mcc", "pr_auc")]
            for v in vars_ for d in dets]
    out += ["## Intensywność (H4′)", "", _table(pd.DataFrame(rows, columns=["Wariant", "Detektor", "MCC silne", "PR-AUC silne",
                                                                            "MCC słabe", "PR-AUC słabe"])), ""]

    # Incydenty i błędy.
    rows = [[VARIANT_LABEL[v], DET_LABEL[d], _fmt(*_get(s, "full", v, d, "incidents", "inc_recall")),
             _fmt(*_get(s, "full", v, d, "incidents", "inc_delay_median_min"), digits=0),
             _fmt(*_get(s, "full", v, d, "incidents", "inc_delay_p90_min"), digits=0),
             _fmt(*_get(s, "full", v, d, "errors", "fp_hard_share")), _fmt(*_get(s, "full", v, d, "errors", "normal_hard_share")),
             _fmt(*_get(s, "full", v, d, "errors", "fpr_hard")), _fmt(*_get(s, "full", v, d, "errors", "fpr_plain"))]
            for v in vars_ for d in dets]
    out += ["## Incydenty (D9) i analiza błędów (RQ1)", "",
            _table(pd.DataFrame(rows, columns=["Wariant", "Detektor", "Recall incydentów", "Opóźnienie mediana [min]",
                                               "Opóźnienie P90 [min]", "FP z HARD_NORMAL", "Udział HARD_NORMAL w normie",
                                               "FPR trudne", "FPR zwykłe"])), ""]

    # Hipotezy.
    out += ["## Hipotezy (kryteria D21)", "", "### RQ1: FPR ≤ 5% i recall ≥ 0,50", "",
            _table(pd.DataFrame([[VARIANT_LABEL[r["variant"]], DET_LABEL[r["detector"]], f"{r['fpr']:.3f}", f"{r['recall']:.3f}",
                                  _yes(r["confirmed"])] for r in hyp["RQ1"]],
                                columns=["Wariant", "Detektor", "FPR", "Recall", "Spełnione"])), ""]
    out += ["### H1: per rola / per użytkownik vs global (próg recall 0,8)", "",
            _table(pd.DataFrame([[DET_LABEL[r["detector"]], VARIANT_LABEL[r["variant"]], f"{r['fpr_diff']:+.3f}",
                                  f"[{r['fpr_ci'][0]:+.3f}; {r['fpr_ci'][1]:+.3f}]", f"{r['recall_diff']:+.3f}",
                                  _yes(r["confirmed"])] for r in hyp["H1"]],
                                columns=["Detektor", "Wariant", "ΔFPR", "95% CI ΔFPR", "ΔRecall", "Potwierdzona"])), ""]
    h2 = [r for r in hyp["H2"] if r["scope"] == "overall"]
    out += ["### H2: Czas+Wolumen+Kontekst vs pojedyncze grupy (ΔPR-AUC, łącznie)", "",
            _table(pd.DataFrame([[VARIANT_LABEL[r["variant"]], DET_LABEL[r["detector"]]]
                                 + [f"{r['diffs'][k]:+.3f}" for k in ("time", "volume", "context")] + [_yes(r["confirmed"])]
                                 for r in h2], columns=["Wariant", "Detektor", "vs Czas", "vs Wolumen", "vs Kontekst",
                                                        "Potwierdzona"])) if h2 else "brak konfiguracji ablacji", ""]
    out += ["### H3′: ML vs reguły", "",
            _table(pd.DataFrame([[VARIANT_LABEL[r["variant"]], ", ".join(r["better_than_rules"]) or "–", _yes(r["confirmed"])]
                                 for r in hyp["H3'"]], columns=["Wariant", "Istotnie lepsze od reguł", "Potwierdzona"])), ""]
    out += ["### H4′: silne – MCC porównywalne z regułami; słabe – PR-AUC wyższe", "",
            _table(pd.DataFrame([[VARIANT_LABEL[r["variant"]], DET_LABEL[r["detector"]], f"{r['strong_mcc_diff']:+.3f}",
                                  _yes(r["strong_comparable"]), f"{r['weak_pr_auc_diff']:+.3f}", _yes(r["weak_better"]),
                                  _yes(r["confirmed"])] for r in hyp["H4'"]],
                                columns=["Wariant", "Metoda", "ΔMCC silne", "porównywalne", "ΔPR-AUC słabe", "istotnie wyższe",
                                         "Potwierdzona"])), ""]
    out += ["### H5: RiskScore vs chronologia i vs score", "",
            _table(pd.DataFrame([[VARIANT_LABEL[r["variant"]], DET_LABEL[r["detector"]], r["k"]]
                                 + [f"{r.get(k, np.nan):+.3f}" for k in ("ndcg_vs_chronological_diff", "ndcg_vs_score_diff",
                                                                          "p_vs_chronological_diff", "p_vs_score_diff")]
                                 + [_yes(r["confirmed"])] for r in hyp["H5"]],
                                columns=["Wariant", "Detektor", "K", "ΔNDCG vs chrono", "ΔNDCG vs score", "ΔP vs chrono",
                                         "ΔP vs score", "Potwierdzona"])), ""]

    # Wydajność.
    p = perf.drop(columns=[c for c in perf.columns if perf[c].isna().all()])
    out += ["## Wydajność systemu", "", _table(p.round(2)), "",
            "Liczba porównań sparowanych: zob. `comparisons.csv` (bez poprawki na wielokrotne testowanie, D21)."]
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")
