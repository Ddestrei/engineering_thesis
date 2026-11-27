"""
Faza 8: eksport wyników do bazy AnomalyResults dla panelu WPF.

Źródła (nic nie jest liczone od nowa ani dopasowywane):
- results/seed_<N>/evaluation/<split>/<konfiguracja>/scores_<wariant>.csv (Faza 7: score'y, alarmy, RiskScore),
- results/seed_<N>/detectors/<konfiguracja>/frozen_config.json i modele .joblib (Faza 6: hiperparametry,
  progi, mediany/skale robust i progi reguł do wyjaśnienia alertu),
- data/seed_<N>/features/windows_<wariant>_{train,<split>}.csv (cechy okna i profil normalny z TRAIN),
- data/seed_<N>/joined/joined_<split>.csv i generated/incidents_<split>.csv (zdarzenia XE, incydenty).

Kontrole: SHA-256 zamrożonej konfiguracji zgadza się z manifestem ewaluacji, liczba okien score'ów = liczba
okien cech, norm każdego alertu ≥ próg główny jego grupy, każde zdarzenie ma swoje okno.

Uruchomienie (Windows Authentication, bez haseł):
    python -m anomaly.export.results_db --split test --config full --seed 11 22 33 44 55
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

from anomaly.evaluation.score_frozen import detectors_of, load_frozen
from anomaly.features.windowing import FEATURE_GROUPS, ROLE_COLUMNS, VARIANTS, load_events
from anomaly.models.training import threshold_groups, units_of
from anomaly.paths import (SQL_DIR, evaluation_root, incidents_csv, models_joblib, scores_csv, windows_csv,
                           xe_json)
from anomaly.settings import get_setting
from anomaly.versioning import git_version

SCHEMA_SQL = SQL_DIR / "anomaly_results_schema.sql"
KEYS = ["UserId", "WindowStart"]
GROUP_OF = {f: g for g, fs in FEATURE_GROUPS.items() for f in fs} | {f: "context" for f in ROLE_COLUMNS}


class ExportError(RuntimeError):
    pass


# --- budowa tabel (bez bazy) ------------------------------------------------------------------

def window_rows(scores: pd.DataFrame, windows: pd.DataFrame) -> pd.DataFrame:
    """Okna przebiegu: etykiety i składniki RiskScore (wspólne dla wariantów) + liczba operacji."""
    cols = KEYS + ["RoleId", "IsAnomaly", "IncidentIds", "AnomalyClasses", "Intensities", "Severity", "HardNormal",
                   "severity_weight", "ctx_non_primary_device", "ctx_after_hours", "ctx_unknown_ip", "context_weight"]
    out = scores[cols].merge(windows[KEYS + ["operation_count"]], on=KEYS, how="left", validate="one_to_one")
    if out["operation_count"].isna().any():
        raise ExportError("Okno score'ów bez okna cech.")
    for c in ("IncidentIds", "AnomalyClasses", "Intensities", "HardNormal"):
        out[c] = out[c].fillna("").astype(str)
    return out


def feature_rows(windows: pd.DataFrame, train: pd.DataFrame, features: List[str], variant: str,
                 robust_models: Dict[str, object], rules_models: Dict[str, object]) -> pd.DataFrame:
    """
    Cechy okna w wariancie (format długi) z porównaniem do profilu normalnego:
    - UnitMedian / UnitP01 / UnitP99: okna TRAIN jednostki modelu (global / rola / użytkownik),
    - UserMedian: okna TRAIN tego użytkownika (ten sam wariant cech),
    - RobustZ: |x − mediana| / skala z dopasowanego detektora robust (D12), NULL dla cech binarnych,
    - RuleViolated: przekroczenie progu reguły D13 (cechy z regułą), inaczej NULL.
    """
    w = windows[KEYS + features].copy()
    w["_unit"] = units_of(windows, variant).to_numpy()
    t = train[features].copy()
    t["_unit"] = units_of(train, variant).to_numpy()
    g = t.groupby("_unit")[features]
    prof = pd.concat({"UnitMedian": g.median(), "UnitP01": g.quantile(0.01), "UnitP99": g.quantile(0.99)}, axis=1)
    prof = prof.stack(level=1, future_stack=True).rename_axis(["_unit", "Feature"]).reset_index()
    user_med = (train.groupby("UserId")[features].median().stack().rename("UserMedian")
                .rename_axis(["UserId", "Feature"]).reset_index())

    z = pd.DataFrame(np.nan, index=w.index, columns=features)
    rule = pd.DataFrame(np.nan, index=w.index, columns=features)
    for unit, idx in w.groupby("_unit").groups.items():
        x = w.loc[idx, features]
        m = robust_models.get(unit)
        if m is not None:
            names = [m.features_[i] for i in m.idx_]
            z.loc[idx, names] = np.abs(x[names].to_numpy(float) - m.median_) / m.scale_
        r = rules_models.get(unit)
        if r is not None:
            for f, p99 in r.upper_.items():
                rule.loc[idx, f] = (x[f] > p99).astype(float)
            for f, (p1, p99) in r.ratio_bounds_.items():
                rule.loc[idx, f] = ((x[f] < p1) | (x[f] > p99)).astype(float)

    long = w.melt(id_vars=KEYS + ["_unit"], value_vars=features, var_name="Feature", value_name="Value",
                  ignore_index=False)
    long["RobustZ"] = z.melt(value_vars=features, ignore_index=False)["value"].to_numpy()
    long["RuleViolated"] = rule.melt(value_vars=features, ignore_index=False)["value"].to_numpy()
    long = long.merge(prof, on=["_unit", "Feature"], how="left").merge(user_med, on=["UserId", "Feature"], how="left")
    long["Variant"] = variant
    long["FeatureGroup"] = long["Feature"].map(GROUP_OF)
    return long.drop(columns="_unit")


def alert_rows(scores: pd.DataFrame, detector: str, variant: str, thresholds: dict) -> pd.DataFrame:
    """Alerty modelu: okna z alarmem przy głównym progu (D7) i ich score'y."""
    a = scores[scores[f"{detector}_alarm_main"] == 1]
    groups = threshold_groups(a, variant)
    return pd.DataFrame({
        "UserId": a["UserId"], "WindowStart": a["WindowStart"],
        "RawScore": a[f"{detector}_raw"], "NormScore": a[f"{detector}_norm"], "TailExcess": a[f"{detector}_excess"],
        "RiskScore": a[f"{detector}_risk"], "ThresholdGroup": groups,
        "MainThreshold": groups.map({k: v["main"]["threshold"] for k, v in thresholds.items()}),
        "AlarmRecall80": a[f"{detector}_alarm_recall80"].astype(int)}).reset_index(drop=True)


