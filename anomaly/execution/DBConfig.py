from dataclasses import dataclass, field
from typing import Optional

from anomaly.settings import get_setting

# Parametry połączenia z instancją Microsoft SQL Server dla loginów symulacji.
# Login wynika z operacji (kolumna login_name generatora), hasło z anomaly/settings.py.


@dataclass
class DBConfig:
    server: str = field(default_factory=lambda: get_setting("server"))
    database: str = field(default_factory=lambda: get_setting("database"))
    schema: str = "SalesLT"
    driver: Optional[str] = None
    # Hasło nie jest zapisane w kodzie. Gdy None, pobierane jest przy łączeniu ze zmiennej
    # środowiskowej albo z config.local.json (anomaly/settings.py).
    password: Optional[str] = None
    trust_server_certificate: bool = True
    login_timeout: int = 10
