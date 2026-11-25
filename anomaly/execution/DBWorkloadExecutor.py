"""
Wykonanie operacji generatora na SQL Server (PLAN Faza 4), żeby sesja Extended Events
zarejestrowała je w pliku .xel.

- operacje wykonywane są w kolejności czasu symulowanego (kolumna timestamp, D11),
- każda sesja generatora to osobne połączenie: login użytkownika (SIM_<ROLA>_<nr>), host w WSID,
  urządzenie i IP w nazwie aplikacji; połączenie zamykane jest po ostatniej operacji sesji,
- każdy zapis (INSERT / UPDATE / DELETE) musi zmienić dokładnie jeden wiersz; inaczej rekord startowy
  albo rodzic nie istnieje i przebieg jest błędny (tak samo jak błąd SQL, D3),
- baza musi być wcześniej przywrócona z .bak i zasilona rekordami startowymi (run_experiment.py).
"""
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set

import pandas as pd

from anomaly.domain import SELECT
from anomaly.execution.DBConfig import DBConfig
from anomaly.execution.ExecutionConfig import ExecutionConfig
from anomaly.execution.SessionConnectionManager import SessionConnectionManager
from anomaly.execution.SQLQueryBuilder import SQLQueryBuilder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("DBWorkloadExecutor")

REQUIRED_COLUMNS = [
    "event_uid", "run_id", "timestamp", "session_id", "role", "login_name", "user", "operation_type",
    "target_table", "record_key", "parent_keys", "device_context", "client_hostname", "simulated_ip",
]
MAX_LOGGED_ERRORS = 20


def rare_tables_by_user(users: Optional[pd.DataFrame]) -> Dict[str, Set[str]]:
    if users is None:
        return {}
    return {u: set(str(t).split(";")) if isinstance(t, str) and t else set()
            for u, t in zip(users["user_id"], users["rare_tables"])}


def load_events(path: Path | str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"record_key": "string", "parent_keys": "string", "event_uid": "string"},
                     keep_default_na=False)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"W pliku {path} brakuje kolumn: {missing}")
    return df


