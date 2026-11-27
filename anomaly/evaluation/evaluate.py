"""
Faza 7: ewaluacja z zamrożonych konfiguracji (METODOLOGIA D21).

    python -m anomaly.evaluation.evaluate --split validation --seed 11 22 33 44 55   # kontrola łańcucha
    python -m anomaly.evaluation.evaluate --split test --seed 11 22 33 44 55         # wyniki pracy

Dla każdego ziarna i każdej konfiguracji z frozen_config.json (full + ablacja):
score'y z modeli .joblib -> RiskScore -> metryki per okno / podzbiór / incydent / błędy / ranking.
Na VAL kod sprawdza dodatkowo, że alarmy odtwarzają metryki VAL z Fazy 6.
Wynik: results/seed_<N>/evaluation/<split>/ (score'y, metrics.csv, incidents.csv, manifest.json)
i results/summary/<split>/ (średnie i 95% CI, porównania, hipotezy, wydajność).
TEST wymaga czystego drzewa gita (wynik z jednoznacznym hashem commita), chyba że --allow-dirty.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from typing import List, Optional

import pandas as pd

from anomaly.evaluation.ablation import ABLATION_CONFIGS, SINGLE_H2, TRIPLE_H2
from anomaly.evaluation.metrics import add_risk, detector_metrics
from anomaly.evaluation.report import write_report
from anomaly.evaluation.risk import load_risk_components
from anomaly.evaluation.score_frozen import (check_reproduces_val, detectors_of, load_frozen, score_split)
from anomaly.evaluation.stats import evaluate_hypotheses, summarize
from anomaly.features import windowing
from anomaly.paths import (evaluation_dir, evaluation_root, frozen_config_json, incidents_csv, run_summary_json,
                           scores_csv, summary_dir)
from anomaly.versioning import git_version

KEYS = ["UserId", "WindowStart"]


def available_configs(seed: int) -> List[str]:
    return [c for c in ABLATION_CONFIGS if frozen_config_json(seed, c).exists()]


def feature_build_seconds(seed: int, split: str) -> float:
    """Czas budowy okien i cech splitu (3 warianty) bez zapisu plików: wydajność systemu (Faza 7)."""
    users = windowing.load_users(seed)
    t0 = time.perf_counter()
    train_ev = windowing.load_events("train", seed, users)
    ev = train_ev if split == "train" else windowing.load_events(split, seed, users)
    train_w, w = windowing.base_windows(train_ev), windowing.base_windows(ev)
    for variant in windowing.VARIANTS:
        refs = windowing.fit_reference(train_ev, train_w, variant)
        windowing.relative_features(ev, w, refs, variant)
    return time.perf_counter() - t0


def evaluate_seed(seed: int, split: str, configs: Optional[List[str]] = None, verbose: bool = True) -> dict:
    configs = configs or available_configs(seed)
    if "full" not in configs:
        raise FileNotFoundError(f"Brak konfiguracji 'full' dla ziarna {seed} (Faza 6).")
    incidents = pd.read_csv(incidents_csv(split, seed), keep_default_na=False)
    risk = load_risk_components(split, seed)
    long_rows, incident_tables, problems = [], [], []
    manifest = {"seed": seed, "split": split, "evaluated": datetime.now().isoformat(timespec="seconds"),
                "code_version": git_version(), "configs": {}, "scoring_us_per_window": {}}
    for config in configs:
        frozen, sha = load_frozen(seed, config)
        manifest["configs"][config] = {"frozen_sha256": sha, "frozen_created": frozen["created"],
                                       "frozen_code_version": frozen["code_version"]}
        evaluation_dir(seed, split, config).mkdir(parents=True, exist_ok=True)
        for variant in frozen["results"]:
            scores, timings = score_split(seed, config, variant, split, frozen)
            if split == "validation":
                problems += [f"{config}/{p}" for p in check_reproduces_val(scores, frozen, variant)]
            scores = scores.merge(risk, on=KEYS, how="left", validate="one_to_one")
            if scores["severity_weight"].isna().any():
                raise ValueError("Okno bez składników RiskScore (niezgodne pliki okien).")
            for det in detectors_of(frozen, variant):
                add_risk(scores, det)
                rows, inc = detector_metrics(scores, det, incidents)
                long_rows += [{"seed": seed, "split": split, "config": config, "variant": variant, "detector": det, **r}
                              for r in rows]
                if config == "full":
                    incident_tables.append(inc.assign(seed=seed, variant=variant, detector=det))
                    manifest["scoring_us_per_window"][f"{variant}/{det}"] = round(1e6 * timings[det] / len(scores), 2)
            scores.to_csv(scores_csv(variant, split, seed, config), index=False)

    out = evaluation_root(seed, split)
    pd.DataFrame(long_rows).to_csv(out / "metrics.csv", index=False)
    pd.concat(incident_tables).to_csv(out / "incidents_full.csv", index=False)
    manifest["consistency_problems"] = problems
    manifest["performance"] = performance(seed, split)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if verbose:
        status = "OK" if not problems else f"{len(problems)} NIEZGODNOŚCI"
        print(f"ziarno {seed} ({split}): {len(configs)} konfiguracji, {len(long_rows)} wierszy metryk"
              + (f", odtworzenie metryk VAL z Fazy 6: {status}" if split == "validation" else ""))
        for p in problems[:10]:
            print("   -", p)
    return manifest


def performance(seed: int, split: str) -> dict:
    perf = {"feature_build_sec": round(feature_build_seconds(seed, split), 3)}
    path = run_summary_json(seed)
    if path.exists():
        run = json.loads(path.read_text(encoding="utf-8"))["runs"].get(split, {})
        ex = run.get("execution", {})
        perf.update({"events": ex.get("executed"), "execution_sec": ex.get("duration_sec"),
                     "queries_per_sec": ex.get("queries_per_sec"), "logger_sec": run.get("logger_sec"),
                     "join_sec": run.get("join_sec")})
        if ex.get("executed") and run.get("logger_sec"):
            perf["logger_events_per_sec"] = round(ex["executed"] / run["logger_sec"], 1)
    frozen = json.loads(frozen_config_json(seed).read_text(encoding="utf-8"))
    perf["training_sec_full"] = frozen.get("total_sec")
    return perf


def summarize_split(split: str, seeds: List[str], verbose: bool = True) -> dict:
    frames, manifests = [], []
    for seed in seeds:
        base = evaluation_root(seed, split)
        frames.append(pd.read_csv(base / "metrics.csv"))
        manifests.append(json.loads((base / "manifest.json").read_text(encoding="utf-8")))
    long = pd.concat(frames, ignore_index=True)
    out = summary_dir(split)
    out.mkdir(parents=True, exist_ok=True)
    summary = summarize(long)
    summary.to_csv(out / "metrics_summary.csv", index=False)
    detectors = sorted(long["detector"].unique())
    variants = [v for v in windowing.VARIANTS if v in set(long["variant"])]
    hyp = evaluate_hypotheses(long, detectors, variants, singles=SINGLE_H2, triple=TRIPLE_H2)
    pd.DataFrame(hyp.pop("comparisons")).to_csv(out / "comparisons.csv", index=False)
    (out / "hypotheses.json").write_text(json.dumps(hyp, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    perf = pd.DataFrame([{"seed": m["seed"], **m["performance"]} for m in manifests])
    perf.to_csv(out / "performance.csv", index=False)
    scoring = pd.DataFrame([{"seed": m["seed"], "variant": k.split("/")[0], "detector": k.split("/")[1], "us_per_window": us}
                            for m in manifests for k, us in m.get("scoring_us_per_window", {}).items()])
    scoring.to_csv(out / "scoring_time.csv", index=False)
    meta ={"split": split, "seeds": seeds, "created": datetime.now().isoformat(timespec="seconds"),
            "code_version": git_version(),
            "per_seed": [{"seed": m["seed"], "code_version": m["code_version"],
                          "frozen": {c: v["frozen_sha256"] for c, v in m["configs"].items()},
                          "consistency_problems": len(m["consistency_problems"])} for m in manifests]}
    (out / "manifest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    write_report(out / "RAPORT.md", split, summary, hyp, perf, meta, scoring)
    if verbose:
        print(f"Podsumowanie {split}: {out}")
    return hyp


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Faza 7: ewaluacja z zamrożonych konfiguracji (D21).")
    parser.add_argument("--split", choices=["validation", "test"], required=True)
    parser.add_argument("--seed", type=int, nargs="+", required=True)
    parser.add_argument("--configs", nargs="+", choices=list(ABLATION_CONFIGS))
    parser.add_argument("--allow-dirty", action="store_true", help="pozwól ocenić TEST na niezacommitowanym kodzie")
    parser.add_argument("--summary-only", action="store_true",
                        help="tylko odtwórz results/summary/<split>/ z istniejących metryk per ziarno (bez scoringu)")
    args = parser.parse_args(argv)
    if args.summary_only:
        summarize_split(args.split, args.seed)
        return 0
    if args.split == "test" and git_version()["dirty"] and not args.allow_dirty:
        print("Drzewo gita ma niezacommitowane zmiany: wyniki TEST nie miałyby jednoznacznej wersji kodu. "
              "Zrób commit albo użyj --allow-dirty.")
        return 2
    t0 = time.perf_counter()
    manifests = [evaluate_seed(seed, args.split, args.configs) for seed in args.seed]
    summarize_split(args.split, args.seed)
    print(f"Czas: {time.perf_counter() - t0:.0f} s")
    return 1 if any(m["consistency_problems"] for m in manifests) else 0


if __name__ == "__main__":
    raise SystemExit(main())
