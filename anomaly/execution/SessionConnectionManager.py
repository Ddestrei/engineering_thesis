from typing import Dict

from anomaly.execution.DBConfig import DBConfig
from anomaly.settings import require_db_password

"""
Połączenia ODBC symulowanych użytkowników. Jedno połączenie = jedna sesja generatora:
login użytkownika (SIM_<ROLA>_<nr>), host w WSID, urządzenie i IP w nazwie aplikacji.
Połączenie jest zamykane po ostatniej operacji sesji. Etykiety ground truth NIE są
przekazywane do bazy; łączy je z logami dopiero anomaly/features/xe_join.py.
"""


class SessionConnectionManager:

    def __init__(self, db_cfg: DBConfig):
        self.cfg = db_cfg
        self._driver = self._resolve_driver()
        self._active: Dict[str, object] = {}
        self.opened = 0

    # Wybiera najnowszy dostępny w systemie sterownik ODBC.
    def _resolve_driver(self) -> str:
        if self.cfg.driver:
            return self.cfg.driver
        try:
            import pyodbc
            drivers = pyodbc.drivers()
            for preferred in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server"):
                if preferred in drivers:
                    return preferred
            if drivers:
                return drivers[0]
        except Exception:
            pass
        return "ODBC Driver 17 for SQL Server"

    @staticmethod
    def make_app_name(run_id: str, device_context: str, simulated_ip: str) -> str:
        """
        Nazwa aplikacji klienta (client_app_name w Extended Events). Niesie wyłącznie kontekst,
        który w prawdziwym systemie byłby obserwowalny po stronie serwera: przebieg, typ urządzenia
        i adres IP klienta. NIE zawiera etykiet (anomalia / norma, klasa, incydent).

        Format: SIM|run:<run_id>|dev:<device>|ip:<ip>
        Bez znaków ';' i '=', które w connection stringu ODBC ucinałyby wartość.
        Ten sam format odczytuje LOGGER (XeLogReader.cs, ParseAppName).
        """
        fields = {"run": run_id, "dev": device_context, "ip": simulated_ip}
        for key, value in fields.items():
            if any(ch in str(value) for ch in ";=|:{}"):
                raise ValueError(f"Niedozwolony znak w polu APP '{key}': {value!r}")
        return "SIM|" + "|".join(f"{k}:{v}" for k, v in fields.items())

    # Connection string: login użytkownika symulacji, WSID = host klienta, APP = kontekst urządzenia i sieci.
    def build_connection_string(self, login_name: str, client_hostname: str, device_context: str,
                                simulated_ip: str, run_id: str) -> str:
        parts = [
            f"Driver={{{self._driver}}}",
            f"Server={self.cfg.server}",
            f"Database={self.cfg.database}",
            f"APP={self.make_app_name(run_id, device_context, simulated_ip)}",
            f"WSID={client_hostname}",
            f"LoginTimeout={self.cfg.login_timeout}",
            f"UID={login_name}",
            f"PWD={self.cfg.password or require_db_password(login_name)}",
        ]
        if "18" in self._driver or self.cfg.trust_server_certificate:
            parts.append("TrustServerCertificate=yes")
        return ";".join(parts) + ";"

    def get_connection(self, session_id: str, login_name: str, client_hostname: str, device_context: str,
                       simulated_ip: str, run_id: str):
        """Połączenie sesji (otwierane przy pierwszej operacji). Bez zapytań kontrolnych typu SELECT 1:
        każde zapytanie w logu XE pochodzi z generatora."""
        conn = self._active.get(session_id)
        if conn is None:
            import pyodbc
            if pyodbc.pooling:              # wyłączana w anomaly/execution/__init__.py przed 1. połączeniem
                raise RuntimeError("Pula połączeń ODBC jest włączona: sesje generatora współdzieliłyby połączenia.")
            conn = pyodbc.connect(self.build_connection_string(
                login_name, client_hostname, device_context, simulated_ip, run_id), autocommit=True)
            self._active[session_id] = conn
            self.opened += 1
        return conn

    def close(self, session_id: str) -> None:
        conn = self._active.pop(session_id, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def close_all(self) -> None:
        for session_id in list(self._active):
            self.close(session_id)
