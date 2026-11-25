"""
Operacje administracyjne na SQL Server (PLAN Faza 4). Wykonywane przez Windows Authentication
(konto z uprawnieniami sysadmin), nigdy przez loginy symulacji.

- przywrócenie bazy z oryginalnego pliku .bak przed każdym przebiegiem (powtarzalność metryk XE),
- 30 loginów SIM_<ROLA>_<nr> (poziom serwera, wspólne hasło z config.local.json),
- role bazodanowe sim_<rola> z uprawnieniami 1:1 z anomaly/domain.py (dokument „Poprawiony schemat
  użycia”) oraz użytkownicy bazy przypisani do ról; odtwarzane po każdym przywróceniu bazy,
- rekordy startowe przebiegu (seed_records_<split>.csv, METODOLOGIA D20) w kolejności rodzice -> dzieci,
- sesja Extended Events: osobny plik .xel dla każdego przebiegu.

Skrypt SQL ról i użytkowników (bez haseł) do wglądu i do pracy dyplomowej:
    python -m anomaly.execution.db_admin --write-sql      ->  sql_files/sim_roles_and_users.sql
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import Iterable, List, Sequence

import pandas as pd

from anomaly.domain import OPERATIONS, PERMISSIONS, ROLES
from anomaly.execution.SQLQueryBuilder import insert_statement, record_guid
from anomaly.paths import SQL_DIR
from anomaly.settings import get_setting

LEGACY_LOGINS = ("CEO_User",)                # zastąpione loginami SIM_* (decyzja z 25.11.2026)
GO_SEPARATOR = re.compile(r"^\s*GO\s*$", flags=re.IGNORECASE | re.MULTILINE)
XE_SETUP_SQL = SQL_DIR / "Extended_Events_set_up.sql"
ROLES_SQL = SQL_DIR / "sim_roles_and_users.sql"
SEED_BATCH = 100                              # instrukcji INSERT w jednym batchu


def role_name(role: str) -> str:
    return f"sim_{role.lower()}"


def _ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError(f"Niedozwolona nazwa obiektu: {name!r}")
    return f"[{name}]"


# --- połączenie administracyjne -------------------------------------------------------------------------

def _driver() -> str:
    import pyodbc
    drivers = pyodbc.drivers()
    for preferred in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server"):
        if preferred in drivers:
            return preferred
    raise RuntimeError("Brak sterownika ODBC dla SQL Server.")


def admin_connect(database: str = "master"):
    import pyodbc
    server = get_setting("server")
    conn_str = (f"Driver={{{_driver()}}};Server={server};Database={database};Trusted_Connection=yes;"
                f"TrustServerCertificate=yes;APP=SIM-ADMIN;")
    return pyodbc.connect(conn_str, autocommit=True, timeout=30)


def _drain(cursor) -> None:
    """Odbiera wszystkie wyniki/komunikaty (RESTORE i batch-e wielu instrukcji kończą się dopiero wtedy)."""
    while cursor.nextset():
        pass


def run_script(sql: str, database: str = "master") -> None:
    """Wykonuje skrypt z separatorami GO."""
    with admin_connect(database) as conn:
        cur = conn.cursor()
        for batch in (b.strip() for b in GO_SEPARATOR.split(sql)):
            if batch:
                cur.execute(batch)
                _drain(cur)


# --- skrypty SQL --------------------------------------------------------------------------------------

def logins_sql(logins: Sequence[str], password: str, database: str) -> str:
    """Loginy serwera (tworzone lub z aktualizacją hasła) i usunięcie loginów zastąpionych."""
    pwd = password.replace("'", "''")
    lines = []
    for login in logins:
        name = _ident(login)
        lines.append(
            f"IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'{login}')\n"
            f"    CREATE LOGIN {name} WITH PASSWORD = N'{pwd}', DEFAULT_DATABASE = {_ident(database)}, "
            f"CHECK_EXPIRATION = OFF, CHECK_POLICY = OFF;\n"
            f"ELSE\n"
            f"    ALTER LOGIN {name} WITH PASSWORD = N'{pwd}', DEFAULT_DATABASE = {_ident(database)}, "
            f"CHECK_EXPIRATION = OFF, CHECK_POLICY = OFF;")
    for login in LEGACY_LOGINS:
        lines.append(f"IF EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'{login}')\n"
                     f"    DROP LOGIN {_ident(login)};")
    return "\n".join(lines)


def principals_sql(users: pd.DataFrame) -> str:
    """Role bazodanowe z uprawnieniami z domain.PERMISSIONS i użytkownicy bazy (idempotentnie)."""
    lines: List[str] = ["-- Role i uprawnienia (anomaly/domain.py, dokument „Poprawiony schemat użycia”)."]
    for role in ROLES:
        rname = role_name(role)
        lines.append(f"IF DATABASE_PRINCIPAL_ID(N'{rname}') IS NULL CREATE ROLE {_ident(rname)};")
        for op in OPERATIONS:
            for table in sorted(PERMISSIONS[role][op]):
                lines.append(f"GRANT {op} ON [SalesLT].{_ident(table)} TO {_ident(rname)};")
    lines.append("-- Użytkownicy bazy (loginy SIM_*), każdy w dokładnie jednej roli.")
    for login, role in zip(users["login_name"], users["role"]):
        lines.append(f"IF DATABASE_PRINCIPAL_ID(N'{login}') IS NULL CREATE USER {_ident(login)} FOR LOGIN {_ident(login)};")
        lines.append(f"ALTER ROLE {_ident(role_name(role))} ADD MEMBER {_ident(login)};")
    return "\n".join(lines)


def write_roles_sql(users: pd.DataFrame, database: str) -> Path:
    text = (
        "-- Wygenerowane przez: python -m anomaly.execution.db_admin --write-sql\n"
        "-- Hasło loginów jest zmienną SQLCMD (nie jest zapisane w pliku):\n"
        "--   sqlcmd -S localhost -E -v SIM_PASSWORD=\"...\" -i sim_roles_and_users.sql\n"
        "-- run_experiment.py wykonuje te same instrukcje automatycznie.\n"
        "USE [master];\nGO\n"
        + logins_sql(list(users["login_name"]), "$(SIM_PASSWORD)", database)
        + f"\nGO\nUSE {_ident(database)};\nGO\n"
        + principals_sql(users) + "\nGO\n"
    )
    ROLES_SQL.write_text(text, encoding="utf-8")
    return ROLES_SQL


# --- przywracanie bazy i konta ------------------------------------------------------------------------------

def backup_file() -> str:
    configured = get_setting("backup_file")
    if configured:
        return configured
    with admin_connect() as conn:
        path = conn.cursor().execute(
            "SELECT CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS nvarchar(4000))").fetchone()[0]
    return str(Path(path) / f"{get_setting('database')}.bak")


def restore_database() -> float:
    """Przywraca bazę z .bak (REPLACE). Zwraca czas w sekundach."""
    db, bak = get_setting("database"), backup_file()
    t0 = time.perf_counter()
    with admin_connect() as conn:
        cur = conn.cursor()
        if cur.execute("SELECT DB_ID(?)", db).fetchone()[0] is not None:
            cur.execute(f"ALTER DATABASE {_ident(db)} SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
            _drain(cur)
        cur.execute(f"RESTORE DATABASE {_ident(db)} FROM DISK = ? WITH REPLACE, RECOVERY", bak)
        _drain(cur)
        cur.execute(f"ALTER DATABASE {_ident(db)} SET MULTI_USER")
        _drain(cur)
    return time.perf_counter() - t0


def ensure_logins(users: pd.DataFrame, password: str) -> None:
    run_script(logins_sql(list(users["login_name"]), password, get_setting("database")))


def setup_principals(users: pd.DataFrame) -> None:
    run_script(principals_sql(users), database=get_setting("database"))


# --- rekordy startowe -------------------------------------------------------------------------------------

def seed_statements(seeds: pd.DataFrame) -> List[str]:
    """INSERT-y rekordów startowych w kolejności pliku (generator zapisuje rodziców przed dziećmi)."""
    import json
    out = []
    for key, table, parents in zip(seeds["record_key"], seeds["table"], seeds["parent_keys"].fillna("")):
        out.append(insert_statement(table, key, json.loads(parents) if parents else {}))
    return out


def insert_seed_records(seeds: pd.DataFrame) -> dict:
    """Wstawia rekordy startowe jako administrator i sprawdza, że wszystkie istnieją."""
    t0 = time.perf_counter()
    statements = seed_statements(seeds)
    with admin_connect(get_setting("database")) as conn:
        cur = conn.cursor()
        for i in range(0, len(statements), SEED_BATCH):
            cur.execute("SET NOCOUNT ON;\n" + ";\n".join(statements[i:i + SEED_BATCH]) + ";")
            _drain(cur)
        missing = {}
        for table, grp in seeds.groupby("table"):
            guids = [record_guid(k) for k in grp["record_key"]]
            found = 0
            for j in range(0, len(guids), 500):
                chunk = ", ".join(f"'{g}'" for g in guids[j:j + 500])
                found += cur.execute(
                    f"SELECT COUNT(*) FROM [SalesLT].{_ident(table)} WHERE rowguid IN ({chunk})").fetchone()[0]
            if found != len(guids):
                missing[table] = len(guids) - found
    if missing:
        raise RuntimeError(f"Nie wszystkie rekordy startowe zostały wstawione: {missing}")
    return {"seed_records": len(statements), "seconds": round(time.perf_counter() - t0, 2)}


# --- Extended Events -----------------------------------------------------------------------------------

def _xe_name() -> str:
    return get_setting("xe_session")


def ensure_xe_session() -> None:
    """Tworzy (albo odtwarza) sesję XE ze skryptu sql_files/Extended_Events_set_up.sql, jeśli jej nie ma
    lub ma starą konfigurację (mniejszy bufor)."""
    with admin_connect() as conn:
        row = conn.cursor().execute(
            "SELECT max_memory FROM sys.server_event_sessions WHERE name = ?", _xe_name()).fetchone()
    if row is None or row[0] < 32768:
        run_script(XE_SETUP_SQL.read_text(encoding="utf-8"))


def xe_running() -> bool:
    with admin_connect() as conn:
        return bool(conn.cursor().execute(
            "SELECT COUNT(*) FROM sys.dm_xe_sessions WHERE name = ?", _xe_name()).fetchone()[0])


def xe_stop() -> None:
    if xe_running():
        run_script(f"ALTER EVENT SESSION {_ident(_xe_name())} ON SERVER STATE = STOP;")


def xe_start(file_base: str) -> str:
    """Przekierowuje cel event_file sesji na <xe_dir>\\<file_base>.xel i uruchamia sesję.
    Zwraca wzorzec plików do odczytu przez LOGGER."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", file_base):
        raise ValueError(f"Niedozwolona nazwa pliku XE: {file_base!r}")
    xe_stop()
    name = _ident(_xe_name())
    path = str(Path(get_setting("xe_dir")) / f"{file_base}.xel").replace("'", "''")
    with admin_connect() as conn:
        has_target = conn.cursor().execute(
            "SELECT COUNT(*) FROM sys.server_event_session_targets t JOIN sys.server_event_sessions s "
            "ON s.event_session_id = t.event_session_id WHERE s.name = ? AND t.name = 'event_file'",
            _xe_name()).fetchone()[0]
    script = []
    if has_target:
        script.append(f"ALTER EVENT SESSION {name} ON SERVER DROP TARGET package0.event_file;")
    script.append(f"ALTER EVENT SESSION {name} ON SERVER ADD TARGET package0.event_file("
                  f"SET filename = N'{path}', max_file_size = (100), max_rollover_files = (10));")
    script.append(f"ALTER EVENT SESSION {name} ON SERVER STATE = START;")
    run_script("\nGO\n".join(script))
    return str(Path(get_setting("xe_dir")) / f"{file_base}*.xel")


def main(argv: Iterable[str] | None = None) -> int:
    from anomaly.paths import DEFAULT_SEED, users_csv
    parser = argparse.ArgumentParser(description="Operacje administracyjne symulacji.")
    parser.add_argument("--write-sql", action="store_true", help="zapisz sql_files/sim_roles_and_users.sql")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="ziarno, z którego brana jest lista użytkowników")
    args = parser.parse_args(argv)
    if args.write_sql:
        users = pd.read_csv(users_csv(args.seed))
        print(f"Zapisano {write_roles_sql(users, get_setting('database'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
