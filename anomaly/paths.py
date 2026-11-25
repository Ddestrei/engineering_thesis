"""
Wspólne ścieżki projektu.

Wszystkie ścieżki liczone są względem katalogu głównego repozytorium,
dzięki czemu skrypty działają niezależnie od katalogu, z którego zostały uruchomione.
"""
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[1]

SQL_DIR: Path = REPO_ROOT / "sql_files"

# Dane i wyniki są generowane przez skrypty i nie trafiają do gita (.gitignore).
DATA_DIR: Path = REPO_ROOT / "data"
GENERATED_DIR: Path = DATA_DIR / "generated"   # CSV z generatora
XE_JSON_DIR: Path = DATA_DIR / "xe_json"       # JSON z programu LOGGER

RESULTS_DIR: Path = REPO_ROOT / "results"
ML_RESULTS_DIR: Path = RESULTS_DIR / "ml"
EDA_PLOTS_DIR: Path = RESULTS_DIR / "eda_plots"

JOINED_DIR: Path = DATA_DIR / "joined"         # zdarzenia XE połączone z ground truth

LOCAL_CONFIG_FILE: Path = REPO_ROOT / "config.local.json"

SPLITS = ("train", "validation", "test")


def events_csv(split: str) -> Path:
    """Ground truth per operacja (jeden wiersz = jedno zapytanie wysłane do bazy)."""
    return GENERATED_DIR / f"events_{split}.csv"


def incidents_csv(split: str) -> Path:
    """Ground truth per incydent (jeden wiersz = jedna wstrzyknięta anomalia)."""
    return GENERATED_DIR / f"incidents_{split}.csv"


def seed_records_csv(split: str) -> Path:
    """Rekordy startowe do wstawienia przed przebiegiem (cele UPDATE/DELETE i rodzice)."""
    return GENERATED_DIR / f"seed_records_{split}.csv"


def users_csv() -> Path:
    """Użytkownicy symulacji (role, loginy, urządzenia, rzadkie tabele)."""
    return GENERATED_DIR / "users.csv"


def joined_csv(split: str) -> Path:
    """Zdarzenia z Extended Events połączone z ground truth po EventUid."""
    return JOINED_DIR / f"joined_{split}.csv"
