"""
Budowa kompletnego zbioru dla jednego ziarna (METODOLOGIA D4–D6, D15–D19).

    TRAIN      2 miesiące, wyłącznie norma (z trudnymi przypadkami legalnymi)
    VALIDATION 1 miesiąc, norma + incydenty wariantu walidacyjnego
    TEST       1 miesiąc, norma o zaburzonych parametrach + incydenty wariantu testowego

Z ziarna głównego wyprowadzane są niezależne pod-ziarna: profile użytkowników, TRAIN, VAL, TEST (D6).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from anomaly.generator.incidents import inject_incidents
from anomaly.generator.normal_activity import generate_normal
from anomaly.generator.records import assign_records
from anomaly.generator.sampling import add_months, month_starts
from anomaly.generator.users import USERS_PER_ROLE, SimUser, make_users, perturb_for_test

DEFAULT_START = date(2026, 1, 1)
# split -> (przesunięcie w miesiącach od startu, liczba miesięcy)
SPLIT_PERIODS = {"train": (0, 2), "validation": (2, 1), "test": (3, 1)}

EVENT_COLUMNS = [
    "event_uid", "run_id", "timestamp", "session_id", "user", "role", "login_name",
    "client_hostname", "simulated_ip", "device_context", "operation_type", "target_table",
    "record_key", "parent_keys",
    "incident_id", "is_anomaly", "anomaly_class", "intensity", "severity_level", "hard_normal",
]
INCIDENT_COLUMNS = ["incident_id", "run_id", "anomaly_class", "intensity", "user", "role", "login_name",
                    "start", "end", "severity_level", "n_events", "tables", "operations"]


@dataclass
class SplitData:
    events: pd.DataFrame
    incidents: pd.DataFrame
    seeds: pd.DataFrame


def make_run_id(split: str, seed: int) -> str:
    """Identyfikator przebiegu, np. 'train-s11'. Prefiks każdego EventUid."""
    return f"{split}-s{seed}"


def _to_frame(events: List[dict], run_id: str) -> pd.DataFrame:
    df = pd.DataFrame(events)
    df = df.sort_values(["timestamp", "user"], kind="stable").reset_index(drop=True)
    df.insert(0, "run_id", run_id)
    df.insert(0, "event_uid", [f"{run_id}-{i:06d}" for i in range(len(df))])
    if "severity_level" not in df:
        df["severity_level"] = None
    df["incident_id"] = df["incident_id"].replace("", pd.NA)
    df["is_anomaly"] = df["is_anomaly"].astype(int)
    df["severity_level"] = pd.to_numeric(df["severity_level"]).astype("Int64")
    return df


def incidents_table(events: pd.DataFrame) -> pd.DataFrame:
    anomalous = events[events["is_anomaly"] == 1]
    if anomalous.empty:
        return pd.DataFrame(columns=INCIDENT_COLUMNS)
    g = anomalous.groupby("incident_id", sort=False)
    table = g.agg(
        run_id=("run_id", "first"), anomaly_class=("anomaly_class", "first"), intensity=("intensity", "first"),
        user=("user", "first"), role=("role", "first"), login_name=("login_name", "first"),
        start=("timestamp", "min"), end=("timestamp", "max"), severity_level=("severity_level", "first"),
        n_events=("event_uid", "size"),
        tables=("target_table", lambda s: ";".join(sorted(set(s)))),
        operations=("operation_type", lambda s: ";".join(sorted(set(s)))),
    ).reset_index()
    return table.sort_values("start", kind="stable")[INCIDENT_COLUMNS].reset_index(drop=True)


def build_split(split: str, seed: int, users: List[SimUser], rng: np.random.Generator,
                start: date = DEFAULT_START) -> SplitData:
    run_id = make_run_id(split, seed)
    offset, n_months = SPLIT_PERIODS[split]
    s_start, s_end = add_months(start, offset), add_months(start, offset + n_months)
    split_users = [perturb_for_test(u, rng) for u in users] if split == "test" else users

    events: List[dict] = []
    for user in split_users:
        events += generate_normal(rng, user, s_start, s_end)
    if split != "train":
        for m_start in month_starts(s_start, s_end):
            events += inject_incidents(rng, split_users, m_start, split, run_id)

    df = _to_frame(events, run_id)
    df, seeds = assign_records(df, rng, run_id)
    return SplitData(events=df[EVENT_COLUMNS], incidents=incidents_table(df), seeds=seeds)


def build_dataset(seed: int, start: date = DEFAULT_START, users_per_role: int = USERS_PER_ROLE
                  ) -> Tuple[List[SimUser], Dict[str, SplitData]]:
    s_users, s_train, s_val, s_test = np.random.SeedSequence(seed).spawn(4)
    users = make_users(np.random.default_rng(s_users), users_per_role)
    splits = {}
    for split, sub in (("train", s_train), ("validation", s_val), ("test", s_test)):
        splits[split] = build_split(split, seed, users, np.random.default_rng(sub), start)
    return users, splits


def users_table(users: List[SimUser]) -> pd.DataFrame:
    rows = []
    for u in users:
        rows.append({
            "user_id": u.user_id, "role": u.role, "login_name": u.login_name,
            "volume_factor": round(u.volume_factor, 3),
            "rare_tables": ";".join(u.rare_tables),
            "devices": ";".join(f"{d.name}:{d.weight:.2f}@{d.hour_low:.1f}-{d.hour_high:.1f}" for d in u.devices),
            "device_meta": ";".join(f"{n}={m.hostname}/{m.ip}" for n, m in u.device_meta.items()),
        })
    return pd.DataFrame(rows)
