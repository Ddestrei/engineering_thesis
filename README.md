# System wykrywania anomalii behawioralnych w SQL Server

Praca inżynierska: porównanie metod regułowych, statystycznych i uczenia maszynowego w wykrywaniu anomalii behawioralnych użytkowników bazy SQL Server, na podstawie logów Extended Events, z panelem analitycznym w WPF.

**Dokumenty projektu:**
- `Poprawiony schemat użycia.docx`: cel, pytania badawcze, hipotezy, zakres, profile ról.
- [METODOLOGIA.md](METODOLOGIA.md): zamrożone decyzje eksperymentu (D1–D20).
- [PLAN_NAPRAWCZY.md](PLAN_NAPRAWCZY.md): zadania w kodzie, w kolejności faz.
- `Ocena_projektu_system_wykrywania_anomalii_SQL_WPF.docx`: ocena zewnętrzna, na którą odpowiadają dwa powyższe pliki.

> **Stan:** trwa realizacja planu naprawczego (Fazy 1–3 zakończone). Generator obejmuje 6 ról × 5 użytkowników; wykonanie na bazie dla nowych tabel i UPDATE/DELETE to Faza 4, a eksperyment ML nadal używa cech per zdarzenie (Faza 5); docelowy łańcuch opisuje METODOLOGIA.md.

## Struktura repozytorium

```
anomaly/                  pakiet Pythona
  paths.py                wspólne ścieżki (wszystko względem katalogu repozytorium)
  settings.py             hasła i ustawienia lokalne (config.local.json / zmienne środowiskowe)
  domain.py               uprawnienia ról i wrażliwość tabel
  generator/              generator: profile ról, użytkownicy, norma, incydenty, rekordy startowe
  execution/              wykonanie operacji na SQL Server, konfiguracja kont
  features/               połączenie logów XE z ground truth (xe_join), przygotowanie cech
  models/                 detektory (reguły, robust, IF, OCSVM, LOF) i progowanie
  evaluation/             eksperyment ML, ablacja, EDA
LOGGER/                   program .NET 9: plik .xel (Extended Events) -> JSON (surowe fakty + EventUid)
sql_files/                konfiguracja sesji Extended Events i kont w bazie
PREZENTACJE/              prezentacje
archiwum/                 nieaktualne wyniki sprzed planu naprawczego (patrz archiwum/README.md)
data/                     [generowane, poza gitem] generated/ (ground truth), xe_json/ (LOGGER), joined/ (połączone)
results/                  [generowane, poza gitem] wyniki eksperymentów i wykresy
tests/                    testy jednostkowe (python -m pytest)
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
3. **Generowanie danych** dla ziarna z D6 (11, 22, 33, 44, 55) do `data/generated/`: `users.csv`, `events_<split>.csv` (operacje z `event_uid`, `record_key`, etykietami `gt`), `incidents_<split>.csv`, `seed_records_<split>.csv` (rekordy startowe, D20) i `generator_report.txt` (zgodność z profilami ról):
   ```bash
   python -m anomaly.generator.generate --seed 11
   ```
4. **Wykonanie operacji na bazie** (do czasu Fazy 4 tylko `--dry-run`: wykonawca nie obsługuje jeszcze nowych tabel, UPDATE/DELETE ani 30 loginów) przy **uruchomionej** sesji XE (podgląd bez łączenia: `--dry-run`). Każde zapytanie niesie tylko znacznik `/* gt:<event_uid> */`. Każdy przebieg wykonuj raz: ponowne wykonanie tych samych plików da duplikaty, które walidacja odrzuci.
   ```bash
   python -m anomaly.execution.db_workload_executor --all-csv
   ```
   Po wykonaniu zatrzymaj sesję XE (`ALTER EVENT SESSION [AW2025_Behavioral_Anomaly_Session] ON SERVER STATE = STOP`); wymusza to zapis buforów do pliku.
5. **Eksport logów XE do JSON** do `data/xe_json/` (domyślnie wszystkie pliki `C:\XELogs\AW2025_Behavioral_Anomaly*.xel`):
   ```bash
   dotnet run --project LOGGER -- [plik|katalog|wzorzec.xel] [wyjście.json] [--user LOGIN | --ceo]
   ```
6. **Połączenie z ground truth i walidacja** (pokrycie musi wynosić 100%, inaczej kod wyjścia 1 i raport błędów). Wynik: `data/joined/joined_<split>.csv`:
   ```bash
   python -m anomaly.features.xe_join [--xe plik.json] [--splits train validation test]
   ```
7. **Eksperyment ML** (wyniki w `results/ml/`) i **EDA** (wykresy w `results/eda_plots/`):
   ```bash
   python -m anomaly.evaluation.ml_anomaly_pipeline
   python -m anomaly.evaluation.eda_pipeline [plik.json]
   ```

**Stan przejściowy:** eksperyment ML (krok 7) czyta jeszcze pliki generatora zamiast `data/joined/` (zmieni to Faza 5), a EDA czyta tylko stary format JSON z archiwum (Faza 9).

## Testy

```bash
python -m pytest
```
