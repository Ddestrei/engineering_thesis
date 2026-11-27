"""
Faza 5: okna UserId × 30 min i cechy według METODOLOGIA D14.

Wejście: data/seed_<N>/joined/joined_<split>.csv (zdarzenia XE połączone z ground truth, Faza 4).
Wyjście: data/seed_<N>/features/
    windows_<wariant>_<split>.csv   jedna tabela okien na wariant modelowania (D17) i split
    reference_<wariant>.json        statystyki normy z TRAIN, z których policzono cechy względne
    features_report.json            kontrole poprawności i podsumowanie

Zasady:
- Okno (D1, D11): para UserId + 30-minutowe okno stałe wyrównane do pełnych półgodzin, przedział
  lewostronnie domknięty, czas symulowany. Tylko okna z >= 1 operacją.
- Cechy pochodzą wyłącznie z faktów obserwowalnych w logu XE (login, urządzenie, IP, tekst SQL,
  tabela docelowa, metryki wykonania) i czasu symulowanego. Kolumny ground truth trafiają tylko
  do kolumn etykiet (LABEL_COLUMNS), których detektory nie czytają.
- Cechy „rzadkości” i „względem normy” (hour_rarity, after_hours_flag, volume_ratio_to_median,
  rare_resource_score, new_table_flag, ip_known_flag) liczone są WYŁĄCZNIE ze statystyk TRAIN,
  osobno dla jednostki modelowania wariantu (D14, D17): populacja, rola albo użytkownik.
- Cechy błędów (IsError, ErrorNumber) nie istnieją (D3): wszystkie operacje kończą się wynikiem OK.

Uruchomienie:  python -m anomaly.features.windowing --seed 11 22 33 44 55
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from anomaly.domain import OPERATIONS, ROLES, TABLE_SENSITIVITY
from anomaly.paths import (SPLITS, events_csv, features_dir, features_report_json, incidents_csv,
                           joined_csv, reference_json, users_csv, windows_csv)

WINDOW = "30min"
VARIANTS = ("global", "role", "user")
DEVICE_TYPES = ("Office_PC", "Home_PC", "Mobile_Laptop", "Private_PC")
TABLES = tuple(sorted(TABLE_SENSITIVITY))

RARE_TABLE_SHARE = 0.05          # tabela rzadka: udział < 5% operacji jednostki w TRAIN
AFTER_HOURS_PERCENTILES = (1, 99)  # typowe godziny pracy jednostki: [P1, P99] godzin operacji w TRAIN
MIN_USER_WINDOWS = 30            # D12: użytkownik z mniejszą liczbą okien TRAIN używa statystyk roli

ID_COLUMNS = ["UserId", "RoleId", "WindowStart"]
LABEL_COLUMNS = ["IsAnomaly", "IncidentIds", "AnomalyClasses", "Intensities", "Severity", "HardNormal"]

FEATURE_GROUPS: Dict[str, List[str]] = {
    "time": ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "is_weekend", "after_hours_flag", "hour_rarity"],
    "volume": ["operation_count", "session_count", "ops_per_session", "volume_ratio_to_median"],
    "context": [f"device_{d}" for d in DEVICE_TYPES] + ["ip_known_flag"],
    "ratios": [f"{op.lower()}_ratio" for op in OPERATIONS],
    "resources": ["unique_tables", "rare_resource_score", "new_table_flag"],
    "execution": ["sum_logical_reads", "sum_rows", "mean_duration", "avg_join_count", "avg_sql_length"],
}
ROLE_COLUMNS = [f"role_{r}" for r in ROLES]          # grupa Kontekst, tylko wariant global (D14)
# Cechy binarne (0/1); baseline robust (D12) liczy z-score tylko dla cech ciągłych.
BINARY_FEATURES = ["is_weekend", "after_hours_flag", "new_table_flag", "ip_known_flag"] + ROLE_COLUMNS
GROUP_LABELS = {"time": "Czas", "volume": "Wolumen", "context": "Kontekst",
                "ratios": "Proporcje operacji", "resources": "Zasoby", "execution": "Wykonanie"}


def feature_columns(variant: str, groups: Optional[Iterable[str]] = None) -> List[str]:
    """Kolumny cech wariantu, opcjonalnie tylko z wybranych grup D14 (ablacja)."""
    if variant not in VARIANTS:
        raise ValueError(f"Nieznany wariant: {variant}")
    groups = list(FEATURE_GROUPS) if groups is None else list(groups)
    cols: List[str] = []
    for g in groups:
        cols += FEATURE_GROUPS[g]
        if g == "context" and variant == "global":
            cols += ROLE_COLUMNS
    return cols


def unit_column(variant: str) -> str:
    return {"global": "_unit_global", "role": "RoleId", "user": "UserId"}[variant]


# --- wczytanie zdarzeń ----------------------------------------------------------------

def load_users(seed: int) -> pd.DataFrame:
    users = pd.read_csv(users_csv(seed), keep_default_na=False)
    return users[["user_id", "role", "login_name"]]


def load_events(split: str, seed: int, users: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Zdarzenia jednego splitu z plików ziarna (joined CSV + intensywność i tagi trudnych przypadków)."""
    users = load_users(seed) if users is None else users
    joined = pd.read_csv(joined_csv(split, seed), low_memory=False)
    gt = pd.read_csv(events_csv(split, seed), keep_default_na=False,
                     usecols=["event_uid", "intensity", "hard_normal"])
    return prepare_events(joined, gt, users)


