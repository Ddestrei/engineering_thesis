from dataclasses import dataclass
from typing import Optional

#Przechowuje parametry wykonawcze dotyczące egzekucji zapytań SQL na rzeczywistej bazie danych.

@dataclass
class ExecutionConfig:
    delay_sec: float = 0.05
    tag_normal: str = "NORMAL_TRAFFIC"
    dry_run: bool = False
    limit: Optional[int] = None
    cleanup: bool = False
    simulate_user_as_ceo: bool = True