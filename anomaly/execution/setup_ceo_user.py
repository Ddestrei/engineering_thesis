"""
Skrypt pomocniczy do automatycznego utworzenia użytkownika i loginu CEO_User w SQL Server.
Wykonuje skrypt sql_files/create_ceo_user.sql za pośrednictwem Windows Authentication (jako administrator).

Hasło loginu nie jest zapisane w kodzie. Pobierane jest ze zmiennej środowiskowej
ANOMALY_DB_PASSWORD_CEO_USER albo z config.local.json i podstawiane w miejsce
zmiennej SQLCMD $(CEO_PASSWORD).

Uruchomienie (z katalogu głównego repozytorium):
    python -m anomaly.execution.setup_ceo_user
"""
import re
import sys

import pyodbc

from anomaly.paths import SQL_DIR
from anomaly.settings import get_setting, require_db_password

SQL_FILE = SQL_DIR / "create_ceo_user.sql"
LOGIN = "CEO_User"

# Separator batchy T-SQL: linia zawierająca wyłącznie "GO" (bez rozróżniania wielkości liter).
GO_SEPARATOR = re.compile(r"^\s*GO\s*$", flags=re.IGNORECASE | re.MULTILINE)


def get_connection(server: str):
    drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    driver = "ODBC Driver 17 for SQL Server" if "ODBC Driver 17 for SQL Server" in drivers else (drivers[0] if drivers else "SQL Server")

    conn_str = f"Driver={{{driver}}};Server={server};Database=master;Trusted_Connection=yes;TrustServerCertificate=yes;"
    return pyodbc.connect(conn_str, autocommit=True)


def main():
    server = get_setting("server", "localhost")
    password = require_db_password(LOGIN)
    # Hasło trafia do literału N'...' w T-SQL, więc apostrofy trzeba podwoić.
    password_sql = password.replace("'", "''")

    print(f"Łączenie z serwerem SQL ({server}) za pomocą Windows Authentication...")
    try:
        conn = get_connection(server)
        print("Połączono pomyślnie.")
    except Exception as e:
        print(f"BŁĄD: Nie można połączyć się z serwerem SQL: {e}")
        print("Upewnij się, że usługa SQL Server jest uruchomiona.")
        sys.exit(1)

    sql_text = SQL_FILE.read_text(encoding="utf-8").replace("$(CEO_PASSWORD)", password_sql)
    batches = [b.strip() for b in GO_SEPARATOR.split(sql_text) if b.strip()]

    cursor = conn.cursor()
    errors = 0
    for idx, batch in enumerate(batches, 1):
        try:
            cursor.execute(batch)
        except Exception as ex:
            errors += 1
            print(f"Błąd w batchu {idx}: {ex}")

    cursor.close()
    conn.close()
    if errors:
        print(f"Konfiguracja użytkownika [{LOGIN}] zakończona z {errors} błędami.")
        sys.exit(1)
    print(f"Konfiguracja użytkownika [{LOGIN}] zakończona sukcesem!")


if __name__ == "__main__":
    main()
