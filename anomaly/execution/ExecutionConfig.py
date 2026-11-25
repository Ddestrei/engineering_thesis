from dataclasses import dataclass
from typing import Optional

# Parametry wykonania operacji generatora na bazie danych.


@dataclass
class ExecutionConfig:
    delay_sec: float = 0.0          # odstęp między zapytaniami (czas symulowany pochodzi z generatora, D11)
    dry_run: bool = False           # buduje zapytania bez łączenia z bazą
    limit: Optional[int] = None     # tylko pierwsze N operacji (szybkie próby; przebieg niepełny)