def model_row(res: dict, n_features: int, n_alerts: int) -> dict:
    main = res["val_metrics"]["main"]
    return {"Variant": res["variant"], "Detector": res["detector"],
            "Hyperparameters": json.dumps(res["hyperparameters"], ensure_ascii=False),
            "FeatureCount": n_features, "ValPrAuc": res["val_metrics"].get("pr_auc"),
            "ValMainFpr": main["fpr"], "ValMainRecall": main["recall"], "ValMainMcc": main["mcc"],
            "AlertCount": n_alerts}


def threshold_rows(thresholds: dict) -> List[dict]:
    return [{"GroupKey": g, "MainThreshold": t["main"]["threshold"], "Recall80Threshold": t["recall80"]["threshold"],
             "ValFpr": t["main"]["fpr"], "ValRecall": t["main"]["recall"]} for g, t in thresholds.items()]


def build_run(seed: int, split: str, config: str) -> dict:
    """Wszystkie tabele jednego przebiegu (ziarno × split × konfiguracja) jako DataFrame'y z kluczami lokalnymi."""
    frozen, sha = load_frozen(seed, config)
    manifest = json.loads((evaluation_root(seed, split) / "manifest.json").read_text(encoding="utf-8"))
    eval_cfg = manifest["configs"].get(config)
    if eval_cfg is None or eval_cfg["frozen_sha256"] != sha:
        raise ExportError(f"Ziarno {seed}: score'y {split}/{config} nie pochodzą z bieżącej zamrożonej konfiguracji "
                          "(uruchom ponownie ewaluację Fazy 7).")
    out = {"frozen": frozen, "sha": sha, "evaluation_commit": manifest["code_version"]["commit"],
           "features": [], "models": []}
    windows_out = None
    for variant in VARIANTS:
        res_v = frozen["results"][variant]
        features = res_v["features"]
        # round_trip: domyślny parser pandas potrafi zmienić ostatni bit liczby, a norm alertu jest porównywany
        # z progiem co do bitu (alarm = norm >= próg).
        read = dict(keep_default_na=False, parse_dates=["WindowStart"], float_precision="round_trip")
        scores = pd.read_csv(scores_csv(variant, split, seed, config), **read)
        windows = pd.read_csv(windows_csv(variant, split, seed), **read)
        train = pd.read_csv(windows_csv(variant, "train", seed), **read)
        if len(scores) != len(windows):
            raise ExportError(f"Ziarno {seed}, {variant}: {len(scores)} okien score'ów, {len(windows)} okien cech.")
        if windows_out is None:
            windows_out = window_rows(scores, windows)
        robust = joblib.load(models_joblib(variant, "robust", seed, config))
        rules = joblib.load(models_joblib(variant, "rules", seed, config))
        out["features"].append(feature_rows(windows, train, features, variant, robust, rules))
        for det in detectors_of(frozen, variant):
            alerts = alert_rows(scores, det, variant, res_v[det]["thresholds"])
            if (alerts["NormScore"] < alerts["MainThreshold"]).any():
                raise ExportError(f"Ziarno {seed}, {variant}/{det}: alert z norm poniżej progu głównego.")
            out["models"].append({"row": model_row(res_v[det], len(features), len(alerts)),
                                  "thresholds": threshold_rows(res_v[det]["thresholds"]), "alerts": alerts})
    out["windows"] = windows_out
    out["features"] = pd.concat(out["features"], ignore_index=True)

    events = load_events(split, seed)
    known = windows_out.set_index(KEYS).index
    missing = ~events.set_index(KEYS).index.isin(known)
    if missing.any():
        raise ExportError(f"Ziarno {seed}: {int(missing.sum())} zdarzeń bez okna.")
    out["events"] = events
    inc = pd.read_csv(incidents_csv(split, seed), keep_default_na=False)
    out["incidents"] = inc
    return out