class DBWorkloadExecutor:

    def __init__(self, db_cfg: DBConfig, exec_cfg: ExecutionConfig):
        self.db_cfg = db_cfg
        self.exec_cfg = exec_cfg
        self.query_builder = SQLQueryBuilder(schema=db_cfg.schema)
        self.conn_mgr = SessionConnectionManager(db_cfg)

    def build_queries(self, events: pd.DataFrame, users: Optional[pd.DataFrame] = None) -> pd.Series:
        """Zapytania dla wszystkich operacji (bez łączenia z bazą)."""
        rare = rare_tables_by_user(users)
        return pd.Series([
            self.query_builder.build_query(op, table, uid, key, parents, role, rare.get(user, ()))
            for op, table, uid, key, parents, role, user in zip(
                events["operation_type"], events["target_table"], events["event_uid"], events["record_key"],
                events["parent_keys"], events["role"], events["user"])
        ], index=events.index)

    def execute_events(self, events: pd.DataFrame, users: Optional[pd.DataFrame] = None, name: str = "") -> dict:
        df = events.sort_values(["timestamp", "event_uid"], kind="stable").reset_index(drop=True)
        if self.exec_cfg.limit:
            df = df.iloc[:self.exec_cfg.limit]
            logger.warning(f"Ograniczono wykonanie do {self.exec_cfg.limit} operacji (przebieg niepełny).")
        queries = self.build_queries(df, users)
        last_of_session = set(df.groupby("session_id").tail(1).index)

        stats = {"name": name, "total": len(df), "executed": 0, "errors": 0, "rowcount_mismatches": 0,
                 "by_operation": df["operation_type"].value_counts().to_dict(),
                 "sessions": int(df["session_id"].nunique()), "error_samples": [],
                 "start_time": datetime.now().isoformat(timespec="seconds")}
        logger.info(f"{name}: {len(df)} operacji w {stats['sessions']} sesjach "
                    f"({'DRY-RUN' if self.exec_cfg.dry_run else self.db_cfg.server + '/' + self.db_cfg.database})")
        t0 = time.perf_counter()

        for idx, row in df.iterrows():
            sql = queries[idx]
            if self.exec_cfg.dry_run:
                if idx < 5:
                    app = self.conn_mgr.make_app_name(row["run_id"], row["device_context"], row["simulated_ip"])
                    logger.info(f"[DRY-RUN] {row['login_name']} WSID={row['client_hostname']} APP={app} | {sql}")
                stats["executed"] += 1
                continue

            try:
                conn = self.conn_mgr.get_connection(row["session_id"], row["login_name"], row["client_hostname"],
                                                    row["device_context"], row["simulated_ip"], row["run_id"])
                cursor = conn.cursor()
                cursor.execute(sql)
                if row["operation_type"] == SELECT:
                    cursor.fetchall()
                elif cursor.rowcount != 1:
                    stats["rowcount_mismatches"] += 1
                    self._log_problem(stats, f"{row['event_uid']}: {row['operation_type']} {row['target_table']} "
                                             f"zmienił {cursor.rowcount} wierszy (oczekiwano 1)")
                cursor.close()
                stats["executed"] += 1
            except Exception as ex:
                # Generator tworzy wyłącznie operacje zgodne z uprawnieniami (METODOLOGIA D3),
                # więc każdy błąd SQL to błąd symulacji. Walidacja (xe_join) odrzuci przebieg.
                stats["errors"] += 1
                self._log_problem(stats, f"{row['event_uid']}: {ex}")

            if idx in last_of_session:
                self.conn_mgr.close(row["session_id"])
            if (idx + 1) % 2000 == 0:
                rate = (idx + 1) / (time.perf_counter() - t0)
                logger.info(f"Postęp: {idx + 1}/{len(df)} ({rate:.0f} zapytań/s)")
            if self.exec_cfg.delay_sec > 0:
                time.sleep(self.exec_cfg.delay_sec)

        self.conn_mgr.close_all()
        stats["duration_sec"] = round(time.perf_counter() - t0, 2)
        stats["queries_per_sec"] = round(stats["executed"] / stats["duration_sec"], 1) if stats["duration_sec"] else None
        stats["connections_opened"] = self.conn_mgr.opened
        stats["ok"] = stats["errors"] == 0 and stats["rowcount_mismatches"] == 0 and stats["executed"] == stats["total"]
        self._print_summary(stats)
        return stats

    def execute_csv(self, csv_path: Path | str, users_path: Path | str | None = None) -> dict:
        path = Path(csv_path)
        users = pd.read_csv(users_path, keep_default_na=False) if users_path else None
        return self.execute_events(load_events(path), users, name=path.name)

    @staticmethod
    def _log_problem(stats: dict, message: str) -> None:
        if len(stats["error_samples"]) < MAX_LOGGED_ERRORS:
            stats["error_samples"].append(message)
            logger.warning(message)

    @staticmethod
    def _print_summary(stats: dict) -> None:
        logger.info("=" * 65)
        logger.info(f" PODSUMOWANIE WYKONANIA: {stats['name']}")
        logger.info(f" Operacje: {stats['executed']}/{stats['total']}  {stats['by_operation']}")
        logger.info(f" Sesje (połączenia): {stats['sessions']}")
        logger.info(f" Błędy SQL: {stats['errors']}   Zapisy z liczbą wierszy != 1: {stats['rowcount_mismatches']}")
        logger.info(f" Czas: {stats['duration_sec']} s ({stats['queries_per_sec']} zapytań/s)")
        logger.info(" WYNIK: " + ("OK" if stats["ok"] else "BŁĘDY – przebieg do odrzucenia"))
        logger.info("=" * 65)
