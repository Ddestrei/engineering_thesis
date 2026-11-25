from dataclasses import dataclass
from typing import Optional

#Przechowuje konfigurację parametrów połączeniowych do instancji Microsoft SQL Server.

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