# --- zapis do bazy ------------------------------------------------------------------------------

def _connect(database: str, autocommit: bool = False):
    import pyodbc
    from anomaly.execution.db_admin import _driver
    conn_str = (f"Driver={{{_driver()}}};Server={get_setting('server')};Database={database};Trusted_Connection=yes;"
                f"TrustServerCertificate=yes;APP=ANOMALY-EXPORT;")
    return pyodbc.connect(conn_str, autocommit=autocommit, timeout=30)


def ensure_database(database: str) -> None:
    """Tworzy bazę (jeśli brak) i tabele ze schematu sql_files/anomaly_results_schema.sql."""
    from anomaly.execution.db_admin import _ident, run_script
    with _connect("master", autocommit=True) as conn:
        conn.execute(f"IF DB_ID(N'{database}') IS NULL CREATE DATABASE {_ident(database)};")
    run_script(SCHEMA_SQL.read_text(encoding="utf-8"), database=database)


def _records(df: pd.DataFrame, cols: List[str]) -> List[tuple]:
    sub = df[cols].astype(object)
    sub = sub.where(pd.notna(sub), None)
    rows = []
    for r in sub.itertuples(index=False, name=None):
        rows.append(tuple(v.to_pydatetime() if isinstance(v, pd.Timestamp) else
                          (v.item() if isinstance(v, np.generic) else v) for v in r))
    return rows


def _insert(cur, table: str, columns: List[str], rows: List[tuple]) -> None:
    if not rows:
        return
    cols = ", ".join(f"[{c}]" for c in columns)
    cur.fast_executemany = True
    cur.executemany(f"INSERT INTO dbo.{table} ({cols}) VALUES ({', '.join('?' * len(columns))})", rows)


