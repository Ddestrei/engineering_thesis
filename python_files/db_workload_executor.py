"""
===============================================================================
DB Workload Executor for Extended Events & ML Anomaly Detection
===============================================================================
Skrypt w języku Python realizujący operacje bazodanowe na podstawie wygenerowanych
plików CSV (profil CEO) na bazie AdventureWorksLT2025 w SQL Server.

Cechy i integracja z aplikacją LOGGER (.NET):
1. Tagowanie Ground Truth:
   - Każde zapytanie T-SQL zawiera komentarz: /* [ScenarioID:<id>] */
   - Connection string ustawia parametr APP: "CEO_Client;Device=<device>;ScenarioID=<id>"
   - Connection string ustawia parametr WSID (Workstation ID) na nazwę urządzenia:
     Office_PC, Home_PC lub Mobile_Laptop.
2. Zgodność z XeLogReader.cs:
   - Zapytania zawsze rozpoczynają się od słowa kluczowego (SELECT / INSERT) bez
     początkowych komentarzy, co gwarantuje poprawną klasyfikację przez metodę
     DetermineOperationType w C#.
   - Metoda ExtractScenarioId z XeLogReader bezbłędnie wyciąga etykietę scenariusza
     zarówno z nagłówka połączenia (client_app_name), jak i z komentarza SQL.
3. Zarządzanie sesjami:
   - Zapytania o tym samym session_id wykonują się w ramach tego samego fizycznego
     połączenia ODBC (ten sam SPID w SQL Server), co tworzy realistyczny wzorzec sesyjny.
4. Odporność na błędy:
   - Ewentualne błędy wykonania (np. naruszenie klucza obcego przy nieprawidłowym
     INSERT) są przechwytywane bez przerywania pętli. SQL Server Extended Events
     rejestruje je jako zdarzenie error_reported (cecha IsError / ErrorNumber w LOGGER).
5. Bezpieczeństwo i czyszczenie:
   - Wprowadzane dane posiadają unikalny znacznik '@simulation.corp'.
   - Tryb --cleanup pozwala usunąć wszystkie syntetyczne rekordy.
   - Tryb --dry-run pozwala przetestować generowanie zapytań bez bazy danych.

Autor: Data Science & Software Engineering Assistant
Data:  2026-10
===============================================================================
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import re
import string
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# Konfiguracja logowania
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("DBWorkloadExecutor")


# ---------------------------------------------------------------------------
# 1. KONFIGURACJA POŁĄCZENIA I WYKONANIA
# ---------------------------------------------------------------------------

@dataclass
class DBConfig:
    server: str = "localhost"
    database: str = "AdventureWorksLT2025"
    schema: str = "SalesLT"
    driver: Optional[str] = None
    trusted_connection: bool = False
    username: Optional[str] = "CEO_User"
    password: Optional[str] = "CeoPass2026!Secure#"
    trust_server_certificate: bool = True
    login_timeout: int = 5


@dataclass
class ExecutionConfig:
    delay_sec: float = 0.05
    tag_normal: str = "NORMAL_TRAFFIC"
    dry_run: bool = False
    limit: Optional[int] = None
    cleanup: bool = False
    simulate_user_as_ceo: bool = True


# ---------------------------------------------------------------------------
# 2. GENERATOR REALISTYCZNYCH ZAPYTAŃ SQL (AdventureWorksLT)
# ---------------------------------------------------------------------------

class SQLQueryBuilder:
    """
    Buduje syntaktycznie poprawne zapytania SQL Server dla tabel:
    - Customer
    - SalesOrderHeader
    - Product
    Zgodnie ze schematem bazy AdventureWorksLT.
    """

    FIRST_NAMES = [
        "James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael",
        "Linda", "William", "Elizabeth", "David", "Barbara", "Richard", "Susan",
        "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen", "Christopher"
    ]
    LAST_NAMES = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
        "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin"
    ]
    COMPANIES = [
        "Apex Global Logistics", "Quantum Innovations", "Summit Financial",
        "Vanguard Retail Group", "Blue Horizon Tech", "Pinnacle Capital",
        "Cascade Media Solutions", "Starlight Industries", "Vertex Dynamics",
        "OmniCorp Systems", "AeroTech Components", "Titan Supply Chain"
    ]
    COLORS = ["Black", "Blue", "Red", "Silver", "Yellow", "Multi"]

    def __init__(self, schema: str = "SalesLT"):
        self.schema = schema

    def build_query(
        self,
        operation_type: str,
        target_table: str,
        scenario_id: Optional[str],
        tag_normal: str = "NORMAL_TRAFFIC",
        user_name: str = "CEO_User",
        client_hostname: str = "CEO-DESKTOP",
        simulated_ip: str = "10.0.4.15",
        simulated_timestamp: Optional[str] = None,
    ) -> str:
        """
        Zwraca pełne zapytanie SQL.
        WAŻNE: Rozpoczyna się bezpośrednio od słowa kluczowego (SELECT / INSERT),
        a znacznik /* [ScenarioID:<id>|User:...|Host:...|IP:...|SimTime:...] */ dołączany jest na końcu zapytania.
        """
        effective_scenario = scenario_id if scenario_id and pd.notna(scenario_id) else tag_normal
        sim_time_tag = f"|SimTime:{simulated_timestamp}" if simulated_timestamp else ""
        scenario_comment = f"/* [ScenarioID:{effective_scenario}|User:{user_name}|Host:{client_hostname}|IP:{simulated_ip}{sim_time_tag}] */"

        op = operation_type.upper().strip()
        table = target_table.strip()

        if op == "SELECT":
            sql_body = self._build_select(table)
        elif op == "INSERT":
            sql_body = self._build_insert(table)
        else:
            sql_body = f"SELECT 1 AS Heartbeat FROM {self.schema}.{table}"

        # Konstrukcja końcowa:
        # np. SELECT TOP 10 ... FROM SalesLT.Customer /* [ScenarioID:ANOM_TIME_123] */;
        return f"{sql_body.rstrip(';')} {scenario_comment};"

    def _build_select(self, table: str) -> str:
        """Generuje różnorodne, realistyczne zapytania analityczne CEO."""
        full_table = f"{self.schema}.{table}"

        if table.lower() == "customer":
            templates = [
                f"SELECT TOP 10 CustomerID, FirstName, LastName, CompanyName, EmailAddress, Phone FROM {full_table} ORDER BY CustomerID DESC",
                f"SELECT CustomerID, FirstName, LastName, CompanyName, SalesPerson FROM {full_table} WHERE CustomerID = {random.randint(1, 30118)}",
                f"SELECT TOP 25 CustomerID, CompanyName, EmailAddress FROM {full_table} WHERE CompanyName LIKE '{random.choice(string.ascii_uppercase)}%'",
                f"SELECT SalesPerson, COUNT(*) AS TotalClients FROM {full_table} WHERE SalesPerson IS NOT NULL GROUP BY SalesPerson",
                f"SELECT TOP 15 CustomerID, FirstName, LastName, ModifiedDate FROM {full_table} ORDER BY ModifiedDate DESC",
            ]
            return random.choice(templates)

        elif table.lower() == "salesorderheader":
            templates = [
                f"SELECT TOP 10 SalesOrderID, OrderDate, DueDate, Status, TotalDue, CustomerID FROM {full_table} ORDER BY OrderDate DESC",
                f"SELECT COUNT(*) AS OrderCount, SUM(TotalDue) AS TotalRevenue, AVG(TotalDue) AS AvgOrderValue FROM {full_table}",
                f"SELECT TOP 15 SalesOrderID, SalesOrderNumber, SubTotal, TaxAmt, Freight, TotalDue FROM {full_table} WHERE TotalDue > {random.randint(100, 5000)}",
                f"SELECT Status, COUNT(*) AS StatusCount, SUM(TotalDue) AS StatusRevenue FROM {full_table} GROUP BY Status",
                f"SELECT TOP 5 SalesOrderID, CustomerID, TotalDue FROM {full_table} WHERE CustomerID = {random.randint(1, 30118)}",
            ]
            return random.choice(templates)

        elif table.lower() == "product":
            templates = [
                f"SELECT TOP 20 ProductID, Name, ProductNumber, Color, StandardCost, ListPrice FROM {full_table} ORDER BY ListPrice DESC",
                f"SELECT ProductID, Name, ListPrice FROM {full_table} WHERE ListPrice > {random.randint(50, 1500)} ORDER BY ListPrice DESC",
                f"SELECT ProductCategoryID, COUNT(*) AS ItemCount, AVG(ListPrice) AS AvgPrice FROM {full_table} GROUP BY ProductCategoryID",
                f"SELECT TOP 10 ProductID, Name, StandardCost, ListPrice, Color FROM {full_table} WHERE Color = '{random.choice(self.COLORS)}'",
                f"SELECT ProductID, Name, ProductNumber, ModifiedDate FROM {full_table} WHERE ProductID = {random.randint(680, 999)}",
            ]
            return random.choice(templates)

        else:
            return f"SELECT TOP 10 * FROM {full_table}"

    def _build_insert(self, table: str) -> str:
        """
        Generuje zapytanie INSERT.
        Dla Customer: tworzy poprawne rekordy (spełniające NOT NULL dla Haszy i Soli).
        Dla innych tabel (anomalia/szum): generuje próbę zapisu (może wywołać błąd FK/uprawnień).
        """
        full_table = f"{self.schema}.{table}"

        if table.lower() == "customer":
            first = random.choice(self.FIRST_NAMES)
            last = random.choice(self.LAST_NAMES)
            company = f"SIM_{random.choice(self.COMPANIES)}"
            sim_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
            email = f"ceo_sim_{sim_id}@simulation.corp"
            phone = f"{random.randint(100,999)}-555-{random.randint(1000,9999)}"
            dummy_hash = "c2hhMjU2X2R1bW15X3Bhc3N3b3JkX2hhc2hfZm9yX3NpbXVsYXRpb25f"
            dummy_salt = "c2FsdDEyMw"

            return (
                f"INSERT INTO {full_table} "
                f"(NameStyle, FirstName, LastName, CompanyName, EmailAddress, Phone, PasswordHash, PasswordSalt, ModifiedDate) "
                f"VALUES (0, '{first}', '{last}', '{company}', '{email}', '{phone}', '{dummy_hash}', '{dummy_salt}', GETDATE())"
            )

        elif table.lower() == "product":
            # Nietypowa anomalia zapisu do tabeli Product przez CEO
            rand_code = random.randint(1000, 9999)
            return (
                f"INSERT INTO {full_table} "
                f"(Name, ProductNumber, StandardCost, ListPrice, SellStartDate) "
                f"VALUES ('SimProduct_{rand_code}', 'SIM-{rand_code}', 10.00, 25.00, GETDATE())"
            )

        elif table.lower() == "salesorderheader":
            # Próba zapisu do nagłówka zamówień
            return (
                f"INSERT INTO {full_table} "
                f"(DueDate, CustomerID, ShipMethod, SubTotal, TaxAmt, Freight) "
                f"VALUES (GETDATE()+7, 1, 'CARGO DELUXE', 150.00, 12.00, 5.00)"
            )

        else:
            return f"INSERT INTO {full_table} DEFAULT VALUES"


# ---------------------------------------------------------------------------
# 3. ZARZĄDZANIE POŁĄCZENIAMI ODBC I SESJAMI
# ---------------------------------------------------------------------------

class SessionConnectionManager:
    """
    Zarządza pulą połączeń do bazy danych SQL Server.
    Każde połączenie jest konfigurowane pod kątem urządzenia i sesji:
    - WSID = device_context (np. Office_PC, Home_PC)
    - APP  = 'CEO_Client;Device=<dev>;ScenarioID=<scen>'
    Dzięki temu Extended Events poprawnie przypisuje client_hostname oraz client_app_name.
    """

    def __init__(self, db_cfg: DBConfig):
        self.cfg = db_cfg
        self._driver = self._resolve_driver()
        self._active_connections: Dict[str, any] = {}

    def _resolve_driver(self) -> str:
        """Wybiera najlepszy dostępny sterownik ODBC."""
        if self.cfg.driver:
            return self.cfg.driver

        try:
            import pyodbc
            drivers = pyodbc.drivers()
            for preferred in [
                "ODBC Driver 18 for SQL Server",
                "ODBC Driver 17 for SQL Server",
                "SQL Server",
            ]:
                if preferred in drivers:
                    return preferred
            if drivers:
                return drivers[0]
        except Exception:
            pass

        return "ODBC Driver 17 for SQL Server"

    def build_connection_string(
        self,
        client_hostname: str,
        device_context: str,
        simulated_ip: str,
        user_name: str,
        scenario_id: Optional[str] = None,
        is_anomaly: int = 0,
    ) -> str:
        """Tworzy parametryzowany Connection String z kontekstem urządzenia i Ground Truth."""
        scen_tag = scenario_id if scenario_id and pd.notna(scenario_id) else "NORMAL_TRAFFIC"
        app_name = f"CEO_Client;User={user_name};Dev={device_context};IP={simulated_ip};ScenarioID={scen_tag};IsAnomaly={is_anomaly}"

        parts = [
            f"Driver={{{self._driver}}}",
            f"Server={self.cfg.server}",
            f"Database={self.cfg.database}",
            f"APP={app_name}",
            f"WSID={client_hostname}",
            f"LoginTimeout={self.cfg.login_timeout}",
        ]

        if self.cfg.trusted_connection:
            parts.append("Trusted_Connection=yes")
        else:
            if self.cfg.username:
                parts.append(f"UID={self.cfg.username}")
            if self.cfg.password:
                parts.append(f"PWD={self.cfg.password}")

        if "18" in self._driver or self.cfg.trust_server_certificate:
            parts.append("TrustServerCertificate=yes")

        return ";".join(parts) + ";"

    def get_connection(
        self,
        session_id: str,
        client_hostname: str,
        device_context: str,
        simulated_ip: str,
        user_name: str,
        scenario_id: Optional[str] = None,
        is_anomaly: int = 0,
    ):
        """Pobiera istniejące lub otwiera nowe połączenie powiązane z sesją i hostem."""
        import pyodbc

        key = f"{session_id}_{client_hostname}"
        if key in self._active_connections:
            conn = self._active_connections[key]
            try:
                # Szybki test żywotności połączenia
                conn.cursor().execute("SELECT 1")
                return conn
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                del self._active_connections[key]

        conn_str = self.build_connection_string(
            client_hostname=client_hostname,
            device_context=device_context,
            simulated_ip=simulated_ip,
            user_name=user_name,
            scenario_id=scenario_id,
            is_anomaly=is_anomaly,
        )
        conn = pyodbc.connect(conn_str, autocommit=True)
        self._active_connections[key] = conn
        return conn

    def close_all(self):
        """Zamyka wszystkie aktywne połączenia w puli."""
        for key, conn in list(self._active_connections.items()):
            try:
                conn.close()
            except Exception:
                pass
        self._active_connections.clear()


# ---------------------------------------------------------------------------
# 4. GŁÓWNY SILNIK WYKONANIA (WORKLOAD EXECUTOR)
# ---------------------------------------------------------------------------

class DBWorkloadExecutor:
    """
    Odczytuje pliki CSV i wykonuje sekwencję zapytań na bazie danych SQL Server,
    tak aby sesja Extended Events mogła zapisać zdarzenia do pliku .xel.
    """

    def __init__(self, db_cfg: DBConfig, exec_cfg: ExecutionConfig):
        self.db_cfg = db_cfg
        self.exec_cfg = exec_cfg
        self.query_builder = SQLQueryBuilder(schema=db_cfg.schema)
        self.conn_mgr = SessionConnectionManager(db_cfg)

    def execute_csv(self, csv_path: Path | str) -> dict:
        """Wykonuje operacje z pojedynczego pliku CSV."""
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"Plik CSV nie istnieje: {path}")

        logger.info(f"Wczytywanie logów z: {path.name}")
        df = pd.read_csv(path)

        # Sprawdzenie wymaganych kolumn
        required_cols = [
            "operation_type", "target_table", "device_context", "session_id"
        ]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ValueError(f"W pliku CSV brakuje kolumn: {missing}")

        if self.exec_cfg.limit:
            df = df.iloc[:self.exec_cfg.limit]
            logger.info(f"Ograniczono wykonanie do {self.exec_cfg.limit} rekordów.")

        stats = {
            "total": len(df),
            "executed": 0,
            "errors": 0,
            "selects": 0,
            "inserts": 0,
            "anomalies": 0,
            "start_time": datetime.now(),
        }

        auth_desc = self.db_cfg.username if not self.db_cfg.trusted_connection else "Windows Authentication"
        logger.info(f"Konto logowania do SQL Server: {auth_desc} (Baza: {self.db_cfg.database})")
        logger.info(f"Rozpoczynanie egzekucji {len(df)} operacji bazodanowych...")

        for idx, row in df.iterrows():
            op_type = str(row["operation_type"])
            table = str(row["target_table"])
            device = str(row["device_context"])
            hostname = str(row["client_hostname"]) if "client_hostname" in row and pd.notna(row["client_hostname"]) else device
            simulated_ip = str(row["simulated_ip"]) if "simulated_ip" in row and pd.notna(row["simulated_ip"]) else "10.0.4.15"
            login_user = str(row["login_name"]) if "login_name" in row and pd.notna(row["login_name"]) else str(row.get("user", "CEO_User"))
            session_id = str(row["session_id"])
            scenario_id = str(row["scenario_id"]) if pd.notna(row.get("scenario_id")) else ""
            is_anomaly = int(row["is_anomaly"]) if "is_anomaly" in row and pd.notna(row["is_anomaly"]) else 0
            sim_timestamp = str(row["timestamp"]) if "timestamp" in row and pd.notna(row["timestamp"]) else None

            if is_anomaly == 1:
                stats["anomalies"] += 1

            # Przygotowanie zapytania z etykietą scenariusza i metadanymi
            sql_query = self.query_builder.build_query(
                operation_type=op_type,
                target_table=table,
                scenario_id=scenario_id,
                tag_normal=self.exec_cfg.tag_normal,
                user_name=login_user,
                client_hostname=hostname,
                simulated_ip=simulated_ip,
                simulated_timestamp=sim_timestamp,
            )

            if op_type.upper() == "SELECT":
                stats["selects"] += 1
            elif op_type.upper() == "INSERT":
                stats["inserts"] += 1

            if self.exec_cfg.dry_run:
                if idx < 5 or idx % 50 == 0:
                    logger.info(f"[DRY-RUN #{idx+1}] Host:{hostname} (IP:{simulated_ip}) | {sql_query}")
                stats["executed"] += 1
                continue

            # Realne wykonanie zapytania
            try:
                conn = self.conn_mgr.get_connection(
                    session_id=session_id,
                    client_hostname=hostname,
                    device_context=device,
                    simulated_ip=simulated_ip,
                    user_name=login_user,
                    scenario_id=scenario_id,
                    is_anomaly=is_anomaly,
                )
                cursor = conn.cursor()
                cursor.execute(sql_query)

                # Dla SELECT konsumujemy wyniki, aby zakończyć batch
                if op_type.upper() == "SELECT":
                    try:
                        cursor.fetchall()
                    except Exception:
                        pass
                cursor.close()
                stats["executed"] += 1

            except Exception as ex:
                stats["errors"] += 1
                # Błędy są normalnym elementem anomalii i zostaną zarejestrowane
                # w Extended Events jako error_reported
                logger.debug(f"[SQL INFO/WARN #{idx+1}] Zapytanie: {sql_query} | Info: {ex}")

            if idx > 0 and (idx + 1) % 50 == 0:
                logger.info(f"Postęp: {idx+1}/{len(df)} zapytań wykonanych.")

            if self.exec_cfg.delay_sec > 0:
                time.sleep(self.exec_cfg.delay_sec)

        self.conn_mgr.close_all()
        stats["end_time"] = datetime.now()
        stats["duration_sec"] = (stats["end_time"] - stats["start_time"]).total_seconds()

        self._print_execution_summary(path.name, stats)
        return stats

    def cleanup_simulation_data(self) -> int:
        """Usuwa dane wprowadzone podczas symulacji z tabeli SalesLT.Customer."""
        if self.exec_cfg.dry_run:
            logger.info("[DRY-RUN] Pominięto czyszczenie bazy.")
            return 0

        cleanup_sql = f"DELETE FROM {self.db_cfg.schema}.Customer WHERE EmailAddress LIKE '%@simulation.corp';"
        try:
            import pyodbc
            conn_str = self.conn_mgr.build_connection_string("Office_PC", "CLEANUP")
            with pyodbc.connect(conn_str, autocommit=True) as conn:
                cursor = conn.cursor()
                cursor.execute(cleanup_sql)
                deleted_rows = cursor.rowcount
                logger.info(f"Wyczyszczono bazę: usunięto {deleted_rows} wierszy symulacyjnych.")
                return deleted_rows
        except Exception as ex:
            logger.error(f"Błąd podczas czyszczenia bazy: {ex}")
            return -1

    @staticmethod
    def _print_execution_summary(name: str, stats: dict):
        """Prezentuje zwięzłe podsumowanie wykonania."""
        logger.info("=" * 65)
        logger.info(f" PODSUMOWANIE WYKONANIA: {name}")
        logger.info("=" * 65)
        logger.info(f" Całkowita liczba wierszy w CSV:   {stats['total']}")
        logger.info(f" Zapytania wysłane do serwera:    {stats['executed']}")
        logger.info(f" Zapytania SELECT:                {stats['selects']}")
        logger.info(f" Zapytania INSERT:                {stats['inserts']}")
        logger.info(f" Zdarzenia anomalne (etykietowane):{stats['anomalies']}")
        logger.info(f" Zgłoszone błędy wykonania:       {stats['errors']}")
        logger.info(f" Czas całkowity wykonania:        {stats['duration_sec']:.2f} s")
        logger.info("=" * 65)


# ---------------------------------------------------------------------------
# 5. CLI I INTERFEJS UŻYTKOWNIKA
# ---------------------------------------------------------------------------

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
