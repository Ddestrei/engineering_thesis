# System wykrywania anomalii behawioralnych w SQL Server

Praca inżynierska: porównanie metod regułowych, statystycznych i uczenia maszynowego w wykrywaniu anomalii behawioralnych użytkowników bazy SQL Server, na podstawie logów Extended Events, z panelem analitycznym w WPF.

**Dokumenty projektu:**
- `Poprawiony schemat użycia.docx`: cel, pytania badawcze, hipotezy, zakres, profile ról.
- [METODOLOGIA.md](METODOLOGIA.md): zamrożone decyzje eksperymentu (D1–D19).
- [PLAN_NAPRAWCZY.md](PLAN_NAPRAWCZY.md): zadania w kodzie, w kolejności faz.
- `Ocena_projektu_system_wykrywania_anomalii_SQL_WPF.docx`: ocena zewnętrzna, na którą odpowiadają dwa powyższe pliki.

> **Stan:** trwa realizacja planu naprawczego (Faza 1 zakończona). Obecny kod to jeszcze wersja z jednym użytkownikiem (CEO) i cechami per zdarzenie; docelowy łańcuch opisuje METODOLOGIA.md.

## Struktura repozytorium

```
anomaly/                  pakiet Pythona
  paths.py                wspólne ścieżki (wszystko względem katalogu repozytorium)
  settings.py             hasła i ustawienia lokalne (config.local.json / zmienne środowiskowe)
  generator/              generator syntetycznej aktywności i anomalii
  execution/              wykonanie operacji na SQL Server, konfiguracja kont
  features/               przygotowanie cech
  models/                 detektory (reguły, robust, IF, OCSVM, LOF) i progowanie
  evaluation/             eksperyment ML, ablacja, EDA
LOGGER/                   program .NET 9: plik .xel (Extended Events) -> JSON
sql_files/                konfiguracja sesji Extended Events i kont w bazie
PREZENTACJE/              prezentacje
archiwum/                 nieaktualne wyniki sprzed planu naprawczego (patrz archiwum/README.md)
data/                     [generowane, poza gitem] CSV z generatora, JSON z LOGGER-a
results/                  [generowane, poza gitem] wyniki eksperymentów i wykresy
```

## Przygotowanie środowiska

Wymagania: Python 3.13, .NET 9 SDK, SQL Server z bazą `AdventureWorksLT2025`, sterownik ODBC Driver 17/18 for SQL Server.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**Hasła** nie są zapisane w kodzie. Skopiuj `config.example.json` jako `config.local.json` (ten plik jest w `.gitignore`) i wpisz hasło konta `CEO_User`. Alternatywnie ustaw zmienną środowiskową `ANOMALY_DB_PASSWORD_CEO_USER`.

## Uruchomienie łańcucha (obecna wersja)

Wszystkie polecenia Pythona uruchamia się **z katalogu głównego repozytorium** jako moduły (`python -m …`).

1. **Sesja Extended Events** (jednorazowo, jako administrator SQL Server): wykonaj `sql_files/Extended_Events_set_up.sql`. Pliki `.xel` trafiają do `C:\XELogs\`.
2. **Konto CEO_User** (jednorazowo, Windows Authentication jako administrator):
   ```bash
   python -m anomaly.execution.setup_ceo_user
   ```
3. **Generowanie danych** do `data/generated/`:
   ```bash
   python -m anomaly.generator.ceo_log_generator
   ```
4. **Wykonanie operacji na bazie** (podgląd bez łączenia: `--dry-run`):
   ```bash
   python -m anomaly.execution.db_workload_executor --all-csv
   ```
5. **Eksport logów XE do JSON** do `data/xe_json/` (odczekaj kilka sekund na zapis buforów XE):
   ```bash
   dotnet run --project LOGGER -- [plik.xel] [plik_wyjsciowy.json] [--ceo]
   ```
6. **Eksperyment ML** (wyniki w `results/ml/`) i **EDA** (wykresy w `results/eda_plots/`):
   ```bash
   python -m anomaly.evaluation.ml_anomaly_pipeline
   python -m anomaly.evaluation.eda_pipeline [plik.json]
   ```

Uwaga: obecny eksperyment ML (krok 6) czyta jeszcze CSV z generatora zamiast logów z XE. Naprawia to Faza 5 planu.