DELETE_RUN = """
DELETE a FROM dbo.Alerts a JOIN dbo.Models m ON a.ModelId = m.ModelId WHERE m.RunId = ?;
DELETE t FROM dbo.Thresholds t JOIN dbo.Models m ON t.ModelId = m.ModelId WHERE m.RunId = ?;
DELETE FROM dbo.Models WHERE RunId = ?;
DELETE e FROM dbo.Events e JOIN dbo.Windows w ON e.WindowId = w.WindowId WHERE w.RunId = ?;
DELETE f FROM dbo.WindowFeatures f JOIN dbo.Windows w ON f.WindowId = w.WindowId WHERE w.RunId = ?;
DELETE FROM dbo.Windows WHERE RunId = ?;
DELETE FROM dbo.Incidents WHERE RunId = ?;
DELETE FROM dbo.Runs WHERE RunId = ?;
"""


def write_run(conn, seed: int, split: str, config: str, run: dict) -> dict:
    """Zapis jednego przebiegu w jednej transakcji; poprzednia wersja tego przebiegu jest usuwana."""
    cur = conn.cursor()
    old = cur.execute("SELECT RunId FROM dbo.Runs WHERE Seed = ? AND Split = ? AND Config = ?",
                      seed, split, config).fetchone()
    if old:
        cur.execute(DELETE_RUN, *([old[0]] * 8))
    w = run["windows"]
    cv = run["frozen"]["code_version"]
    run_id = cur.execute(
        "INSERT INTO dbo.Runs (Seed, Split, Config, CodeCommit, CodeDirty, FrozenSha256, EvaluationCommit, "
        "ExportCommit, ExportedAt, WindowCount, AnomalousWindows, EventCount, XeJsonPath) OUTPUT INSERTED.RunId "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        seed, split, config, cv["commit"], int(cv["dirty"]), run["sha"], run["evaluation_commit"],
        git_version()["commit"], datetime.now().replace(microsecond=0), len(w), int(w["IsAnomaly"].sum()),
        len(run["events"]), str(xe_json(f"{split}-s{seed}", seed))).fetchone()[0]

    wcols = ["UserId", "RoleId", "WindowStart", "OperationCount", "IsAnomaly", "IncidentIds", "AnomalyClasses",
             "Intensities", "Severity", "HardNormal", "SeverityWeight", "CtxNonPrimaryDevice", "CtxAfterHours",
             "CtxUnknownIp", "ContextWeight"]
    wdf = w.rename(columns={"operation_count": "OperationCount", "severity_weight": "SeverityWeight",
                            "ctx_non_primary_device": "CtxNonPrimaryDevice", "ctx_after_hours": "CtxAfterHours",
                            "ctx_unknown_ip": "CtxUnknownIp", "context_weight": "ContextWeight"}).assign(RunId=run_id)
    _insert(cur, "Windows", ["RunId"] + wcols, _records(wdf, ["RunId"] + wcols))
    ids = pd.DataFrame.from_records(
        [tuple(r) for r in cur.execute("SELECT WindowId, UserId, WindowStart FROM dbo.Windows WHERE RunId = ?",
                                       run_id).fetchall()], columns=["WindowId", "UserId", "WindowStart"])
    ids["WindowStart"] = pd.to_datetime(ids["WindowStart"])

    def with_id(df: pd.DataFrame) -> pd.DataFrame:
        out = df.merge(ids, on=KEYS, how="left", validate="many_to_one")
        if out["WindowId"].isna().any():
            raise ExportError("Wiersz bez okna w bazie.")
        return out

    n_alerts = 0
    for m in run["models"]:
        row = m["row"]
        mcols = list(row)
        model_id = cur.execute(f"INSERT INTO dbo.Models (RunId, {', '.join(mcols)}) OUTPUT INSERTED.ModelId "
                               f"VALUES (?, {', '.join('?' * len(mcols))})", run_id, *row.values()).fetchone()[0]
        tcols = ["GroupKey", "MainThreshold", "Recall80Threshold", "ValFpr", "ValRecall"]
        _insert(cur, "Thresholds", ["ModelId"] + tcols, [(model_id, *(t[c] for c in tcols)) for t in m["thresholds"]])
        acols = ["WindowId", "RawScore", "NormScore", "TailExcess", "RiskScore", "ThresholdGroup", "MainThreshold",
                 "AlarmRecall80"]
        a = with_id(m["alerts"]).assign(ModelId=model_id)
        _insert(cur, "Alerts", ["ModelId"] + acols, _records(a, ["ModelId"] + acols))
        n_alerts += len(a)

    fcols = ["WindowId", "Variant", "Feature", "FeatureGroup", "Value", "RobustZ", "UnitMedian", "UnitP01", "UnitP99",
             "UserMedian", "RuleViolated"]
    f = with_id(run["features"])
    _insert(cur, "WindowFeatures", fcols, _records(f, fcols))

    inc = run["incidents"].rename(columns={
        "incident_id": "IncidentId", "anomaly_class": "AnomalyClass", "intensity": "Intensity", "user": "UserId",
        "role": "RoleId", "start": "StartTime", "end": "EndTime", "severity_level": "SeverityLevel",
        "n_events": "EventCount", "tables": "Tables", "operations": "Operations"}).assign(RunId=run_id)
    inc["StartTime"] = pd.to_datetime(inc["StartTime"], format="ISO8601")
    inc["EndTime"] = pd.to_datetime(inc["EndTime"], format="ISO8601")
    icols = ["RunId", "IncidentId", "AnomalyClass", "Intensity", "UserId", "RoleId", "StartTime", "EndTime",
             "SeverityLevel", "EventCount", "Tables", "Operations"]
    _insert(cur, "Incidents", icols, _records(inc, icols))

    ev = with_id(run["events"]).rename(columns={
        "event_uid": "EventUid", "sim_timestamp": "SimTimestamp", "physical_timestamp": "PhysicalTimestamp",
        "xe_event_name": "XeEventName", "xe_event_sequence": "XeEventSequence", "xe_session_id": "XeSessionId",
        "xe_connection_id": "XeConnectionId", "username": "LoginName", "host_name": "HostName", "device": "Device",
        "ip": "Ip", "operation_type": "OperationType", "target_table": "TargetTable", "sql_text": "SqlText",
        "duration_us": "DurationUs", "cpu_time_us": "CpuTimeUs", "logical_reads": "LogicalReads",
        "physical_reads": "PhysicalReads", "writes": "Writes", "row_count": "RowCount", "result": "Result",
        "gt_incident_id": "GtIncidentId", "gt_anomaly_class": "GtAnomalyClass"})
    ecols = ["EventUid", "WindowId", "SimTimestamp", "PhysicalTimestamp", "XeEventName", "XeEventSequence",
             "XeSessionId", "XeConnectionId", "LoginName", "HostName", "Device", "Ip", "OperationType", "TargetTable",
             "SqlText", "DurationUs", "CpuTimeUs", "LogicalReads", "PhysicalReads", "Writes", "RowCount", "Result",
             "GtIncidentId", "GtAnomalyClass"]
    ev["GtAnomalyClass"] = ev["GtAnomalyClass"].replace("None", "")
    _insert(cur, "Events", ecols, _records(ev, ecols))
    conn.commit()
    return {"run_id": run_id, "windows": len(w), "models": len(run["models"]), "alerts": n_alerts,
            "features": len(f), "events": len(ev), "incidents": len(inc)}


def export(seeds: Iterable[int], split: str = "test", config: str = "full", database: Optional[str] = None) -> List[dict]:
    database = database or get_setting("results_database")
    ensure_database(database)
    out = []
    with _connect(database) as conn:
        for seed in seeds:
            t0 = time.perf_counter()
            stats = write_run(conn, seed, split, config, build_run(seed, split, config))
            stats["sec"] = round(time.perf_counter() - t0, 1)
            print(f"ziarno {seed} ({split}/{config}) -> {database}: {stats['windows']} okien, {stats['models']} modeli, "
                  f"{stats['alerts']} alertów, {stats['features']} wartości cech, {stats['events']} zdarzeń XE, "
                  f"{stats['incidents']} incydentów ({stats['sec']} s)", flush=True)
            out.append(stats)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Eksport wyników do bazy panelu WPF (Faza 8).")
    parser.add_argument("--seed", type=int, nargs="+", required=True)
    parser.add_argument("--split", choices=["validation", "test"], default="test")
    parser.add_argument("--config", default="full")
    parser.add_argument("--database", default=None, help="domyślnie settings.results_database (AnomalyResults)")
    args = parser.parse_args(argv)
    export(args.seed, args.split, args.config, args.database)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
