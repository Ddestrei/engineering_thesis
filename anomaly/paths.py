"""
Wspólne ścieżki projektu.

Wszystkie ścieżki liczone są względem katalogu głównego repozytorium,
dzięki czemu skrypty działają niezależnie od katalogu, z którego zostały uruchomione.

Dane każdego ziarna (METODOLOGIA D6) leżą w osobnym katalogu data/seed_<N>/:
    generated/   pliki generatora (ground truth, rekordy startowe, użytkownicy)
    xe/          eksport JSON z LOGGER-a, jeden plik na przebieg (<run_id>.json)
    joined/      zdarzenia XE połączone z ground truth + raporty walidacji
    features/    okna UserId × 30 min z cechami D14 per wariant modelowania (Faza 5)
    run_summary.json  podsumowanie przebiegu run_experiment (czasy, statystyki)
"""
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[1]

SQL_DIR: Path = REPO_ROOT / "sql_files"
LOGGER_PROJECT: Path = REPO_ROOT / "LOGGER"

# Dane i wyniki są generowane przez skrypty i nie trafiają do gita (.gitignore).
DATA_DIR: Path = REPO_ROOT / "data"
XE_JSON_DIR: Path = DATA_DIR / "xe_json"       # domyślne wyjście LOGGER-a uruchamianego ręcznie

RESULTS_DIR: Path = REPO_ROOT / "results"
ML_RESULTS_DIR: Path = RESULTS_DIR / "ml"
EDA_PLOTS_DIR: Path = RESULTS_DIR / "eda_plots"

LOCAL_CONFIG_FILE: Path = REPO_ROOT / "config.local.json"

SPLITS = ("train", "validation", "test")
DEFAULT_SEED = 11


def seed_dir(seed: int) -> Path:
    return DATA_DIR / f"seed_{seed}"


def generated_dir(seed: int) -> Path:
    return seed_dir(seed) / "generated"


def xe_dir(seed: int) -> Path:
    return seed_dir(seed) / "xe"


def joined_dir(seed: int) -> Path:
    return seed_dir(seed) / "joined"


def events_csv(split: str, seed: int) -> Path:
    """Ground truth per operacja (jeden wiersz = jedno zapytanie wysłane do bazy)."""
    return generated_dir(seed) / f"events_{split}.csv"


def incidents_csv(split: str, seed: int) -> Path:
    """Ground truth per incydent (jeden wiersz = jedna wstrzyknięta anomalia)."""
    return generated_dir(seed) / f"incidents_{split}.csv"


def seed_records_csv(split: str, seed: int) -> Path:
    """Rekordy startowe do wstawienia przed przebiegiem (cele UPDATE/DELETE i rodzice)."""
    return generated_dir(seed) / f"seed_records_{split}.csv"


def users_csv(seed: int) -> Path:
    """Użytkownicy symulacji (role, loginy, urządzenia, rzadkie tabele)."""
    return generated_dir(seed) / "users.csv"


def generator_report_txt(seed: int) -> Path:
    return generated_dir(seed) / "generator_report.txt"


def xe_json(run_id: str, seed: int) -> Path:
    """Eksport LOGGER-a dla jednego przebiegu."""
    return xe_dir(seed) / f"{run_id}.json"


def joined_csv(split: str, seed: int) -> Path:
    """Zdarzenia z Extended Events połączone z ground truth po EventUid."""
    return joined_dir(seed) / f"joined_{split}.csv"


def join_report_json(split: str, seed: int) -> Path:
    return joined_dir(seed) / f"join_report_{split}.json"


def run_summary_json(seed: int) -> Path:
    return seed_dir(seed) / "run_summary.json"


def features_dir(seed: int) -> Path:
    return seed_dir(seed) / "features"


def windows_csv(variant: str, split: str, seed: int) -> Path:
    """Okna UserId × 30 min z cechami D14 dla wariantu modelowania (global / role / user)."""
    return features_dir(seed) / f"windows_{variant}_{split}.csv"


def reference_json(variant: str, seed: int) -> Path:
    """Statystyki normy z TRAIN, z których liczone są cechy względne wariantu."""
    return features_dir(seed) / f"reference_{variant}.json"


def features_report_json(seed: int) -> Path:
    return features_dir(seed) / "features_report.json"


# --- wyniki detektorów (Faza 6) ----------------------------------------------------------
# results/seed_<N>/detectors/<konfiguracja>/   konfiguracja = "full" albo nazwa zestawu ablacji (Faza 7)

def results_seed_dir(seed: int) -> Path:
    return RESULTS_DIR / f"seed_{seed}"


def detectors_dir(seed: int, config: str = "full") -> Path:
    return results_seed_dir(seed) / "detectors" / config


def frozen_config_json(seed: int, config: str = "full") -> Path:
    """Hiperparametry, normalizacja i progi zamrożone przed TEST (D7)."""
    return detectors_dir(seed, config) / "frozen_config.json"


def models_joblib(variant: str, detector: str, seed: int, config: str = "full") -> Path:
    return detectors_dir(seed, config) / "models" / f"{variant}_{detector}.joblib"


def val_scores_csv(variant: str, seed: int, config: str = "full") -> Path:
    return detectors_dir(seed, config) / f"val_scores_{variant}.csv"
