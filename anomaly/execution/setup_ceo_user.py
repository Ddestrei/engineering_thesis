"""
Skrypt pomocniczy do automatycznego utworzenia użytkownika i loginu CEO_User w SQL Server.
Wykonuje skrypt create_ceo_user.sql za pośrednictwem Windows Authentication (jako administrator).
"""
import sys
from pathlib import Path
import pyodbc

SERVER = "localhost"
SQL_FILE = Path(__file__).parent / "create_ceo_user.sql"

def get_connection():
    drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    driver = "ODBC Driver 17 for SQL Server" if "ODBC Driver 17 for SQL Server" in drivers else (drivers[0] if drivers else "SQL Server")
    
    conn_str = f"Driver={{{driver}}};Server={SERVER};Database=master;Trusted_Connection=yes;TrustServerCertificate=yes;"
    return pyodbc.connect(conn_str, autocommit=True)

def main():
    print(f"Łączenie z serwerem SQL ({SERVER}) za pomocą Windows Authentication...")
    try:
        conn = get_connection()
        print("Połączono pomyślnie.")
    except Exception as e:
        print(f"BŁĄD: Nie można połączyć się z serwerem SQL: {e}")
        print("Upewnij się, że usługa SQL Server jest uruchomiona.")
        sys.exit(1)

    sql_text = SQL_FILE.read_text(encoding="utf-8")
    batches = [b.strip() for b in sql_text.split("GO") if b.strip()]

    cursor = conn.cursor()
    for idx, batch in enumerate(batches, 1):
        try:
            cursor.execute(batch)
        except Exception as ex:
            print(f"Błąd w batchu {idx}: {ex}")

    cursor.close()
    conn.close()
    print("Konfiguracja użytkownika [CEO_User] zakończona sukcesem!")

if __name__ == "__main__":
    main()
