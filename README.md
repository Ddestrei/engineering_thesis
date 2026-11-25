# System wykrywania anomalii behawioralnych w SQL Server

Praca inżynierska: porównanie metod regułowych, statystycznych i uczenia maszynowego w wykrywaniu anomalii behawioralnych użytkowników bazy SQL Server, na podstawie logów Extended Events, z panelem analitycznym w WPF.

**Dokumenty projektu:**
- `Poprawiony schemat użycia.docx`: cel, pytania badawcze, hipotezy, zakres, profile ról.
- [METODOLOGIA.md](METODOLOGIA.md): zamrożone decyzje eksperymentu (D1–D20).
- [PLAN_NAPRAWCZY.md](PLAN_NAPRAWCZY.md): zadania w kodzie, w kolejności faz.
- `Ocena_projektu_system_wykrywania_anomalii_SQL_WPF.docx`: ocena zewnętrzna, na którą odpowiadają dwa powyższe pliki.

> **Stan:** trwa realizacja planu naprawczego (Fazy 1–4 zakończone). Jedno polecenie (`run_experiment`) przeprowadza ziarno przez cały łańcuch: generator → baza (30 loginów, role) → Extended Events → LOGGER → połączenie z ground truth. Eksperyment ML nadal używa cech per zdarzenie z plików generatora (Faza 5); docelowy łańcuch opisuje METODOLOGIA.md.

## Struktura repozytorium

```
anomaly/                  pakiet Pythona
  paths.py                wspólne ścieżki (wszystko względem katalogu repozytorium)
  settings.py             hasła i ustawienia lokalne (config.local.json / zmienne środowiskowe)
  domain.py               uprawnienia ról i wrażliwość tabel
  generator/              generator: profile ról, użytkownicy, norma, incydenty, rekordy startowe
  execution/              zapytania SQL, wykonanie operacji, operacje administracyjne (restore, role, XE)
  run_experiment.py       orkiestrator: cały łańcuch dla jednego ziarna
  features/               połączenie logów XE z ground truth (xe_join), przygotowanie cech
  models/                 detektory (reguły, robust, IF, OCSVM, LOF) i progowanie
  evaluation/             eksperyment ML, ablacja, EDA
LOGGER/                   program .NET 9: plik .xel (Extended Events) -> JSON (surowe fakty + EventUid)
sql_files/                sesja Extended Events, role i loginy (sim_roles_and_users.sql, generowany)
PREZENTACJE/              prezentacje
archiwum/                 nieaktualne wyniki sprzed planu naprawczego (patrz archiwum/README.md)
data/seed_<N>/            [generowane, poza gitem] generated/ (ground truth), xe/ (LOGGER), joined/ (połączone),
                          run_summary.json (czasy, statystyki, wersja kodu)
results/                  [generowane, poza gitem] wyniki eksperymentów i wykresy
tests/                    testy jednostkowe (python -m pytest)
```

## Przygotowanie środowiska

Wymagania: Python 3.13, .NET 9 SDK, SQL Server (testowane: 2025 Developer) z plikiem kopii `AdventureWorksLT2025.bak` (domyślnie w katalogu kopii zapasowych instancji), sterownik ODBC Driver 17/18 for SQL Server, katalog `C:\XELogs` z prawem zapisu dla usługi SQL Server. Orkiestrator uruchamia się z konta Windows z rolą `sysadmin` (restore bazy, loginy, sesja XE).

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**Hasło** nie jest zapisane w kodzie. Skopiuj `config.example.json` jako `config.local.json` (ten plik jest w `.gitignore`) i wpisz wspólne hasło 30 loginów symulacji (`sim_password`). Alternatywnie ustaw zmienną środowiskową `ANOMALY_SIM_PASSWORD`. Loginy tworzy (i aktualizuje im hasło) orkiestrator. W sekcji `settings` można zmienić serwer, bazę, ścieżkę `.bak` i katalog plików XE.

## Uruchomienie łańcucha

Wszystkie polecenia Pythona uruchamia się **z katalogu głównego repozytorium** jako moduły (`python -m …`).

**Cały łańcuch dla jednego ziarna** z D6 (11, 22, 33, 44, 55):

```bash
python -m anomaly.run_experiment --seed 11
python -m anomaly.run_experiment --seed 11 --splits validation      # tylko wybrane przebiegi
```

Orkiestrator robi kolejno:
1. generator → `data/seed_<N>/generated/` (`users.csv`, `events_<split>.csv`, `incidents_<split>.csv`, `seed_records_<split>.csv`, `generator_report.txt`),
2. kompilacja LOGGER-a, loginy `SIM_<ROLA>_<nr>` (usuwa stary login `CEO_User`), sesja XE ze skryptu `sql_files/Extended_Events_set_up.sql` (tworzona, jeśli jej brak),
3. dla każdego splitu (przebiegu `train-s<N>`, `validation-s<N>`, `test-s<N>`):
   - przywrócenie bazy z `.bak`, role `sim_<rola>` z uprawnieniami z dokumentu, użytkownicy bazy,
   - wstawienie rekordów startowych (D20) jako administrator,
   - start sesji XE z osobnym plikiem `C:\XELogs\<run_id>__<czas>*.xel`,
   - wykonanie operacji w kolejności czasu symulowanego (każda sesja generatora = osobne połączenie z loginem użytkownika, hostem w WSID i urządzeniem/IP w nazwie aplikacji),
   - zatrzymanie XE, LOGGER → `data/seed_<N>/xe/<run_id>.json`, połączenie z ground truth → `data/seed_<N>/joined/joined_<split>.csv` i `join_report_<split>.json`,
4. podsumowanie `data/seed_<N>/run_summary.json` (czasy kroków, statystyki wykonania, hash commita).

Kod wyjścia 0 oznacza: zero błędów SQL, każdy zapis zmienił dokładnie jeden wiersz, a join ma pokrycie 100%. Przy pierwszym błędzie orkiestrator kończy pracę (przebieg odrzucony).

**Kroki pojedynczo** (podgląd, diagnostyka):

```bash
python -m anomaly.generator.generate --seed 11
python -m anomaly.execution.db_workload_executor --seed 11 --split validation --dry-run    # zapytania bez bazy
dotnet run --project LOGGER -- [plik|katalog|wzorzec.xel] [wyjście.json] [--user LOGIN]
python -m anomaly.features.xe_join --seed 11 --split validation [--xe plik.json]
python -m anomaly.execution.db_admin --write-sql        # odświeża sql_files/sim_roles_and_users.sql
```

Wykonawca uruchomiony samodzielnie (bez `--dry-run`) nie przywraca bazy ani nie wstawia rekordów startowych; do przebiegów eksperymentu służy wyłącznie `run_experiment`.

**Eksperyment ML** (wyniki w `results/ml/`) i **EDA** (wykresy w `results/eda_plots/`):

```bash
python -m anomaly.evaluation.ml_anomaly_pipeline
python -m anomaly.evaluation.eda_pipeline [plik.json]
```

**Stan przejściowy:** eksperyment ML czyta jeszcze pliki generatora ziarna 11 zamiast `data/seed_<N>/joined/` (zmieni to Faza 5), a EDA czyta tylko stary format JSON z archiwum (Faza 9).

## Testy

```bash
python -m pytest
```
