"""
Faza 7: score'y splitu z zamrożonej konfiguracji Fazy 6 (METODOLOGIA D7, D21).

Używa wyłącznie results/seed_<N>/detectors/<konfiguracja>/frozen_config.json (po sprawdzeniu SHA-256)
i modeli .joblib. Nic nie jest dopasowywane na ocenianym splicie: normalizacja (D8) i progi (D7)
pochodzą z VAL. Ten sam kod uruchomiony na VAL musi odtworzyć metryki VAL z Fazy 6 (kontrola spójności).
"""
from __future__ import annotations

import json
import time
from typing import Dict, Tuple

import joblib
import pandas as pd

from anomaly.features.windowing import ID_COLUMNS, LABEL_COLUMNS
from anomaly.models.scoring import ScoreNormalizer
from anomaly.models.training import apply_thresholds, normalize, score_units
from anomaly.paths import frozen_config_json, models_joblib, windows_csv
from anomaly.versioning import sha256_file


class FrozenConfigError(RuntimeError):
    pass


def load_frozen(seed: int, config: str) -> Tuple[dict, str]:
    path = frozen_config_json(seed, config)
    if not path.exists():
        raise FrozenConfigError(f"Brak zamrożonej konfiguracji {path} (najpierw Faza 6 / ablacja).")
    sha_path = path.with_suffix(".sha256")
    actual = sha256_file(path)
    if not sha_path.exists() or sha_path.read_text().strip() != actual:
        raise FrozenConfigError(f"Suma SHA-256 {path.name} się nie zgadza: plik zmieniony po zamrożeniu.")
    return json.loads(path.read_text(encoding="utf-8")), actual


def _normalizers(units: dict) -> Dict[str, ScoreNormalizer]:
    out = {}
    for unit, u in units.items():
        n = u["normalizer"]
        out[unit] = ScoreNormalizer(reference=n["reference"], s_max=n["s_max"], sigma=n["sigma"],
                                    sigma_level=n["sigma_level"])
    return out


def score_split(seed: int, config: str, variant: str, split: str, frozen: dict) -> Tuple[pd.DataFrame, dict]:
    """Score'y wszystkich detektorów wariantu na splicie. Zwraca tabelę okien i czasy scoringu [s]."""
    windows = pd.read_csv(windows_csv(variant, split, seed), keep_default_na=False, parse_dates=["WindowStart"])
    res_v = frozen["results"][variant]
    features = res_v["features"]
    out = windows[ID_COLUMNS + LABEL_COLUMNS].copy()
    timings = {}
    for detector, res in res_v.items():
        if detector == "features":
            continue
        models = joblib.load(models_joblib(variant, detector, seed, config))
        t0 = time.perf_counter()
        raw = score_units(models, windows, features, variant)
        timings[detector] = time.perf_counter() - t0
        normalizers = _normalizers(res["units"])
        norm = normalize(raw, windows, normalizers, variant)
        out[f"{detector}_raw"] = raw
        out[f"{detector}_norm"] = norm
        out[f"{detector}_excess"] = normalize(raw, windows, normalizers, variant, excess=True)
        for point in ("main", "recall80"):
            out[f"{detector}_alarm_{point}"] = apply_thresholds(norm, windows, res["thresholds"], variant,
                                                                point).astype(int)
    return out, timings


def detectors_of(frozen: dict, variant: str):
    return [d for d in frozen["results"][variant] if d != "features"]


def check_reproduces_val(scores: pd.DataFrame, frozen: dict, variant: str) -> list:
    """Kontrola spójności: scoring VAL z plików musi dać te same alarmy co Faza 6."""
    problems = []
    y = scores["IsAnomaly"].to_numpy()
    for det in detectors_of(frozen, variant):
        ref = frozen["results"][variant][det]["val_metrics"]
        for point in ("main", "recall80"):
            a = scores[f"{det}_alarm_{point}"].to_numpy() == 1
            tp, fp = int((a & (y == 1)).sum()), int((a & (y == 0)).sum())
            if (tp, fp) != (ref[point]["tp"], ref[point]["fp"]):
                problems.append(f"{variant}/{det}/{point}: TP/FP {tp}/{fp} != Faza 6 {ref[point]['tp']}/{ref[point]['fp']}")
    return problems
