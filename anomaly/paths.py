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

LOCAL_CONFIG_FILE: Path = REPO_ROOT / "config.local.json"
