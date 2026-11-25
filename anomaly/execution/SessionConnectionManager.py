from typing import Optional
from typing import Dict
from DBConfig import DBConfig
import pandas as pd

"""
Zarządza pulą połączeń ODBC do bazy danych, 
dbając o symulację tożsamości stacji roboczej oraz 
rzekazywanie etykiet Ground Truth do sesji Extended Events.
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

    #tworzy connection string przekazujący parametry WSID oraz APP (nazwa aplikacji zawierająca wstrzyknięty identyfikator scenariusza i flagę anomalii dla Extended Events).
    def build_connection_string(
        self,
        client_hostname: str,
        device_context: str,
        simulated_ip: str,
        user_name: str,
        scenario_id: Optional[str] = None,
        is_anomaly: int = 0,
    ) -> str:
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

    #zwraca istniejące otwarte połączenie dla danej sesji i hosta (weryfikując jego stan przez SELECT 1) lub otwiera nowe połączenie i dodaje je do puli.
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
            user_name=user_name,
            scenario_id=scenario_id,
            is_anomaly=is_anomaly,
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