def prepare_events(joined: pd.DataFrame, gt: pd.DataFrame, users: pd.DataFrame) -> pd.DataFrame:
    """
    Fakty z XE + czas symulowany + etykiety (tylko do kolumn etykiet).
    UserId i RoleId wynikają z loginu w XE (katalog kont: login -> użytkownik, rola).
    """
    ev = joined.merge(gt, on="event_uid", how="left", validate="one_to_one")
    by_login = users.set_index("login_name")
    unknown = sorted(set(ev["username"]) - set(by_login.index))
    if unknown:
        raise ValueError(f"Loginy z XE spoza katalogu użytkowników: {unknown[:5]}")
    ev["UserId"] = ev["username"].map(by_login["user_id"])
    ev["RoleId"] = ev["username"].map(by_login["role"])
    ev["_unit_global"] = "ALL"

    ts = pd.to_datetime(ev["sim_timestamp"], format="ISO8601")     # z ułamkiem sekundy i bez
    ev["sim_timestamp"] = ts
    ev["WindowStart"] = ts.dt.floor(WINDOW)
    ev["op_hour"] = ts.dt.hour + ts.dt.minute / 60 + ts.dt.second / 3600
    ev["join_count"] = ev["sql_text"].str.count(r"(?i)\bJOIN\b")
    for col in ("gt_anomaly_class", "gt_incident_id", "intensity", "hard_normal"):
        ev[col] = ev[col].fillna("").astype(str)
    ev["gt_severity"] = pd.to_numeric(ev["gt_severity"], errors="coerce").fillna(0)
    return ev


# --- cechy niezależne od normy ------------------------------------------------------------

def _joined_unique(values: pd.Series) -> str:
    items = set()
    for v in values:
        items.update(x for x in str(v).split(";") if x)
    return ";".join(sorted(items))


