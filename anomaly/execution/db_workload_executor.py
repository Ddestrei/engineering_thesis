"""
Wykonanie pliku zdarzeń jednego przebiegu na SQL Server (bez przywracania bazy).

Pełny, powtarzalny przebieg (restore z .bak, role, rekordy startowe, sesja XE, LOGGER, join)
uruchamia orkiestrator:  python -m anomaly.run_experiment --seed 11

Ten skrypt służy do podglądu zapytań (--dry-run) i do ręcznych prób:
    python -m anomaly.execution.db_workload_executor --seed 11 --split validation --dry-run
"""
from __future__ import annotations

import argparse

from anomaly.execution.DBConfig import DBConfig
from anomaly.execution.DBWorkloadExecutor import DBWorkloadExecutor
from anomaly.execution.ExecutionConfig import ExecutionConfig
from anomaly.paths import DEFAULT_SEED, SPLITS, events_csv, users_csv


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wykonuje operacje generatora (events_<split>.csv) na SQL Server.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="ziarno (katalog data/seed_<N>/)")
    parser.add_argument("--split", choices=SPLITS, default="validation")
    parser.add_argument("--dry-run", action="store_true", help="tylko buduje i wypisuje zapytania")
    parser.add_argument("--limit", type=int, default=None, help="tylko pierwsze N operacji")
    parser.add_argument("--delay", type=float, default=0.0, help="odstęp między zapytaniami [s]")
    parser.add_argument("--driver", type=str, default=None, help="sterownik ODBC (domyślnie auto)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    executor = DBWorkloadExecutor(DBConfig(driver=args.driver),
                                  ExecutionConfig(delay_sec=args.delay, dry_run=args.dry_run, limit=args.limit))
    stats = executor.execute_csv(events_csv(args.split, args.seed), users_csv(args.seed))
    return 0 if stats["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
