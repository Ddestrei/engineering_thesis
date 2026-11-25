"""
Połączenie zdarzeń z Extended Events (JSON z LOGGER-a) z ground truth z generatora po EventUid
(METODOLOGIA D11, PLAN Faza 2).

Zasady:
- Z logów XE pochodzą wszystkie fakty obserwowalne: metryki wykonania, tekst SQL, host, urządzenie, IP.
- Z generatora pochodzą WYŁĄCZNIE: czas symulowany oraz etykiety (kolumny z prefiksem gt_).
  Kolumny gt_ służą do oceny i nie mogą trafić do cech modeli.
- Połączenie musi być pełne: każde zdarzenie generatora ma dokładnie jedno zdarzenie XE i odwrotnie.
  Każde odstępstwo (brak, duplikat, sierota, błąd SQL, niezgodny kontekst) przerywa przebieg.

Uruchomienie (z katalogu głównego repozytorium); zwykle robi to run_experiment.py:
    python -m anomaly.features.xe_join --seed 11 --split validation
        (domyślny JSON: data/seed_11/xe/validation-s11.json, wynik: data/seed_11/joined/)
    python -m anomaly.features.xe_join --seed 11 --split validation --xe inny_plik.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import pandas as pd

from anomaly.generator.dataset import make_run_id
from anomaly.paths import DEFAULT_SEED, SPLITS, events_csv, join_report_json, joined_csv, joined_dir, xe_json

XE_SCHEMA = "xe-export/v3"

# Kolumny ground truth (etykiety). Prefiks gt_ oznacza: tylko do oceny, nigdy jako cecha.
GT_LABEL_COLUMNS = {
    "is_anomaly": "gt_is_anomaly",
    "anomaly_class": "gt_anomaly_class",
    "incident_id": "gt_incident_id",
    "severity_level": "gt_severity",
}

# Kolumny XE (JSON z LOGGER-a) -> nazwy w tabeli połączonej.
XE_COLUMNS = {
    "EventUid": "event_uid",
    "EventName": "xe_event_name",
    "PhysicalTimestamp": "physical_timestamp",
    "EventSequence": "xe_event_sequence",
    "SessionId": "xe_session_id",
    "ClientConnectionId": "xe_connection_id",
    "Username": "username",
    "HostName": "host_name",
    "AppDevice": "device",
    "AppIp": "ip",
    "SqlText": "sql_text",
    "SqlTextLength": "sql_text_length",
    "OperationType": "operation_type",
    "TargetTable": "target_table",       # tabela główna zapytania (pierwsza po FROM / INTO / UPDATE / DELETE FROM)
    "Tables": "tables",                  # wszystkie tabele zapytania (z JOIN-ami)
    "Result": "result",
    "DurationMicroseconds": "duration_us",
    "CpuTimeMicroseconds": "cpu_time_us",
    "LogicalReads": "logical_reads",
    "PhysicalReads": "physical_reads",
    "Writes": "writes",
    "RowCount": "row_count",
}

# Spójność kontekstu: (kolumna XE, kolumna generatora). Sprawdza, że transport przez
# WSID / nazwę aplikacji / login działa i nic nie zostało przekłamane po drodze.
CONTEXT_CHECKS = [
    ("AppRun", "run_id"),
    ("AppDevice", "device_context"),
    ("AppIp", "simulated_ip"),
    ("HostName", "client_hostname"),
    ("Username", "login_name"),
    ("OperationType", "operation_type"),
    ("TargetTable", "target_table"),
]

# Kolumny generatora przenoszone do tabeli połączonej (poza etykietami gt_).
GT_KEEP = ["event_uid", "run_id", "split", "timestamp", "session_id", "user", "login_name"]


class JoinValidationError(RuntimeError):
    pass


@dataclass
class JoinReport:
    xe_file: str
    runs: List[str]
    gt_events: int = 0
    xe_events_in_runs: int = 0
    xe_events_other_runs: int = 0
    joined_events: int = 0
    problems: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(self.problems.values())

    def add(self, check: str, items: Sequence[str]) -> None:
        self.problems[check] = list(items)

    def summary(self) -> str:
        lines = [
            f"Plik XE:                  {self.xe_file}",
            f"Przebiegi:                {', '.join(self.runs)}",
            f"Zdarzenia generatora:     {self.gt_events}",
            f"Zdarzenia XE (te przebiegi): {self.xe_events_in_runs}",
            f"Zdarzenia XE (inne przebiegi, pominięte): {self.xe_events_other_runs}",
            f"Połączone:                {self.joined_events}",
        ]
        for check, items in self.problems.items():
            status = "OK" if not items else f"BŁĄD ({len(items)})"
            lines.append(f"  [{status:>10}] {check}")
            for item in items[:5]:
                lines.append(f"               - {item}")
            if len(items) > 5:
                lines.append(f"               ... i {len(items) - 5} więcej")
        lines.append("WYNIK: " + ("POPRAWNE (pokrycie 100%)" if self.ok else "ODRZUCONE"))
        return "\n".join(lines)


def run_of(event_uid: str) -> str:
    """Prefiks przebiegu z EventUid '<run_id>-<nr>'."""
    return event_uid.rsplit("-", 1)[0]


def load_xe_export(path: Path) -> Tuple[pd.DataFrame, pd.DataFrame, dict]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != XE_SCHEMA:
        raise JoinValidationError(
            f"{path}: nieobsługiwany format '{doc.get('schema')}' (oczekiwano {XE_SCHEMA}). "
            f"Wygeneruj plik nową wersją LOGGER-a."
        )
    events = pd.DataFrame(doc.get("events", []), columns=list(XE_COLUMNS) + ["ClientAppName", "AppRun"])
    errors = pd.DataFrame(doc.get("errors", []), columns=["EventUid", "ErrorNumber", "Severity", "Message"])
    return events, errors, doc


def load_ground_truth(splits: Sequence[str], seed: int) -> pd.DataFrame:
    frames = []
    for split in splits:
        path = events_csv(split, seed)
        if not path.exists():
            raise JoinValidationError(f"Brak pliku ground truth: {path}")
        df = pd.read_csv(path, dtype={"incident_id": "string", "event_uid": "string", "run_id": "string"})
        df.insert(0, "split", split)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def validate_and_join(xe_events: pd.DataFrame, xe_errors: pd.DataFrame, gt: pd.DataFrame,
                      xe_file: str = "") -> Tuple[pd.DataFrame, JoinReport]:
    runs = sorted(gt["run_id"].unique())
    report = JoinReport(xe_file=xe_file, runs=runs, gt_events=len(gt))

    xe_run = xe_events["EventUid"].map(run_of)
    in_runs = xe_run.isin(runs)
    report.xe_events_other_runs = int((~in_runs).sum())
    xe = xe_events[in_runs].copy()
    report.xe_events_in_runs = len(xe)

    errors = xe_errors[xe_errors["EventUid"].map(run_of).isin(runs)] if len(xe_errors) else xe_errors
    report.add("brak błędów SQL w operacjach symulatora (D3)",
               [f"{r.EventUid}: błąd {r.ErrorNumber} – {r.Message}" for r in errors.itertuples()])

    dup = xe["EventUid"][xe["EventUid"].duplicated(keep=False)].unique()
    report.add("każde EventUid najwyżej raz w XE (brak ponownego wykonania)",
               [f"{uid}: {int((xe['EventUid'] == uid).sum())} zdarzeń" for uid in dup])

    gt_uids, xe_uids = set(gt["event_uid"]), set(xe["EventUid"])
    report.add("każde zdarzenie generatora ma zdarzenie XE",
               sorted(gt_uids - xe_uids))
    report.add("każde zdarzenie XE ma zdarzenie generatora (brak sierot)",
               sorted(xe_uids - gt_uids))

    not_ok = xe[xe["Result"].str.upper() != "OK"]
    report.add("wszystkie zapytania zakończone wynikiem OK",
               [f"{r.EventUid}: {r.Result}" for r in not_ok.itertuples()])

    merged = xe.drop_duplicates("EventUid").merge(gt, left_on="EventUid", right_on="event_uid", how="inner")
    for xe_col, gt_col in CONTEXT_CHECKS:
        a = merged[xe_col].astype(str).str.upper()
        b = merged[gt_col].astype(str).str.upper()
        bad = merged[a != b]
        report.add(f"zgodność kontekstu: XE.{xe_col} == generator.{gt_col}",
                   [f"{r['EventUid']}: XE='{r[xe_col]}' generator='{r[gt_col]}'" for _, r in bad.iterrows()])

    if not report.ok:
        return pd.DataFrame(), report

    # Fakty z XE + czas/identyfikatory i etykiety z generatora. Kolumny kontekstu generatora
    # (urządzenie, host, typ operacji, tabela) służyły tylko do kontroli powyżej i nie są przenoszone,
    # więc każda kolumna występuje raz, a jej wartość pochodzi z logu XE.
    xe_cols = [c for c in XE_COLUMNS if c != "EventUid"]
    joined = merged[GT_KEEP + xe_cols + list(GT_LABEL_COLUMNS)].rename(
        columns={**{k: XE_COLUMNS[k] for k in xe_cols}, "timestamp": "sim_timestamp",
                 "session_id": "sim_session_id", **GT_LABEL_COLUMNS})
    joined["tables"] = joined["tables"].map(lambda t: ";".join(t) if isinstance(t, list) else t)
    joined = joined.sort_values(["split", "sim_timestamp", "event_uid"], kind="stable").reset_index(drop=True)
    report.joined_events = len(joined)
    return joined, report


def join_split(seed: int, split: str, xe_path: Path | None = None, verbose: bool = True) -> JoinReport:
    """Łączy eksport XE jednego przebiegu z ground truth splitu; zapisuje joined_<split>.csv i raport."""
    xe_path = Path(xe_path or xe_json(make_run_id(split, seed), seed))
    out_dir = joined_dir(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = join_report_json(split, seed)
    try:
        xe_events, xe_errors, _ = load_xe_export(xe_path)
        gt = load_ground_truth([split], seed)
    except (JoinValidationError, FileNotFoundError) as e:
        report = JoinReport(xe_file=str(xe_path), runs=[make_run_id(split, seed)])
        report.add("odczyt plików wejściowych", [str(e)])
    else:
        joined, report = validate_and_join(xe_events, xe_errors, gt, xe_file=str(xe_path))
        if report.ok:
            joined.to_csv(joined_csv(split, seed), index=False)
    if not report.ok and joined_csv(split, seed).exists():
        joined_csv(split, seed).unlink()          # nie zostawiać wyniku poprzedniego, poprawnego przebiegu
    report_path.write_text(
        json.dumps({"ok": report.ok, "xe_file": report.xe_file, "runs": report.runs,
                    "gt_events": report.gt_events, "joined_events": report.joined_events,
                    "problems": report.problems}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if verbose:
        print(report.summary())
        if report.ok:
            print(f"Zapisano {report.joined_events} zdarzeń -> {joined_csv(split, seed)}")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Łączy zdarzenia XE z ground truth po EventUid i waliduje pokrycie.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--split", choices=list(SPLITS), required=True)
    parser.add_argument("--xe", type=Path, default=None,
                        help="JSON z LOGGER-a (domyślnie data/seed_<N>/xe/<split>-s<N>.json)")
    args = parser.parse_args(argv)
    return 0 if join_split(args.seed, args.split, args.xe).ok else 1


if __name__ == "__main__":
    sys.exit(main())
