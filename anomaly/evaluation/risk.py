"""
Faza 7: RiskScore (METODOLOGIA D10, D21). Składniki wyłącznie z obserwowalnych danych okna:

    RiskScore = 0,60 · norm_anomaly_score + 0,25 · severity_weight + 0,15 · context_weight

- severity_weight: max po operacjach okna z wrażliwości tabeli docelowej × (1,0 zapis / 0,7 odczyt),
- context_weight: średnia flag: praca po godzinach (after_hours_flag z wariantu per użytkownik),
  urządzenie inne niż podstawowe (najczęstsze w TRAIN użytkownika), IP niewidziane w TRAIN użytkownika
  (1 − ip_known_flag z wariantu per użytkownik).
Składniki nie zależą od wariantu ani detektora; zmienia się tylko norm_anomaly_score.
"""
from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from anomaly.domain import SELECT, TABLE_SENSITIVITY
from anomaly.features.windowing import load_events, load_users
from anomaly.paths import windows_csv

WEIGHTS = {"anomaly": 0.60, "severity": 0.25, "context": 0.15}
READ_FACTOR, WRITE_FACTOR = 0.7, 1.0
KEYS = ["UserId", "WindowStart"]


def primary_devices(train_events: pd.DataFrame) -> Dict[str, str]:
    """Urządzenie podstawowe użytkownika: typ z największą liczbą operacji w TRAIN (remis: alfabetycznie)."""
    counts = train_events.groupby(["UserId", "device"]).size().rename("n").reset_index()
    counts = counts.sort_values(["UserId", "n", "device"], ascending=[True, False, True])
    return counts.drop_duplicates("UserId").set_index("UserId")["device"].to_dict()


def risk_components(events: pd.DataFrame, user_windows: pd.DataFrame, primary: Dict[str, str]) -> pd.DataFrame:
    """Składniki RiskScore per okno (UserId, WindowStart) z jego zdarzeń i cech wariantu per użytkownik."""
    e = events[KEYS + ["target_table", "operation_type", "device"]].copy()
    unknown = sorted(set(e["target_table"]) - set(TABLE_SENSITIVITY))
    if unknown:
        raise ValueError(f"Tabele bez wrażliwości D10: {unknown}")
    factor = np.where(e["operation_type"] == SELECT, READ_FACTOR, WRITE_FACTOR)
    e["sev"] = e["target_table"].map(TABLE_SENSITIVITY).to_numpy() * factor
    e["non_primary"] = (e["device"] != e["UserId"].map(primary)).astype(int)
    comp = e.groupby(KEYS).agg(severity_weight=("sev", "max"), ctx_non_primary_device=("non_primary", "max"))

    w = user_windows.set_index(KEYS)[["after_hours_flag", "ip_known_flag"]]
    comp = comp.join(w, how="left")
    if comp[["after_hours_flag", "ip_known_flag"]].isna().any().any():
        raise ValueError("Okno bez cech wariantu per użytkownik (niezgodne pliki okien i zdarzeń).")
    comp["ctx_after_hours"] = comp.pop("after_hours_flag").astype(int)
    comp["ctx_unknown_ip"] = 1 - comp.pop("ip_known_flag").astype(int)
    comp["context_weight"] = comp[["ctx_after_hours", "ctx_non_primary_device", "ctx_unknown_ip"]].mean(axis=1)
    return comp.reset_index()


def load_risk_components(split: str, seed: int) -> pd.DataFrame:
    users = load_users(seed)
    primary = primary_devices(load_events("train", seed, users))
    events = load_events(split, seed, users)
    user_windows = pd.read_csv(windows_csv("user", split, seed), parse_dates=["WindowStart"])
    return risk_components(events, user_windows, primary)


def risk_score(norm: np.ndarray, severity_weight: np.ndarray, context_weight: np.ndarray) -> np.ndarray:
    return (WEIGHTS["anomaly"] * np.asarray(norm) + WEIGHTS["severity"] * np.asarray(severity_weight)
            + WEIGHTS["context"] * np.asarray(context_weight))