def base_windows(ev: pd.DataFrame) -> pd.DataFrame:
    """Agregaty okna liczone wyłącznie z jego własnych zdarzeń (bez statystyk TRAIN)."""
    keys = ["UserId", "WindowStart"]
    g = ev.groupby(keys, sort=True)
    w = g.agg(RoleId=("RoleId", "first"),
              operation_count=("event_uid", "size"),
              session_count=("xe_connection_id", "nunique"),
              unique_tables=("target_table", "nunique"),
              sum_logical_reads=("logical_reads", "sum"),
              sum_rows=("row_count", "sum"),
              mean_duration=("duration_us", "mean"),
              avg_join_count=("join_count", "mean"),
              avg_sql_length=("sql_text_length", "mean"))
    w["ops_per_session"] = w["operation_count"] / w["session_count"]

    ops = pd.crosstab([ev["UserId"], ev["WindowStart"]], ev["operation_type"])
    for op in OPERATIONS:
        w[f"{op.lower()}_ratio"] = (ops[op] if op in ops else 0) / w["operation_count"]
    dev = pd.crosstab([ev["UserId"], ev["WindowStart"]], ev["device"])
    unknown_dev = sorted(set(dev.columns) - set(DEVICE_TYPES))
    if unknown_dev:
        raise ValueError(f"Nieznane typy urządzeń w XE: {unknown_dev}")
    for d in DEVICE_TYPES:
        w[f"device_{d}"] = (dev[d] if d in dev else 0) / w["operation_count"]

    start = w.index.get_level_values("WindowStart")
    hour = start.hour + start.minute / 60
    dow = start.dayofweek
    w["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    w["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    w["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    w["dow_cos"] = np.cos(2 * np.pi * dow / 7)
    w["is_weekend"] = (dow >= 5).astype(int)
    for r in ROLES:
        w[f"role_{r}"] = (w["RoleId"] == r).astype(int)

    # Etykiety: tylko do oceny (D2). Nie są cechami.
    w["IsAnomaly"] = g["gt_is_anomaly"].max().astype(int)
    w["IncidentIds"] = g["gt_incident_id"].agg(_joined_unique)
    w["AnomalyClasses"] = g["gt_anomaly_class"].agg(_joined_unique)
    w["Intensities"] = ev[ev["gt_is_anomaly"] == 1].groupby(keys)["intensity"].agg(_joined_unique)
    w["Intensities"] = w["Intensities"].fillna("")
    w["Severity"] = g["gt_severity"].max().astype(int)
    w["HardNormal"] = g["hard_normal"].agg(_joined_unique)
    return w.reset_index()


# --- statystyki normy z TRAIN ----------------------------------------------------------------

@dataclass
class UnitReference:
    """Norma jednostki modelowania wyznaczona z okien i operacji TRAIN."""
    n_windows: int
    n_operations: int
    hour_window_counts: List[int]            # liczba okien TRAIN, których start wypada w godzinie h (0-23)
    op_hour_low: float                       # P1 godzin operacji
    op_hour_high: float                      # P99 godzin operacji
    median_operation_count: float
    known_ips: List[str]
    table_counts: Dict[str, int]
    source: str = "own"                      # "own" albo "role:<rola>" (zastępstwo D12)

    def hour_rarity(self, hours: np.ndarray) -> np.ndarray:
        counts = np.asarray(self.hour_window_counts, dtype=float)
        p = (counts[hours] + 1) / (self.n_windows + 24)
        return -np.log(p)

    def table_share(self, table: str) -> float:
        return (self.table_counts.get(table, 0) + 1) / (self.n_operations + len(TABLES))


def _unit_reference(ev: pd.DataFrame, windows: pd.DataFrame) -> UnitReference:
    hours = windows["WindowStart"].dt.hour.to_numpy()
    lo, hi = np.percentile(ev["op_hour"], AFTER_HOURS_PERCENTILES)
    return UnitReference(
        n_windows=len(windows),
        n_operations=len(ev),
        hour_window_counts=np.bincount(hours, minlength=24).astype(int).tolist(),
        op_hour_low=round(float(lo), 4),
        op_hour_high=round(float(hi), 4),
        median_operation_count=float(windows["operation_count"].median()),
        known_ips=sorted(ev["ip"].astype(str).unique()),
        table_counts={t: int(n) for t, n in ev["target_table"].value_counts().items()},
    )


def fit_reference(train_ev: pd.DataFrame, train_windows: pd.DataFrame, variant: str) -> Dict[str, UnitReference]:
    """Statystyki normy z TRAIN dla każdej jednostki wariantu (D14, D17)."""
    if (train_windows["IsAnomaly"] == 1).any():
        raise ValueError("TRAIN zawiera okna anomalne; norma musi pochodzić z samej normy (D5).")
    col = unit_column(variant)
    tw = train_windows.copy()
    tw["_unit_global"] = "ALL"
    refs = {unit: _unit_reference(train_ev[train_ev[col] == unit], tw[tw[col] == unit])
            for unit in sorted(train_ev[col].unique())}
    if variant == "user":
        role_refs = fit_reference(train_ev, train_windows, "role")
        role_of = train_windows.groupby("UserId")["RoleId"].first()
        for user, ref in refs.items():
            if ref.n_windows < MIN_USER_WINDOWS:
                role = role_of[user]
                refs[user] = UnitReference(**{**role_refs[role].__dict__, "source": f"role:{role}"})
    return refs


# --- cechy względem normy ----------------------------------------------------------------

def relative_features(ev: pd.DataFrame, windows: pd.DataFrame, refs: Dict[str, UnitReference],
                      variant: str) -> pd.DataFrame:
    """Dokleja do okien cechy liczone względem normy TRAIN jednostki modelowania."""
    col = unit_column(variant)
    w = windows.copy()
    w["_unit_global"] = "ALL"
    missing = sorted(set(w[col]) - set(refs))
    if missing:
        raise ValueError(f"Brak statystyk TRAIN dla jednostek {missing[:5]} (wariant {variant}).")

    e = ev[["UserId", "WindowStart", "op_hour", "ip", "target_table"]].copy()
    unit_e = ev[col]
    lo = unit_e.map({u: r.op_hour_low for u, r in refs.items()})
    hi = unit_e.map({u: r.op_hour_high for u, r in refs.items()})
    e["after_hours"] = ((e["op_hour"] < lo) | (e["op_hour"] > hi)).astype(int)
    known = {u: set(r.known_ips) for u, r in refs.items()}
    e["ip_known"] = [int(ip in known[u]) for u, ip in zip(unit_e, e["ip"].astype(str))]
    share = {(u, t): r.table_share(t) for u, r in refs.items() for t in TABLES}
    p = np.array([share.get((u, t), 1.0 / (refs[u].n_operations + len(TABLES)))
                  for u, t in zip(unit_e, e["target_table"])])
    e["rare_contrib"] = np.where(p < RARE_TABLE_SHARE, -np.log(p), 0.0)
    e["new_table"] = [int(refs[u].table_counts.get(t, 0) == 0) for u, t in zip(unit_e, e["target_table"])]

    agg = e.groupby(["UserId", "WindowStart"]).agg(after_hours_flag=("after_hours", "max"),
                                                    ip_known_flag=("ip_known", "min"),
                                                    rare_resource_score=("rare_contrib", "sum"),
                                                    new_table_flag=("new_table", "max"))
    w = w.merge(agg, left_on=["UserId", "WindowStart"], right_index=True, how="left", validate="one_to_one")

    hours = w["WindowStart"].dt.hour.to_numpy()
    w["hour_rarity"] = 0.0
    w["volume_ratio_to_median"] = 0.0
    for unit, idx in w.groupby(col).groups.items():
        ref = refs[unit]
        pos = w.index.get_indexer(idx)
        w.loc[idx, "hour_rarity"] = ref.hour_rarity(hours[pos])
        w.loc[idx, "volume_ratio_to_median"] = w.loc[idx, "operation_count"] / ref.median_operation_count
    return w.drop(columns=["_unit_global"])


# --- kontrola poprawności ---------------------------------------------------------------

@dataclass
class FeatureReport:
    seed: int
    checks: Dict[str, List[str]] = field(default_factory=dict)
    summary: Dict[str, dict] = field(default_factory=dict)

    def add(self, name: str, problems: List[str]) -> None:
        self.checks[name] = problems[:10] + ([f"... i {len(problems) - 10} więcej"] if len(problems) > 10 else [])

    @property
    def ok(self) -> bool:
        return not any(self.checks.values())

    def print(self) -> None:
        # Nazwy kontroli mają postać "<split>/<wariant>: <kontrola>"; wypis zbiorczy per kontrola.
        kinds: Dict[str, List[str]] = {}
        for name, problems in self.checks.items():
            where, kind = name.split(": ", 1)
            kinds.setdefault(kind, [])
            if problems:
                kinds[kind].append(f"{where}: {'; '.join(problems)}")
        for kind, failed in kinds.items():
            print(f"  [{'OK' if not failed else 'BŁĄD':>4}] {kind}")
            for f in failed:
                print(f"         - {f}")


def validate_windows(report: FeatureReport, split: str, variant: str, ev: pd.DataFrame,
                     w: pd.DataFrame, incidents: pd.DataFrame) -> None:
    tag = f"{split}/{variant}"
    feats = feature_columns(variant)
    report.add(f"{tag}: suma operacji okien = liczba zdarzeń",
               [] if int(w["operation_count"].sum()) == len(ev) else
               [f"{int(w['operation_count'].sum())} != {len(ev)}"])
    report.add(f"{tag}: para (UserId, WindowStart) unikalna",
               [] if not w.duplicated(["UserId", "WindowStart"]).any() else ["duplikaty okien"])
    values = w[feats].to_numpy(dtype=float)
    bad = [c for c, ok in zip(feats, np.isfinite(values).all(axis=0)) if not ok]
    report.add(f"{tag}: cechy skończone (bez NaN/inf)", bad)
    ratio_sum = w[FEATURE_GROUPS["ratios"]].sum(axis=1)
    dev_sum = w[[f"device_{d}" for d in DEVICE_TYPES]].sum(axis=1)
    report.add(f"{tag}: udziały operacji i urządzeń sumują się do 1",
               [f"okno {i}" for i in w.index[(ratio_sum - 1).abs().gt(1e-9) | (dev_sum - 1).abs().gt(1e-9)]])
    leak = sorted(set(feats) & (set(LABEL_COLUMNS) | set(ID_COLUMNS)))
    report.add(f"{tag}: cechy rozłączne z etykietami i identyfikatorami", leak)
    in_windows = set(x for ids in w["IncidentIds"] for x in ids.split(";") if x)
    expected = set(incidents["incident_id"].astype(str))
    report.add(f"{tag}: każdy incydent w >= 1 oknie i tylko incydenty z ground truth",
               [f"brak {i}" for i in sorted(expected - in_windows)] +
               [f"nadmiarowy {i}" for i in sorted(in_windows - expected)])
    anomalous_keys = set(map(tuple, ev.loc[ev["gt_is_anomaly"] == 1, ["UserId", "WindowStart"]].drop_duplicates()
                             .itertuples(index=False)))
    flagged = set(map(tuple, w.loc[w["IsAnomaly"] == 1, ["UserId", "WindowStart"]].itertuples(index=False)))
    report.add(f"{tag}: okno anomalne <=> zawiera zdarzenie incydentu (D2)",
               [] if anomalous_keys == flagged else [f"{len(anomalous_keys ^ flagged)} niezgodnych okien"])


# --- przebieg dla ziarna -----------------------------------------------------------------

def _split_summary(w: pd.DataFrame) -> dict:
    anom = w[w["IsAnomaly"] == 1]
    per_class = {}
    for cls in sorted(set(c for cs in anom["AnomalyClasses"] for c in cs.split(";") if c)):
        per_class[cls] = int(anom["AnomalyClasses"].str.split(";").map(lambda cs, c=cls: c in cs).sum())
    return {"windows": int(len(w)), "anomalous_windows": int(len(anom)),
            "anomalous_share": round(float(len(anom) / len(w)), 4) if len(w) else 0.0,
            "anomalous_windows_per_class": per_class,
            "windows_per_user_min": int(w.groupby("UserId").size().min()),
            "hard_normal_windows": int((w["HardNormal"] != "").sum())}


def build_seed(seed: int, variants: Iterable[str] = VARIANTS, verbose: bool = True) -> FeatureReport:
    users = load_users(seed)
    events = {s: load_events(s, seed, users) for s in SPLITS}
    base = {s: base_windows(events[s]) for s in SPLITS}
    report = FeatureReport(seed=seed)
    out = features_dir(seed)
    out.mkdir(parents=True, exist_ok=True)

    for variant in variants:
        refs = fit_reference(events["train"], base["train"], variant)
        fallback = sorted(u for u, r in refs.items() if r.source != "own")
        report.summary.setdefault("fallback_to_role", {})[variant] = fallback
        reference_json(variant, seed).write_text(json.dumps(
            {"seed": seed, "variant": variant, "units": {u: r.__dict__ for u, r in refs.items()}},
            ensure_ascii=False, indent=1), encoding="utf-8")
        for split in SPLITS:
            w = relative_features(events[split], base[split], refs, variant)
            w = w[ID_COLUMNS + LABEL_COLUMNS + feature_columns(variant)]
            incidents = pd.read_csv(incidents_csv(split, seed), keep_default_na=False)
            validate_windows(report, split, variant, events[split], w, incidents)
            w.to_csv(windows_csv(variant, split, seed), index=False)
            report.summary.setdefault(split, _split_summary(w))

    features_report_json(seed).write_text(json.dumps(
        {"seed": seed, "ok": report.ok, "checks": report.checks, "summary": report.summary},
        ensure_ascii=False, indent=1), encoding="utf-8")
    if verbose:
        print(f"\n=== Cechy okien: ziarno {seed} -> {out}")
        report.print()
        for split in SPLITS:
            s = report.summary[split]
            print(f"  {split:10s} okna {s['windows']:5d}, anomalne {s['anomalous_windows']:4d} "
                  f"({s['anomalous_share']:.1%}), trudne legalne {s['hard_normal_windows']:4d}, "
                  f"min okien/użytkownik {s['windows_per_user_min']}")
        print(f"WYNIK: {'POPRAWNY' if report.ok else 'BŁĘDNY'}")
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Okna UserId × 30 min i cechy D14 z danych po joinie XE.")
    parser.add_argument("--seed", type=int, nargs="+", required=True)
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    args = parser.parse_args(argv)
    ok = True
    for seed in args.seed:
        ok &= build_seed(seed, args.variants).ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
