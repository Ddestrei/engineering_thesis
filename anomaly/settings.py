"""
Odczyt ustawień lokalnych: hasła kont symulacyjnych i parametry serwera SQL.

Hasła NIE są zapisane w kodzie. Kolejność źródeł:
1. zmienna środowiskowa (ANOMALY_SIM_PASSWORD dla wspólnego hasła loginów SIM_*,
   ANOMALY_DB_PASSWORD_<LOGIN> dla pojedynczego loginu),
2. plik config.local.json w katalogu głównym repozytorium (jest w .gitignore),
   wzór w pliku config.example.json.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Optional

from anomaly.paths import LOCAL_CONFIG_FILE

SIM_PASSWORD_ENV = "ANOMALY_SIM_PASSWORD"

# Wartości domyślne ustawień (sekcja "settings" w config.local.json je nadpisuje).
DEFAULT_SETTINGS = {
    "server": "localhost",
    "database": "AdventureWorksLT2025",
    "backup_file": None,          # None = <domyślny katalog kopii serwera>\AdventureWorksLT2025.bak
    "xe_session": "AW2025_Behavioral_Anomaly_Session",
    "xe_dir": r"C:\XELogs",       # katalog plików .xel (zapisuje je usługa SQL Server)
}


@lru_cache(maxsize=1)
def _local_config() -> dict:
    if LOCAL_CONFIG_FILE.exists():
        return json.loads(LOCAL_CONFIG_FILE.read_text(encoding="utf-8"))
    return {}


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Ustawienie z sekcji "settings" pliku config.local.json, potem DEFAULT_SETTINGS, potem default."""
    for source in (_local_config().get("settings", {}), DEFAULT_SETTINGS):
        if source.get(key) is not None:
            return source[key]
    return default


def env_var_for_login(login: str) -> str:
    return "ANOMALY_DB_PASSWORD_" + login.upper().replace("-", "_").replace(" ", "_")


def get_db_password(login: str) -> Optional[str]:
    """Hasło dla loginu SQL Server albo None, jeśli nigdzie go nie podano.
    Loginy symulacji (SIM_*) używają wspólnego hasła (decyzja z 25.11.2026)."""
    env_value = os.environ.get(env_var_for_login(login))
    if env_value:
        return env_value
    own = _local_config().get("db_passwords", {}).get(login)
    if own:
        return own
    if login.upper().startswith("SIM_"):
        return get_sim_password()
    return None


def get_sim_password() -> Optional[str]:
    return os.environ.get(SIM_PASSWORD_ENV) or _local_config().get("sim_password")


def require_sim_password() -> str:
    password = get_sim_password()
    if not password:
        raise RuntimeError(
            f"Brak wspólnego hasła loginów symulacji. Ustaw zmienną środowiskową {SIM_PASSWORD_ENV} "
            f"albo wpis \"sim_password\" w config.local.json (wzór: config.example.json)."
        )
    if any(ch in password for ch in "'\";{}"):
        raise RuntimeError("Hasło symulacji nie może zawierać znaków ' \" ; { } (connection string ODBC).")
    return password


def require_db_password(login: str) -> str:
    """Jak get_db_password, ale z czytelnym błędem, gdy hasła brakuje."""
    password = get_db_password(login)
    if not password:
        raise RuntimeError(
            f"Brak hasła dla loginu '{login}'. Ustaw zmienną środowiskową "
            f"{env_var_for_login(login)} (albo {SIM_PASSWORD_ENV} dla loginów SIM_*) "
            f"lub dodaj je w config.local.json (wzór: config.example.json)."
        )
    return password
