"""
Odczyt ustawień lokalnych, przede wszystkim haseł do kont symulacyjnych w SQL Server.

Hasła NIE są zapisane w kodzie. Kolejność źródeł:
1. zmienna środowiskowa, np. ANOMALY_DB_PASSWORD_CEO_USER (nazwa loginu wielkimi literami),
2. plik config.local.json w katalogu głównym repozytorium (jest w .gitignore),
   wzór w pliku config.example.json.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Optional

from anomaly.paths import LOCAL_CONFIG_FILE


@lru_cache(maxsize=1)
def _local_config() -> dict:
    if LOCAL_CONFIG_FILE.exists():
        return json.loads(LOCAL_CONFIG_FILE.read_text(encoding="utf-8"))
    return {}


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Zwraca ustawienie z sekcji "settings" pliku config.local.json albo wartość domyślną."""
    return _local_config().get("settings", {}).get(key, default)


def env_var_for_login(login: str) -> str:
    return "ANOMALY_DB_PASSWORD_" + login.upper().replace("-", "_").replace(" ", "_")


def get_db_password(login: str) -> Optional[str]:
    """Hasło dla loginu SQL Server albo None, jeśli nigdzie go nie podano."""
    env_value = os.environ.get(env_var_for_login(login))
    if env_value:
        return env_value
    return _local_config().get("db_passwords", {}).get(login)


def require_db_password(login: str) -> str:
    """Jak get_db_password, ale z czytelnym błędem, gdy hasła brakuje."""
    password = get_db_password(login)
    if not password:
        raise RuntimeError(
            f"Brak hasła dla loginu '{login}'. Ustaw zmienną środowiskową "
            f"{env_var_for_login(login)} albo dodaj je w config.local.json "
            f"(wzór: config.example.json)."
        )
    return password
