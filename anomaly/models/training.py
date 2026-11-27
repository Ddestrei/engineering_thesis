"""
Faza 6: uczenie detektorów, strojenie na VAL i zamrożenie konfiguracji przed TEST (METODOLOGIA D7).

Dla każdego wariantu modelowania (D17) i detektora:
  1. model dopasowany na oknach TRAIN każdej jednostki (global: 1, rola: 6, użytkownik: 30);
     użytkownik z < 30 oknami TRAIN używa okien swojej roli (D12),
  2. score'y VAL znormalizowane per jednostka na normalnych oknach VAL (D8),
  3. hiperparametry (IF, OCSVM, LOF) wybierane według PR-AUC na połączonych znormalizowanych score'ach
     VAL wariantu; jeden zestaw na wariant, wspólny dla wszystkich jego jednostek,
  4. progi (D7) na znormalizowanych score'ach VAL: global – jeden, per rola – per rola,
     per użytkownik – wspólny dla roli; główny punkt pracy (FPR <= 5%, max MCC) i recall = 0,80 (H1).

Moduł nie czyta plików TEST. Wynik (results/seed_<N>/detectors/<konfiguracja>/):
    frozen_config.json (+ .sha256)   hiperparametry, parametry dopasowania, normalizacja, progi, metryki VAL
    models/<wariant>_<detektor>.joblib  dopasowane modele per jednostka (do scoringu TEST w Fazie 7)
    val_scores_<wariant>.csv            score surowy, znormalizowany i alarmy na VAL

Uruchomienie:  python -m anomaly.models.training --seed 11 22 33 44 55
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from typing import Dict, Iterable, List, Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from anomaly.features.windowing import (FEATURE_GROUPS, ID_COLUMNS, LABEL_COLUMNS, MIN_USER_WINDOWS, VARIANTS,
                                        feature_columns)
from anomaly.models.detectors import DETECTORS, Detector, hyperparameter_grid, make_detector
from anomaly.models.scoring import (MAX_FPR, TARGET_RECALL, ScoreNormalizer, metrics_at, select_main_threshold,
                                    select_recall_threshold)
from anomaly.paths import detectors_dir, frozen_config_json, models_joblib, val_scores_csv, windows_csv
from anomaly.versioning import git_version, sha256_file

UNIT_COLUMN = {"global": None, "role": "RoleId", "user": "UserId"}


def units_of(df: pd.DataFrame, variant: str) -> pd.Series:
    col = UNIT_COLUMN[variant]
    return pd.Series("ALL", index=df.index) if col is None else df[col]


def threshold_groups(df: pd.DataFrame, variant: str) -> pd.Series:
    """D7: próg globalny (global), per rola (rola) albo wspólny dla roli (użytkownik)."""
    return pd.Series("ALL", index=df.index) if variant == "global" else df["RoleId"]


def load_windows(variant: str, split: str, seed: int) -> pd.DataFrame:
    if split == "test":
        raise ValueError("Faza 6 nie czyta TEST: konfiguracja musi być zamrożona przed TEST (D7).")
    return pd.read_csv(windows_csv(variant, split, seed), keep_default_na=False, parse_dates=["WindowStart"])


# --- dopasowanie i scoring per jednostka ----------------------------------------------------

def fit_units(detector: str, hp: dict, train: pd.DataFrame, features: List[str], variant: str) -> Dict[str, Detector]:
    units = units_of(train, variant)
    models: Dict[str, Detector] = {}
    for unit in sorted(units.unique()):
        rows = train[units == unit]
        if variant == "user" and len(rows) < MIN_USER_WINDOWS:          # D12: statystyki roli
            rows = train[train["RoleId"] == rows["RoleId"].iloc[0]]
        models[unit] = make_detector(detector, hp).fit(rows[features].to_numpy(float), features)
    return models


def score_units(models: Dict[str, Detector], df: pd.DataFrame, features: List[str], variant: str) -> np.ndarray:
    units = units_of(df, variant)
    missing = sorted(set(units) - set(models))
    if missing:
        raise ValueError(f"Brak modelu dla jednostek {missing[:5]} (wariant {variant}).")
    out = np.empty(len(df))
    for unit, idx in df.groupby(units).indices.items():
        out[idx] = models[unit].score(df.iloc[idx][features].to_numpy(float))
    return out


def fit_normalizers(raw: np.ndarray, val: pd.DataFrame, variant: str) -> Dict[str, ScoreNormalizer]:
    """D8: normalizacja na normalnych oknach VAL jednostki modelowania."""
    units = units_of(val, variant).to_numpy()
    normal = val["IsAnomaly"].to_numpy() == 0
    return {u: ScoreNormalizer.fit(raw[(units == u) & normal]) for u in np.unique(units)}


def normalize(raw: np.ndarray, df: pd.DataFrame, normalizers: Dict[str, ScoreNormalizer], variant: str,
              excess: bool = False) -> np.ndarray:
    """Score znormalizowany (D8) albo, przy excess=True, nadwyżka ogona do rozstrzygania remisów."""
    units = units_of(df, variant).to_numpy()
    out = np.empty(len(df))
    for u in np.unique(units):
        mask = units == u
        out[mask] = (normalizers[u].excess if excess else normalizers[u].transform)(raw[mask])
    return out


def select_thresholds(norm: np.ndarray, val: pd.DataFrame, variant: str) -> Dict[str, dict]:
    groups = threshold_groups(val, variant).to_numpy()
    y = val["IsAnomaly"].to_numpy()
    return {g: {"main": select_main_threshold(y[groups == g], norm[groups == g]),
                "recall80": select_recall_threshold(y[groups == g], norm[groups == g])}
            for g in np.unique(groups)}


def apply_thresholds(norm: np.ndarray, df: pd.DataFrame, thresholds: Dict[str, dict], variant: str,
                     point: str) -> np.ndarray:
    groups = threshold_groups(df, variant)
    return norm >= groups.map({g: t[point]["threshold"] for g, t in thresholds.items()}).to_numpy(float)


# --- jeden detektor w jednym wariancie ------------------------------------------------------

def tune_detector(detector: str, variant: str, train: pd.DataFrame, val: pd.DataFrame, features: List[str],
                  seed: int) -> dict:
    y = val["IsAnomaly"].to_numpy()
    grid_results = []
    best = None
    for hp in hyperparameter_grid(detector, seed):
        t0 = time.perf_counter()
        models = fit_units(detector, hp, train, features, variant)
        raw = score_units(models, val, features, variant)
        normalizers = fit_normalizers(raw, val, variant)
        norm = normalize(raw, val, normalizers, variant)
        pr_auc = float(average_precision_score(y, norm))
        grid_results.append({"hyperparameters": hp, "val_pr_auc": round(pr_auc, 6),
                             "fit_score_sec": round(time.perf_counter() - t0, 3)})
        if best is None or pr_auc > best["pr_auc"]:                 # remis: pierwsza konfiguracja z siatki
            best = {"hp": hp, "pr_auc": pr_auc, "models": models, "raw": raw, "norm": norm,
                    "normalizers": normalizers}

    thresholds = select_thresholds(best["norm"], val, variant)
    alarms = {p: apply_thresholds(best["norm"], val, thresholds, variant, p) for p in ("main", "recall80")}
    val_metrics = {p: metrics_at(y, alarms[p].astype(float), 0.5) for p in alarms}
    for m in val_metrics.values():
        m.pop("threshold")
    val_metrics["pr_auc"] = round(best["pr_auc"], 6)
    return {
        "detector": detector, "variant": variant, "hyperparameters": best["hp"], "grid": grid_results,
        "units": {u: {"fit": m.params(), "normalizer": best["normalizers"][u].to_dict()}
                  for u, m in best["models"].items()},
        "thresholds": thresholds, "val_metrics": val_metrics,
        "_models": best["models"], "_raw": best["raw"], "_norm": best["norm"], "_alarms": alarms,
        "_excess": normalize(best["raw"], val, best["normalizers"], variant, excess=True),
    }


# --- przebieg dla ziarna ----------------------------------------------------------------

def train_seed(seed: int, groups: Optional[Iterable[str]] = None, config: str = "full",
               variants: Iterable[str] = VARIANTS, detectors: Iterable[str] = DETECTORS,
               verbose: bool = True) -> dict:
    groups = list(FEATURE_GROUPS) if groups is None else list(groups)
    out_dir = detectors_dir(seed, config)
    (out_dir / "models").mkdir(parents=True, exist_ok=True)
    frozen = {"seed": seed, "config": config, "feature_groups": groups,
              "created": datetime.now().isoformat(timespec="seconds"), "code_version": git_version(),
              "rules": {"max_fpr": MAX_FPR, "target_recall": TARGET_RECALL, "hp_selection": "val_pr_auc",
                        "min_user_windows": MIN_USER_WINDOWS},
              "results": {}}
    t_all = time.perf_counter()
    for variant in variants:
        train, val = load_windows(variant, "train", seed), load_windows(variant, "validation", seed)
        features = feature_columns(variant, groups)
        scores = val[ID_COLUMNS + LABEL_COLUMNS].copy()
        frozen["results"][variant] = {"features": features}
        for detector in detectors:
            t0 = time.perf_counter()
            res = tune_detector(detector, variant, train, val, features, seed)
            joblib.dump(res.pop("_models"), models_joblib(variant, detector, seed, config), compress=3)
            scores[f"{detector}_raw"] = res.pop("_raw")
            scores[f"{detector}_norm"] = res.pop("_norm")
            scores[f"{detector}_excess"] = res.pop("_excess")
            for point, alarm in res.pop("_alarms").items():
                scores[f"{detector}_alarm_{point}"] = alarm.astype(int)
            res["sec"] = round(time.perf_counter() - t0, 2)
            frozen["results"][variant][detector] = res
            if verbose:
                m = res["val_metrics"]
                print(f"  {variant:6s} {detector:8s} PR-AUC {m['pr_auc']:.3f} | główny próg: FPR {m['main']['fpr']:.3f} "
                      f"recall {m['main']['recall']:.3f} MCC {m['main']['mcc']:.3f} | recall 0,8: FPR "
                      f"{m['recall80']['fpr']:.3f} | {res['hyperparameters'] or '-'} ({res['sec']} s)")
        scores.to_csv(val_scores_csv(variant, seed, config), index=False)

    frozen["total_sec"] = round(time.perf_counter() - t_all, 2)
    path = frozen_config_json(seed, config)
    path.write_text(json.dumps(frozen, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    path.with_suffix(".sha256").write_text(sha256_file(path) + "\n", encoding="utf-8")
    if verbose:
        print(f"Zamrożona konfiguracja: {path} ({frozen['total_sec']} s)")
    return frozen


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Faza 6: detektory, strojenie na VAL, progi D7 (bez TEST).")
    parser.add_argument("--seed", type=int, nargs="+", required=True)
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument("--detectors", nargs="+", choices=DETECTORS, default=list(DETECTORS))
    args = parser.parse_args(argv)
    for seed in args.seed:
        print(f"\n=== Detektory: ziarno {seed} (VAL) ===")
        train_seed(seed, variants=args.variants, detectors=args.detectors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
