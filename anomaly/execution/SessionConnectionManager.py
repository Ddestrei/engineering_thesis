from typing import Dict
from anomaly.execution.DBConfig import DBConfig
from anomaly.settings import require_db_password

"""
Zarządza pulą połączeń ODBC do bazy danych i symuluje tożsamość stacji roboczej
(host w WSID, urządzenie i IP w nazwie aplikacji). Etykiety ground truth NIE są
przekazywane do bazy; łączy je z logami dopiero anomaly/features/xe_join.py.
"""

class SessionConnectionManager:

    def __init__(self, db_cfg: DBConfig):
        self.cfg = db_cfg
        self._driver = self._resolve_driver()
        self._active_connections: Dict[str, any] = {}

    #wybiera najnowszy dostępny w systemie Windows sterownik ODBC.
    def _resolve_driver(self) -> str:
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

    # Tworzy connection string: WSID = nazwa hosta klienta, APP = kontekst urządzenia i sieci.
    def build_connection_string(
        self,
        client_hostname: str,
        device_context: str,
        simulated_ip: str,
        run_id: str,
    ) -> str:
        app_name = self.make_app_name(run_id, device_context, simulated_ip)

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
                password = self.cfg.password or require_db_password(self.cfg.username)
                parts.append(f"PWD={password}")

        if "18" in self._driver or self.cfg.trust_server_certificate:
            parts.append("TrustServerCertificate=yes")

        return ";".join(parts) + ";"

    #zwraca istniejące otwarte połączenie dla danej sesji i hosta (weryfikując jego stan przez SELECT 1) lub otwiera nowe połączenie i dodaje je do puli.
    def get_connection(
        self,
        session_id: str,
        client_hostname: str,
        device_context: str,
        simulated_ip: str,
        run_id: str,
    ):
        import pyodbc

        key = f"{session_id}_{client_hostname}"
        if key in self._active_connections:
            conn = self._active_connections[key]
            try:
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
            run_id=run_id,
        )
        conn = pyodbc.connect(conn_str, autocommit=True)
        self._active_connections[key] = conn
        return conn

    def close_all(self):
        for key, conn in list(self._active_connections.items()):
            try:
                conn.close()
            except Exception:
                pass
        self._active_connections.clear()