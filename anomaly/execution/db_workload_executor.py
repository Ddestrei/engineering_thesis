"""
Skrypt CLI uruchamiający egzekucję zapytań z plików CSV na bazie danych SQL Server przy użyciu argumentów linii poleceń.
"""

from __future__ import annotations
import argparse
from pathlib import Path
from typing import List
from DBConfig import DBConfig
from ExecutionConfig import ExecutionConfig
from DBWorkloadExecutor import logger, DBWorkloadExecutor

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wykonuje zapytania SQL na bazie SQL Server na podstawie plików CSV z logami CEO."
    )
    parser.add_argument(
        "--csv",
        type=str,
        default="generated_data/ceo_train.csv",
        help="Ścieżka do pliku CSV (np. generated_data/ceo_train.csv)",
    )
    parser.add_argument(
        "--all-csv",
        action="store_true",
        help="Wykonaj po kolei wszystkie pliki: train, validation, test w katalogu generated_data/",
    )
    parser.add_argument(
        "--server",
        type=str,
        default="localhost",
        help="Nazwa instancji SQL Server (domyślnie: localhost)",
    )
    parser.add_argument(
        "--database",
        type=str,
        default="AdventureWorksLT2025",
        help="Nazwa bazy danych (domyślnie: AdventureWorksLT2025)",
    )
    parser.add_argument(
        "--schema",
        type=str,
        default="SalesLT",
        help="Schemat tabel (domyślnie: SalesLT)",
    )
    parser.add_argument(
        "--driver",
        type=str,
        default=None,
        help="Nazwa sterownika ODBC (domyślnie: auto-wybór)",
    )
    parser.add_argument(
        "--user",
        type=str,
        default="CEO_User",
        help="Użytkownik SQL Server (domyślnie: CEO_User)",
    )
    parser.add_argument(
        "--password",
        type=str,
        default="CeoPass2026!Secure#",
        help="Hasło użytkownika SQL Server (domyślnie dla CEO_User: CeoPass2026!Secure#)",
    )
    parser.add_argument(
        "--windows-auth",
        action="store_true",
        help="Wymuś uwierzytelnianie zintegrowane Windows (Trusted_Connection) zamiast konta CEO_User",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.03,
        help="Odstęp czasu między kolejnymi zapytaniami w sekundach (domyślnie: 0.03)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maksymalna liczba zapytań do wykonania (do szybkich testów)",
    )
    parser.add_argument(
        "--tag-normal",
        type=str,
        default="NORMAL_TRAFFIC",
        help="Wartość znacznika ScenarioID dla normalnego ruchu (domyślnie: NORMAL_TRAFFIC)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tryb próbny: wypisuje generowane zapytania bez łączenia z bazą danych",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Usuń rekordy symulacyjne wstawione do bazy danych",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    use_trusted = args.windows_auth

    db_cfg = DBConfig(
        server=args.server,
        database=args.database,
        schema=args.schema,
        driver=args.driver,
        trusted_connection=use_trusted,
        username=None if use_trusted else args.user,
        password=None if use_trusted else args.password,
    )

    exec_cfg = ExecutionConfig(
        delay_sec=args.delay,
        tag_normal=args.tag_normal,
        dry_run=args.dry_run,
        limit=args.limit,
    )

    executor = DBWorkloadExecutor(db_cfg, exec_cfg)

    if args.cleanup:
        executor.cleanup_simulation_data()
        return

    csv_targets: List[Path] = []
    if args.all_csv:
        base_dir = Path("generated_data")
        csv_targets = [
            base_dir / "ceo_train.csv",
            base_dir / "ceo_validation.csv",
            base_dir / "ceo_test.csv",
        ]
    else:
        csv_targets = [Path(args.csv)]

    for target in csv_targets:
        if not target.exists():
            logger.warning(f"Pominięto nieistniejący plik: {target}")
            continue

        executor.execute_csv(target)

    if not args.dry_run:
        logger.info("\n[WSKAZÓWKA]:")
        logger.info("Extended Events buforuje zdarzenia w pamięci podręcznej.")
        logger.info("Odczekaj 5 sekund (MAX_DISPATCH_LATENCY) przed odczytem pliku .xel w aplikacji LOGGER!")


if __name__ == "__main__":
    main()
