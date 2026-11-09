import logging
from DBConfig import DBConfig
from ExecutionConfig import ExecutionConfig
from SQLQueryBuilder import SQLQueryBuilder
from SessionConnectionManager import SessionConnectionManager
from pathlib import Path
import pandas as pd
from datetime import datetime
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("DBWorkloadExecutor")


"""
    Silnik egzekucji obciążenia bazodanowego. Odczytuje wygenerowane pliki CSV z operacjami i wysyła je na serwer SQL, 
    aby sesja Extended Events zarejestrowała zdarzenia do pliku
"""
class DBWorkloadExecutor:

    def __init__(self, db_cfg: DBConfig, exec_cfg: ExecutionConfig):
        self.db_cfg = db_cfg
        self.exec_cfg = exec_cfg
        self.query_builder = SQLQueryBuilder(schema=db_cfg.schema)
        self.conn_mgr = SessionConnectionManager(db_cfg)

    """
    iteruje po rekordach pliku CSV, buduje zapytania, pobiera odpowiednie połączenie ODBC, 
    wykonuje zapytanie na serwerze, zlicza statystyki wykonania (sukcesy, błędy, typy operacji) i 
    zachowuje opcjonalne opóźnienie między zapytaniami.
    """
    def execute_csv(self, csv_path: Path | str) -> dict:
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

                if op_type.upper() == "SELECT":
                    try:
                        cursor.fetchall()
                    except Exception:
                        pass
                cursor.close()
                stats["executed"] += 1

            except Exception as ex:
                stats["errors"] += 1
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