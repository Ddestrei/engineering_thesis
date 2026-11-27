# Plan naprawczy projektu
## System wykrywania anomalii behawioralnych w SQL Server z panelem WPF

Źródła planu:
- ocena zewnętrzna: `Ocena_projektu_system_wykrywania_anomalii_SQL_WPF.docx`,
- założenia pracy: `Poprawiony schemat użycia.docx` (RQ1–RQ6, H1–H5, 6 ról),
- przegląd kodu repozytorium (stan z commita `6719f6c`, 9.11.2026).

---

## 0. Diagnoza w jednym akapicie

Koncepcja jest dobra i recenzent zaleca jej **nie rozszerzać**: bez nowych modeli i bez nowych klas anomalii. Do poprawy są fundamenty. Obecnie:

1. Próbką jest pojedyncze zdarzenie SQL, a 3 z 4 klas anomalii (wolumen, proporcja, rzadki zasób) da się zobaczyć tylko w agregacie.
2. Pipeline ML **nie czyta logów z Extended Events**: [AnomalyDataLoader.py:93-121](archiwum/stary_pipeline/AnomalyDataLoader.py#L93-L121) losuje metryki SQL w Pythonie (Faza 5 ✅: zastąpione oknami z danych XE).
3. Jest tylko jeden użytkownik w jednej roli, więc RQ4/H1 są niewykonalne.
4. Generator ma błędy, które psują normę (np. normalny ruch w weekend).

Skutek: MCC na teście wynosi 0,05–0,24, a FPR 31–87%. Plan naprawia te punkty w kolejności zależności.

---

## 1. Decyzje metodologiczne (ustalić i zamrozić PRZED kodowaniem)

Wszystkie decyzje (D1–D20) są w **[METODOLOGIA.md](METODOLOGIA.md)**. Ten plan się do nich odwołuje i ich nie powtarza, żeby nie było dwóch rozbieżnych wersji. Po starcie eksperymentów na TEST decyzji się nie zmienia (wyjątki tylko przez „Rejestr zmian”).

---

## 2. Fazy realizacji

Kolejność wynika z zależności: bez ground truth (Faza 2) nie da się wiarygodnie policzyć metryk, a bez okien (Faza 5) modele nie widzą 3 z 4 klas anomalii.

### Faza 1: Porządki w repozytorium ✅ (zakończona 25.11.2026)

- [x] [.gitignore](.gitignore) (`bin/`, `obj/`, `.vs/`, `__pycache__/`, `*.xel`, `/data/`, `/results/`, `config.local.json`); `LOGGER/bin`, `LOGGER/obj`, `LOGGER/.vs` i `__pycache__` usunięte z indeksu gita.
- [x] Hasło usunięte z kodu. Python pobiera je przez [anomaly/settings.py](anomaly/settings.py) ze zmiennej `ANOMALY_DB_PASSWORD_<LOGIN>` albo z `config.local.json` (wzór: [config.example.json](config.example.json)); [create_ceo_user.sql](archiwum/stary_ceo_user/create_ceo_user.sql) (od Fazy 4 w archiwum) używał zmiennej SQLCMD `$(CEO_PASSWORD)`. **Hasło zostaje w historii gita** (zostało wypchnięte na GitHub), więc trzeba je zmienić na serwerze SQL.
- [x] Ścieżka `C:\Users\ddest\...\LOGI` w [Program.cs](LOGGER/Program.cs) zastąpiona wyszukaniem katalogu repozytorium (`data/xe_json/`), z nadpisaniem przez argument albo `ANOMALY_XE_JSON_DIR`.
- [x] [setup_ceo_user.py](archiwum/stary_ceo_user/setup_ceo_user.py) (od Fazy 4 w archiwum): poprawna ścieżka do `sql_files/`, podział batchy po liniach `GO`, kod wyjścia ≠ 0 przy błędach.
- [x] [requirements.txt](requirements.txt) z wersjami.
- [x] Pakiet [anomaly/](anomaly/) z podkatalogami `generator/`, `execution/`, `features/`, `models/`, `evaluation/`; wspólne ścieżki w [anomaly/paths.py](anomaly/paths.py). Uruchamianie: `python -m anomaly.<moduł>` z katalogu głównego.
- [x] Stare wyniki i dane przeniesione do [archiwum/](archiwum/README.md) z opisem, dlaczego są nieaktualne.
- [x] [README.md](README.md) z instrukcją uruchomienia.

**Sprawdzone:** 25 modułów się importuje; generator, podgląd wykonania (`--dry-run`), eksperyment ML i EDA działają z nowych ścieżek; LOGGER kompiluje się bez błędów i ostrzeżeń. Nie sprawdzono połączenia z SQL Server (wymaga hasła i działającej instancji).

### Faza 2: Ground truth i korelacja z XE ✅ (zakończona 25.11.2026)

Cel: każde zdarzenie z XE da się w 100% deterministycznie powiązać z wierszem generatora. Etykieta nie trafia do cech.

**Przyjęty podział** (decyzja z 25.11): z logów XE pochodzą wszystkie fakty obserwowalne (metryki wykonania, tekst SQL, host, urządzenie, IP); z generatora wyłącznie **czas symulowany i etykiety** (kolumny `gt_*`).

- [x] **Generator nadaje identyfikatory** (`CEOLogGenerator.py`, w Fazie 3 zastąpiony przez [dataset.py](anomaly/generator/dataset.py)): `run_id` (`<split>-s<seed>`), `event_uid` (`<run_id>-<nr>`), `incident_id`. Zapisuje `data/generated/events_<split>.csv` i `incidents_<split>.csv`. Kolumna `intensity` jest na razie pusta (wypełni ją Faza 3, D15).
- [x] **W SQL tylko `EventUid`**: komentarz `/* gt:<EventUid> */` ([gt_tag.py](anomaly/execution/gt_tag.py)), bez etykiet, hosta, IP i czasu.
- [x] **`APP` w connection stringu** ([SessionConnectionManager.py](anomaly/execution/SessionConnectionManager.py)): `SIM|run:<run_id>|dev:<urządzenie>|ip:<ip>`, bez `;` i `=`, bez etykiet. Host w `WSID`.
- [x] **LOGGER** przepisany ([XeLogReader.cs](LOGGER/XeLogReader.cs), [XeEventRecord.cs](LOGGER/XeEventRecord.cs), [JsonExporter.cs](LOGGER/JsonExporter.cs)); usunięte `AnomalyFeatureRecord.cs` i `MLFeatureExporter.cs`:
  - eksportuje surowe fakty z XE + `EventUid` (format `xe-export/v2`), **bez etykiet i bez wektora cech**. Cechy liczy Python per okno (Faza 5); punkt „`DayOfWeek` w konwencji ISO w LOGGER-ze” stał się zbędny, bo czas symulowany jest znany dopiero po połączeniu,
  - `SqlText` i `SqlTextLength` po wycięciu znacznika,
  - `error_reported` w osobnej liście `errors`, nie jako dane,
  - `OperationType` po pominięciu komentarzy; lista wszystkich tabel `SalesLT.*` w zapytaniu,
  - domyślnie czyta wszystkie pliki rollover `C:\XELogs\AW2025_Behavioral_Anomaly*.xel`.
- [x] **Połączenie i walidacja** ([xe_join.py](anomaly/features/xe_join.py)): odrzuca przebieg przy błędzie SQL, duplikacie, brakującym zdarzeniu, sierocie, wyniku ≠ OK albo niezgodnym kontekście (run, urządzenie, IP, host, login, typ operacji). Zdarzenia innych przebiegów są pomijane. Wynik: `data/joined/joined_<split>.csv` + `join_report.json`.
- [x] Czas symulowany (`sim_timestamp`) vs fizyczny (`physical_timestamp`) rozdzielone w danych; ograniczenie opisane w METODOLOGIA (D11, sekcja E).
- [x] Poza zakresem fazy, ale blokowało walidację: anomalie „nietypowa pora” i „wzrost wolumenu” losowały tabelę INSERT-a z wag SELECT, więc 22 operacje łamały uprawnienia. Poprawione.
- [x] Testy: [tests/test_ground_truth.py](tests/test_ground_truth.py) (15 testów, `python -m pytest`).

**Sprawdzone na bazie (25.11.2026):** 664 zapytania (3 zbiory) wykonane jako `CEO_User` bez błędów; LOGGER: 664 zdarzenia ze znacznikiem, 0 błędów; walidacja: **pokrycie 100%**, wszystkie 11 kontroli OK. Długość SQL nie zależy już od etykiety (INSERT Customer: średnio 333,1 dla normy i 333,0 dla anomalii; wcześniej komentarz z etykietą różnicował ją o setki znaków).

**Znane ograniczenia przejściowe:**
- eksperyment ML (`ml_anomaly_pipeline`) nadal czyta pliki generatora, a nie `data/joined/`; Faza 5 ✅: przeniesiony do archiwum, cechy liczy `windowing.py` z danych XE,
- `eda_pipeline` czyta tylko stary format JSON; Faza 5: przeniesiony do archiwum, nowa EDA w Fazie 9,
- health-check `SELECT 1` nadal generuje zdarzenia XE bez znacznika (LOGGER je pomija); usunięcie w Fazie 4 ✅,
- `cleanup_simulation_data` nie działa (Faza 4 ✅: zastąpione przywróceniem bazy z `.bak`).

### Faza 3: Generator: wiele ról, wielu użytkowników, poprawna norma ✅ (zakończona 25.11.2026)

Nowy generator w [anomaly/generator/](anomaly/generator/) zastąpił stary generator tylko dla CEO (usunięte: `CEOLogGenerator.py`, `CEOProfileConfig.py`, `AnomalyConfig.py`, `ceo_log_generator.py`). Uruchomienie: `python -m anomaly.generator.generate --seed 11`.

| Moduł | Zawartość |
|---|---|
| [domain.py](anomaly/domain.py) | uprawnienia 6 ról 1:1 z dokumentu, wrażliwość tabel (D10) |
| [profiles.py](anomaly/generator/profiles.py) | profile ról: urządzenia, dni, godziny, wolumen, proporcje, tryb końca miesiąca CFO, trudne przypadki |
| [users.py](anomaly/generator/users.py) | 5 użytkowników na rolę, perturbacja D15, rzadkie tabele, zaburzenie testu D16 |
| [normal_activity.py](anomaly/generator/normal_activity.py) | norma, tygodnie od poniedziałku, sekwencje z profili, trudne przypadki z tagami `HARD_NORMAL_*` |
| [incidents.py](anomaly/generator/incidents.py) | 4 klasy × silna/słaba, warianty VAL/TEST (D16), liczba (D18), severity (D19) |
| [records.py](anomaly/generator/records.py) | `record_key` / `parent_keys` i rekordy startowe (D20) |
| [dataset.py](anomaly/generator/dataset.py), [generate.py](anomaly/generator/generate.py), [report.py](anomaly/generator/report.py) | ziarna (D6), okresy (D5), zapis plików, raport zgodności |

- [x] Profile 6 ról jako dane, w granicach uprawnień (asercja przy tworzeniu profilu).
- [x] 5 użytkowników na rolę z perturbacją D15 (środek okna ±1 h, granice z dokumentu zostają).
- [x] Ziarna: `SeedSequence(seed).spawn(4)`; `SQLQueryBuilder` bez globalnego `random` (treść zapytania zależy tylko od `event_uid`).
- [x] Błędy naprawione: tygodnie od poniedziałku (koniec z normą w weekend), wolumen niepełnego tygodnia proporcjonalny do dni roboczych, kumulowane odstępy w sesji, CEO w biurze tylko wt–czw 10–15.
- [x] UPDATE/DELETE bez błędów kluczy obcych: pula rekordów startowych (D20) zamiast śledzenia stanu bazy.
- [x] Sekwencje z profili: szkic → usunięcie (Marketing Manager), zmiana adresu (Sales), porządkowanie duplikatów 1–2×/mies. (Sales Manager).
- [x] Liczba incydentów D18, severity D19, intensywność 50/50 (D15), warianty VAL/TEST (D16).
- [x] Rzadki zasób rzadki per użytkownik (1–3% odczytów, tylko tabele wyłącznie odczytywane).
- [x] Trudne przypadki legalne D15 we wszystkich zbiorach, także w TRAIN.
- [x] Testy: [tests/test_generator.py](tests/test_generator.py) (14 testów) + przepięte [tests/test_ground_truth.py](tests/test_ground_truth.py); razem 30 testów.

**Sprawdzone (ziarno 11):** 0 operacji niezgodnych z uprawnieniami; wolumen tygodniowy (P10–P90) i udziały operacji wszystkich ról w przedziałach z dokumentu (np. CFO: SELECT 24,7% w miesiącu i 79,9% na koniec miesiąca, UPDATE:DELETE = 40:60); CEO: biuro 73%, dom 14%, laptop 14%; VAL 72 incydenty (3 na klasę i rolę), TEST 70–82 zależnie od ziarna. Udział okien anomalnych: VAL 9,5–10%, TEST 10,6–13% (D18, raportowany). Raport: `data/generated/generator_report.txt`.

**Do Fazy 4 z tej fazy:** wykonawca musi obsłużyć nowy format (30 loginów `SIM_<ROLA>_<nr>`, `record_key` → `rowguid`, `parent_keys`, rekordy startowe). Do tego czasu `db_workload_executor` w trybie `--dry-run` działa, ale nie zna SQL dla nowych tabel ani UPDATE/DELETE.

### Faza 4: SQL Server i wykonanie obciążenia ✅ (zakończona 25.11.2026)

Jedno polecenie przeprowadza ziarno przez cały łańcuch: `python -m anomaly.run_experiment --seed 11`.

| Moduł | Zawartość |
|---|---|
| [run_experiment.py](anomaly/run_experiment.py) | orkiestrator: generator → (dla każdego splitu) restore → role → rekordy startowe → XE → wykonanie → LOGGER → join; `run_summary.json` z czasami i hashem commita |
| [db_admin.py](anomaly/execution/db_admin.py) | restore z `.bak`, 30 loginów, role `sim_<rola>` z `domain.PERMISSIONS`, rekordy startowe, sterowanie sesją XE; `--write-sql` → [sim_roles_and_users.sql](sql_files/sim_roles_and_users.sql) |
| [SQLQueryBuilder.py](anomaly/execution/SQLQueryBuilder.py) | szablony SELECT (z JOIN-ami z dokumentu), INSERT, UPDATE, DELETE dla 10 tabel; `record_key` → `rowguid` (UUID v5) |
| [DBWorkloadExecutor.py](anomaly/execution/DBWorkloadExecutor.py), [SessionConnectionManager.py](anomaly/execution/SessionConnectionManager.py) | wykonanie w kolejności czasu symulowanego, połączenie na sesję z loginem użytkownika, kontrola „zapis = 1 wiersz” |

- [x] **Role bazodanowe** `sim_ceo`, `sim_cfo`, … z uprawnieniami 1:1 z dokumentu (tylko `GRANT`, bez `DENY`) i **30 loginów** `SIM_<ROLA>_<nr>`, każdy w jednej roli. Wspólne hasło w `config.local.json` (`sim_password`) albo `ANOMALY_SIM_PASSWORD`. Login `CEO_User` jest usuwany, a jego skrypty przeszły do [archiwum/stary_ceo_user/](archiwum/stary_ceo_user/).
- [x] **Szablony SQL dla wszystkich tabel i operacji.** SELECT dołącza JOIN-em tylko tabele, które rola może czytać, z pominięciem rzadkich tabel użytkownika (D15). UPDATE i DELETE mają postać `WHERE rowguid = …`, a INSERT rekordu zależnego wyszukuje rodzica podzapytaniem po `rowguid`. Treść zapytania zależy tylko od `event_uid` i `record_key`.
- [x] **Restore z `.bak` przed każdym przebiegiem** zamiast `cleanup_simulation_data` (usunięte). Potem role, użytkownicy i rekordy startowe (`seed_records_<split>.csv`, rodzice przed dziećmi), wstawiane jako administrator i sprawdzane po wstawieniu.
- [x] Usunięty health-check `SELECT 1`. Pula połączeń ODBC wyłączona przy imporcie pakietu `anomaly.execution`, więc każda sesja generatora to osobne logowanie i osobne `client_connection_id` w XE. **Poprawka po pierwszych przebiegach:** pierwotnie pula była wyłączana za późno (po połączeniu administratora), przez co sesje tego samego użytkownika i urządzenia współdzieliły połączenie (`sp_reset_connection`). Etykiety i join były poprawne, ale cechy sesji z XE (D14) byłyby zaniżone; przebiegi wszystkich ziaren trzeba było powtórzyć.
- [x] **Sesja XE:** zostają `sql_batch_completed` (+ `rpc_completed`, `error_reported`), bo każda operacja to jeden batch; `sql_statement_completed` dublowałby zdarzenia i dodawał instrukcje wyzwalaczy. Każdy przebieg ma osobny plik `C:\XELogs\<run_id>__<czas>*.xel`. Tryb `ALLOW_SINGLE_EVENT_LOSS` z buforem 32 MB, bo SQL Server nie dopuszcza `NO_EVENT_LOSS` dla `error_reported`; ewentualną utratę wykrywa join (pokrycie 100%).
- [x] **LOGGER:** nowe pole `TargetTable` (tabela główna zapytania; schemat `xe-export/v3`), domyślnie czyta `C:\XELogs\*.xel`, bez flagi `--ceo`. **Naprawiony błąd:** tekst SQL brany był z akcji `sql_text` (bufor wejściowy sesji); dla wewnętrznych wywołań RPC sterownika ODBC zawierała ona poprzedni batch ze znacznikiem gt, co dawało ok. 2,5 tys. zdublowanych `EventUid` na przebieg TRAIN. Teraz tekst pochodzi z pola zdarzenia (`batch_text` / `statement`).
- [x] **xe_join:** kontrola `TargetTable == target_table` i kontrola „sesja generatora = dokładnie jedno połączenie XE” (13 kontroli); wynik per split i ziarno. **Naprawiony błąd z Fazy 2:** tabela połączona miała dwie kolumny `operation_type` (z XE i z generatora); teraz każda kolumna kontekstu występuje raz, z wartością z XE.
- [x] **Dane per ziarno:** `data/seed_<N>/{generated, xe, joined}` i `run_summary.json`. Wcześniej każde ziarno nadpisywałoby pliki poprzedniego.
- [x] Testy: [tests/test_sql_builder.py](tests/test_sql_builder.py). Dla wszystkich ok. 31 tys. operacji ziarna 11 sprawdza zgodność z uprawnieniami, tabelę docelową, `rowguid`, rodziców i JOIN-y; sprawdza też role i loginy. Łącznie 43 testy.

**Sprawdzone na bazie (25.11.2026, wszystkie ziarna, commit `c57ead3`, `dirty=false`, łącznie ok. 2 h 20 min):**

| Ziarno | TRAIN: operacje / sesje | VAL: operacje / sesje / incydenty | TEST: operacje / sesje / incydenty | Zapytań/s |
|---|---|---|---|---|
| 11 | 13 559 / 2 700 | 8 328 / 1 496 / 72 | 8 848 / 1 440 / 82 | 18,8–20,9 |
| 22 | 13 254 / 2 602 | 8 028 / 1 421 / 72 | 8 438 / 1 454 / 73 | 18,8–21,3 |
| 33 | 13 896 / 2 682 | 8 396 / 1 491 / 72 | 8 965 / 1 493 / 70 | 18,1–20,4 |
| 44 | 13 519 / 2 677 | 8 202 / 1 442 / 72 | 8 794 / 1 493 / 72 | 17,5–20,2 |
| 55 | 13 476 / 2 656 | 8 214 / 1 463 / 72 | 8 607 / 1 484 / 74 | 18,3–21,7 |

We wszystkich 15 przebiegach: 0 błędów SQL, 0 zapisów ≠ 1 wiersz, 13/13 kontroli joinu, pokrycie 100%, 0 zdarzeń innych przebiegów. Liczba połączeń XE = liczba sesji generatora (każda sesja ma dokładnie jedno połączenie i odwrotnie). Liczba zdarzeń anomalnych i incydentów po joinie zgadza się z generatorem; VAL ma po 18 incydentów każdej klasy, TEST 14–23 na klasę. TRAIN bez anomalii. Restore ok. 1 s, rekordy startowe 10–14 s, LOGGER 2–3 s na przebieg.

Tempo ok. 20 zapytań/s wynika z braku puli połączeń: każde logowanie loginem SQL trwa ok. 250 ms (Windows Authentication: 23 ms; SQL Server 2025 haszuje hasła loginów SQL kosztownym algorytmem). Pierwszy przebieg ziarna 11 (z pulą, ok. 90–100 zapytań/s) dał JOIN-y w 9–15% zapytań każdej roli, medianę odczytów logicznych 37 i liczby wierszy 1–2; dane z tamtych przebiegów zostały nadpisane.

**Do następnych faz:** Faza 5 czyta `data/seed_<N>/joined/joined_<split>.csv` (kolumny m.in. `sim_session_id`, `xe_connection_id`, `target_table`, `gt_*`); cechy grupy Zasoby liczy z `target_table` (METODOLOGIA D14).

### Faza 5: Inżynieria cech (okna czasowe) ✅ (zakończona 27.11.2026)

| Plik | Zawartość |
|---|---|
| [windowing.py](anomaly/features/windowing.py) | okna `UserId × 30 min` z danych po joinie XE, cechy D14, statystyki normy z TRAIN per wariant (D17), kontrole poprawności; `python -m anomaly.features.windowing --seed 11 22 33 44 55` |

- [x] Okna `UserId × 30 min` (D1, D11): wyrównane do pełnych półgodzin, lewostronnie domknięte, czas symulowany, tylko okna z ≥ 1 operacją. `UserId` i `RoleId` wynikają z loginu w logu XE.
- [x] Cechy według D14, 6 grup; wszystkie z faktów XE (urządzenie, IP, tekst SQL, `TargetTable`, metryki wykonania) i czasu symulowanego. `session_count` liczone z `client_connection_id` z XE, a nie z identyfikatora sesji generatora.
- [x] Cechy względne (`hour_rarity`, `after_hours_flag`, `volume_ratio_to_median`, `rare_resource_score`, `new_table_flag`, `ip_known_flag`) wyłącznie ze statystyk TRAIN, osobno dla wariantów global / per rola / per użytkownik. Definicje dopisane do D14 (rejestr zmian 27.11.2026): `rare_resource_score` to suma po operacjach na tabelach o udziale < 5%, `after_hours_flag` to wyjście poza [P1, P99] godzin TRAIN, a `avg_query_complexity` rozbite na `avg_join_count` i `avg_sql_length`.
- [x] Brak cech błędów (D3). Stary kod z syntetycznymi metrykami (`AnomalyDataLoader`, `ml_anomaly_pipeline`, `AnomalyDetectionPipeline`, `eda_pipeline`) przeniesiony do [archiwum/stary_pipeline/](archiwum/stary_pipeline/).
- [x] Etykiety okna tylko w osobnych kolumnach (`IsAnomaly`, `IncidentIds`, `AnomalyClasses`, `Intensities`, `Severity`, `HardNormal`); `feature_columns(wariant, grupy)` zwraca kolumny cech (także dla ablacji) i nigdy nie zawiera etykiet ani identyfikatorów.
- [x] Statystyki normy zapisywane do `reference_<wariant>.json` (posłużą też panelowi WPF do porównania z profilem normy).
- [x] `run_experiment` liczy cechy na końcu przebiegu ziarna.
- [x] Testy: [tests/test_windowing.py](tests/test_windowing.py) (11 testów na danych syntetycznych: wyrównanie okien, agregaty, etykiety, niezależność od VAL, definicje cech względnych, zależność od wariantu, zastępstwo D12). Łącznie 54 testy.

**Wynik:** `data/seed_<N>/features/windows_<wariant>_<split>.csv` (3 warianty × 3 splity), `reference_<wariant>.json`, `features_report.json`. Kolumny: `UserId, RoleId, WindowStart`, etykiety, cechy (global: 34, rola i użytkownik: 28).

**Sprawdzone (27.11.2026, wszystkie ziarna, ok. 4 s na ziarno):** 7 kontroli × 3 splity × 3 warianty OK w każdym ziarnie (suma operacji okien = liczba zdarzeń, unikalność okien, brak NaN/inf, udziały sumują się do 1, cechy rozłączne z etykietami, każdy incydent w ≥ 1 oknie, okno anomalne ⇔ zawiera zdarzenie incydentu).

| Ziarno | Okna TRAIN | Okna VAL (anomalne) | Okna TEST (anomalne) | Min. okien TRAIN na użytkownika |
|---|---|---|---|---|
| 11 | 2 822 | 1 601 (9,5%) | 1 601 (13,0%) | 65 |
| 22 | 2 691 | 1 514 (9,6%) | 1 601 (10,6%) | 63 |
| 33 | 2 769 | 1 612 (9,6%) | 1 626 (11,0%) | 66 |
| 44 | 2 777 | 1 563 (9,8%) | 1 640 (11,0%) | 63 |
| 55 | 2 786 | 1 579 (10,0%) | 1 612 (11,0%) | 64 |

- Każdy użytkownik ma w TRAIN ≥ 63 okna, więc zastępstwo D12 (< 30 okien → statystyki roli) w żadnym ziarnie się nie uruchamia.
- Kontrola sygnału (ziarno 11, VAL, średnie okien klasy vs normy): wzrost wolumenu silny `volume_ratio_to_median` 4,1 vs 1,1; nietypowa pora `after_hours_flag` 0,64–0,73 vs 0,05; nietypowa proporcja `delete_ratio` 0,44 vs 0,06. **Rzadki zasób zależy od wariantu (H1):** `rare_resource_score` silnych incydentów to 21,2 per użytkownik i 16,4 per rola, ale 0 w wariancie globalnym, bo rzadka tabela użytkownika nie jest rzadka w populacji. Norma: 0,4.

**Do Fazy 6:** detektory czytają `windows_<wariant>_<split>.csv` i kolumny z `feature_columns(wariant, grupy)`; jednostka modelowania to kolumna `RoleId` albo `UserId`. Na TRAIN cechy względne są liczone w próbie (okno należy do własnej normy), co dla detektorów uczonych na TRAIN jest zgodne z D12/D13.

### Faza 6: Detektory i progowanie ✅ (zakończona 27.11.2026)

| Plik | Zawartość |
|---|---|
| [detectors.py](anomaly/models/detectors.py) | wspólny interfejs `fit(X_train, features)` → `score(X)` (większy = większe ryzyko); reguły D13, robust D12, IF, OCSVM, LOF; siatka hiperparametrów |
| [scoring.py](anomaly/models/scoring.py) | normalizacja D8 (`ScoreNormalizer`), progi D7 (FPR ≤ 5% + max MCC, recall = 0,80), `rank_key` do sortowania alertów |
| [training.py](anomaly/models/training.py) | warianty D17, uczenie na TRAIN, strojenie na VAL, zamrożenie konfiguracji; `python -m anomaly.models.training --seed 11 22 33 44 55` |

- [x] **Reguły** (D13): 4 reguły z percentyli okien TRAIN jednostki, score = liczba naruszonych reguł (0–4). Stare reguły (noc, weekend, nieznane urządzenie, błąd, UPDATE/DELETE) usunięte razem z `RuleBasedDetector.py`.
- [x] **Robust** (D12): mediana + kaskada skal bez wykluczania cech stałych, tylko cechy ciągłe, score = średnia z 3 największych |z|. Dodana tolerancja numeryczna: szum zmiennoprzecinkowy w MAD dawał z rzędu 10¹⁵.
- [x] **IF / OCSVM / LOF** (`novelty=True`): `StandardScaler` dopasowany na TRAIN jednostki, score = −`decision_function`, `random_state` = ziarno. Siatka 19 konfiguracji, wybór według PR-AUC na VAL, jeden zestaw na wariant (METODOLOGIA D7).
- [x] **Normalizacja D8** na normalnych oknach VAL jednostki; σ zastępcze, gdy MAD = 0; remisy na 1,0 rozstrzyga nadwyżka ogona (`*_excess` w `val_scores`).
- [x] **Progi D7**: główny punkt pracy i recall = 0,80; per użytkownik próg wspólny dla roli na połączonych znormalizowanych score’ach.
- [x] **Zamrożenie przed TEST**: kod Fazy 6 odmawia wczytania TEST. `results/seed_<N>/detectors/full/frozen_config.json` (+ SHA-256) zawiera hiperparametry z wynikami całej siatki, parametry dopasowania per jednostka, normalizację, progi i metryki VAL. Modele trafiają do `models/<wariant>_<detektor>.joblib`.
- [x] Parametr `groups` w `train_seed` (podzbiór grup D14) i katalog per konfiguracja; w Fazie 7 posłuży do ablacji.
- [x] Testy: [tests/test_detectors.py](tests/test_detectors.py) (23 testy: reguły, kaskada D12, kierunek score’u ML, przycinanie hiperparametrów, normalizacja, progi, warianty, zastępstwo D12, brak dostępu do TEST). Łącznie 77 testów.

**Sprawdzone na VAL (27.11.2026, 5 ziaren, średnia; ok. 85 s na ziarno):**

| Wariant | Detektor | PR-AUC | FPR (główny próg) | Recall | MCC | FPR przy recall 0,8 |
|---|---|---|---|---|---|---|
| global | reguły | 0,19 | 0,035 | 0,25 | 0,28 | 1,00 |
| global | robust | 0,15 | 0,003 | 0,02 | 0,08 | 0,60 |
| global | IF | 0,20 | 0,043 | 0,13 | 0,12 | 0,47 |
| global | OCSVM | 0,43 | 0,026 | 0,32 | 0,39 | 0,41 |
| global | LOF | 0,46 | 0,018 | 0,33 | 0,44 | 0,42 |
| per rola | reguły | 0,41 | 0,027 | 0,50 | 0,54 | 1,00 |
| per rola | robust | 0,40 | 0,016 | 0,30 | 0,42 | 0,37 |
| per rola | IF | 0,30 | 0,032 | 0,23 | 0,26 | 0,34 |
| per rola | OCSVM | 0,59 | 0,019 | 0,50 | 0,57 | 0,29 |
| per rola | LOF | 0,52 | 0,015 | 0,41 | 0,52 | 0,41 |
| per użytkownik | reguły | 0,32 | 0,024 | 0,31 | 0,37 | 0,60 |
| per użytkownik | robust | 0,42 | 0,009 | 0,29 | 0,46 | 0,40 |
| per użytkownik | IF | 0,30 | 0,009 | 0,12 | 0,25 | 0,40 |
| per użytkownik | OCSVM | 0,58 | 0,005 | 0,40 | 0,59 | 0,31 |
| per użytkownik | LOF | 0,57 | 0,003 | 0,39 | 0,58 | 0,30 |

To wyniki **VAL, na którym stroiliśmy**, więc nie są wynikami pracy (te pochodzą z TEST, Faza 7). Udział okien anomalnych w VAL: 9,5–10% (poziom losowy PR-AUC). Uwagi:
- FPR przy głównym progu nie przekracza 5% w żadnym ziarnie (maksimum 4,97%). W wariancie per użytkownik jest zwykle dużo niższy, bo próg maksymalizujący MCC w roli wypada przy niższym FPR.
- Reguły mają dyskretny score 0–4. W wariantach global i per rola recall 0,8 osiąga się dopiero progiem „każde okno” (FPR = 1). To właściwość baseline’u, którą trzeba opisać przy H1.
- Robust global jest słaby (kaskada D12 przy cechach prawie stałych, METODOLOGIA D12).
- Wybrane hiperparametry różnią się między ziarnami (zapisane w `frozen_config.json`).
- **Poprawka z Fazy 7:** `ScoreNormalizer.to_dict()` zaokrąglał score'y referencyjne do 10 miejsc, przez co scoring z pliku przesuwał rank okien leżących na granicy progu (VAL: 1–16 FP różnicy na detektor). Wykryła to kontrola spójności Fazy 7; teraz referencja jest zapisywana w pełnej precyzji, a metryki VAL odtwarzają się dokładnie. Konfiguracje `full` trzeba zamrozić ponownie (metryki VAL z tabeli wyżej się nie zmieniają, bo były liczone w pamięci).

**Do Fazy 7:** scoring TEST wyłącznie z `frozen_config.json` i modeli `.joblib` (sprawdzenie SHA-256). Ranking alertów sortuje według `rank_key(norm, excess)`. Ablacja: `train_seed(seed, groups=[...], config="<nazwa>")`.

### Faza 7: Protokół ewaluacji ✅ (27.11.2026; TEST oceniony na commicie `63326bf`)

Protokół spisany przed pierwszym uruchomieniem TEST jako **METODOLOGIA D21** (metryki, podzbiory, RiskScore, ranking kolejki alertów, zakres ablacji, dokładne kryteria hipotez).

| Plik | Zawartość |
|---|---|
| [ablation.py](anomaly/evaluation/ablation.py) | 14 konfiguracji D14 i ich uczenie (procedura Fazy 6, tylko TRAIN/VAL) |
| [score_frozen.py](anomaly/evaluation/score_frozen.py) | score'y splitu wyłącznie z `frozen_config.json` (sprawdzenie SHA-256) i modeli `.joblib`; kontrola, że na VAL odtwarzają się alarmy z Fazy 6 |
| [risk.py](anomaly/evaluation/risk.py) | składniki RiskScore z obserwowalnych danych okna (D10, D21) |
| [metrics.py](anomaly/evaluation/metrics.py) | metryki per okno, per klasa / intensywność / klasa × intensywność, incydenty i opóźnienie (D9), analiza FP z `HARD_NORMAL_*`, ranking P@K / NDCG@K |
| [stats.py](anomaly/evaluation/stats.py) | średnia ± std, 95% CI t-Studenta, różnice sparowane po ziarnach, kryteria RQ1, H1, H2, H3′, H4′, H5 |
| [evaluate.py](anomaly/evaluation/evaluate.py), [report.py](anomaly/evaluation/report.py) | orkiestracja, wydajność systemu, `results/summary/<split>/` z `RAPORT.md` |

- [x] Metryki per okno na TEST: Precision, Recall, PR-AUC, MCC, FPR, łącznie i per klasa (D2: negatywy = okna normalne).
- [x] Metryki per incydent: recall incydentów, opóźnienie (mediana, P90), odsetek niewykrytych; łącznie i per klasa.
- [x] Ablacja: 14 konfiguracji × 5 detektorów × 3 warianty, każda z własnym strojeniem na VAL.
- [x] Udział anomalii w raporcie (poziom odniesienia PR-AUC).
- [x] Wyniki per klasa × intensywność.
- [x] Ranking kolejki alertów (P@K, NDCG@K, K = 10, 20, 50) dla 3 sortowań.
- [x] Powtarzalność: mean ± std, 95% CI, różnice sparowane (D6); `comparisons.csv` z każdym porównaniem.
- [x] Analiza błędów: udział FP z `HARD_NORMAL_*` (łącznie i per tag), FPR na oknach trudnych i zwykłych.
- [x] Wydajność: czas wykonania i zapytania/s, czas LOGGER-a i zdarzenia/s, czas joinu, budowy cech, uczenia i scoringu (µs/okno).
- [x] Każdy wynik z wersją kodu i SHA-256 użytej konfiguracji (`manifest.json` per ziarno i zbiorczo). TEST odmawia pracy na niezacommitowanym kodzie (bez `--allow-dirty`).
- [x] Testy: [tests/test_evaluation.py](tests/test_evaluation.py) (11 testów: RiskScore, metryki per okno i incydent, opóźnienie, ranking i brakujące pozycje, CI, różnice sparowane, H3′, konfiguracje ablacji, odrzucenie zmienionej konfiguracji).
- [x] **Sprawdzone na VAL** (ziarna 11 i 22, konfiguracja pełna i 4 konfiguracje H2): scoring z plików odtwarza TP/FP z Fazy 6 dla każdego detektora, wariantu i punktu pracy; raport generuje się w całości.
- [x] **Uruchomienie** (27.11.2026, commit `63326bf`, czyste drzewo): ponowne zamrożenie `full`, ablacja (70 zamrożonych konfiguracji = 5 ziaren × 14), ewaluacja VAL (odtworzenie metryk Fazy 6: OK dla każdego ziarna), ewaluacja TEST (0 problemów spójności).

**Wyniki TEST** (5 ziaren, średnia; pełne tabele z 95% CI: `results/summary/test/RAPORT.md`). Udział okien anomalnych: 11,3%.

| Wariant | Najlepszy detektor | PR-AUC | MCC | FPR | Recall | Recall incydentów |
|---|---|---|---|---|---|---|
| global | OCSVM | 0,454 | 0,382 | 0,023 | 0,289 | 0,408 |
| per rola | OCSVM | 0,521 | 0,447 | 0,021 | 0,350 | 0,489 |
| per użytkownik | OCSVM | 0,467 | 0,403 | 0,016 | 0,279 | 0,397 |
| per rola | reguły (odniesienie) | 0,339 | 0,435 | 0,026 | 0,363 | 0,502 |

| Hipoteza | Wynik | Uwagi |
|---|---|---|
| RQ1 | **niespełnione** | FPR ≤ 5% wszędzie, ale recall ≤ 0,36 (cel 0,50). Na VAL OCSVM per rola miał 0,50, a spadek na TEST wynika głównie z wariantów anomalii TEST (D16, niżej). FP to w 40–87% okna `HARD_NORMAL_*`; FPR na nich 4–16% wobec 0,2–2% na zwykłych. |
| H1 | **częściowo** (2 z 10) | Istotnie niższy FPR przy recall 0,8 dla IF per rola i LOF per użytkownik. OCSVM, robust i reguły per użytkownik też mają istotnie niższy FPR, ale recall na TEST spada o więcej niż 5 pp. Reguły nie osiągają recall 0,8 w global i per rola (FPR = 1). |
| H2 | **częściowo** (3 z 15) | Potwierdzona dla LOF i reguł global oraz reguł per użytkownik. Trójka zawsze bije Czas i Kontekst, ale zwykle nie istotnie sam Wolumen (dla IF Wolumen jest lepszy). |
| H3′ | **potwierdzona** | We wszystkich 3 wariantach OCSVM i LOF mają istotnie wyższe PR-AUC niż reguły, a per użytkownik także MCC. |
| H4′ | **odrzucona** | Kryterium „porównywalne MCC na silnych” zwykle nie spełnione, bo OCSVM jest na silnych anomaliach istotnie **lepszy** od reguł. Na słabych OCSVM ma istotnie wyższe PR-AUC we wszystkich wariantach, a LOF per użytkownik też. |
| H5 | **odrzucona** | RiskScore nie bije ani chronologii, ani samego score'u (zwykle jest gorszy). Mechanizm: w kolejce alertów norm ma 0,95–1,0, więc `0,6·norm` prawie nie różnicuje okien i o kolejności decydują severity i kontekst. Severity jest podobne dla TP i FP (ok. 0,65), a kontekst wyższy dla FP (ok. 0,27 wobec 0,05–0,10), bo `HARD_NORMAL_AFTER_HOURS` / `ALT_IP` to legalna praca z nietypowym kontekstem, a większość klas anomalii nie zmienia urządzenia, IP ani pory. Obserwacja po fakcie, protokołu D10/D21 nie zmieniamy. |

**Uogólnianie VAL → TEST (D16).** Największy spadek dotyczy `unusual_ratio`: na VAL przesunięcie proporcji w stronę najrzadszej operacji (63% incydentów z DELETE), na TEST w stronę drugiej najrzadszej (13% z DELETE, głównie INSERT/UPDATE). OCSVM per rola wykrywa 79–86% incydentów z DELETE i ok. 20–30% bez DELETE, więc PR-AUC tej klasy na TEST spada do poziomu losowego (ok. 0,04–0,08) dla wszystkich detektorów. To wynik zamierzonego testu uogólniania, nie błąd. `volume_spike` ma na TEST większy udział okien (43,7% wobec 33,3%), bo skoki TEST trwają 3–4 okna.

**Wydajność:** budowa cech ok. 1,8 s na split, uczenie konfiguracji `full` 75–112 s na ziarno, scoring 3–520 µs/okno (najwolniejszy IF per użytkownik). Tabela w `RAPORT.md`.

**Mapowanie wyników na hipotezy:**

| Hipoteza / RQ | Eksperyment | Kryterium potwierdzenia |
|---|---|---|
| RQ1 | wszystkie warianty na TEST, główny próg | średni FPR ≤ 5% i średni recall ≥ 0,50 (D7); analiza FP z `HARD_NORMAL` |
| H1 / RQ4 | global vs per rola vs per użytkownik, punkt pracy recall = 0,80 (D7) | per rola/użytkownik ma istotnie niższy FPR niż global (kryterium D6), przy recall na TEST różnym o ≤ 5 pp |
| H2 / RQ2 | ablacja | Czas+Wolumen+Kontekst istotnie lepsze (PR-AUC/MCC, kryterium D6) niż każda z tych grup osobno; dodatkowo per klasa |
| H3′ / RQ3 | 5 detektorów przy FPR ≤ 5% | ≥ 1 model ML ma wyższe PR-AUC lub MCC niż reguły na anomaliach behawioralnych |
| H4′ / RQ5 | wyniki per klasa × intensywność | anomalie silne: reguły ≈ ML (różnica MCC w granicach CI); anomalie słabe: statystyka/ML > reguły (PR-AUC) |
| H5 / RQ6 | ranking (relewancja według D19) | RiskScore istotnie lepszy w P@K/NDCG@K niż chronologia i niż sam anomaly score |

### Faza 8: Panel WPF (wymaganie inżynierskie) ✅ (27.11.2026)

Panel czyta gotowe wyniki TEST z bazy `AnomalyResults`. Nic nie liczy od nowa: score'y, alarmy i RiskScore pochodzą z Fazy 7, a hiperparametry, progi i parametry robust/reguł z zamrożonej konfiguracji Fazy 6.

| Element | Plik | Zawartość |
|---|---|---|
| Schemat bazy | [sql_files/anomaly_results_schema.sql](sql_files/anomaly_results_schema.sql) | `Runs`, `Models`, `Thresholds`, `Windows`, `WindowFeatures`, `Alerts`, `Incidents`, `Events` (idempotentny) |
| Eksport | [anomaly/export/results_db.py](anomaly/export/results_db.py) | jedna transakcja na ziarno, ponowny eksport zastępuje przebieg; kontrole: SHA-256 konfiguracji = manifest ewaluacji, okna score'ów = okna cech, norm alertu ≥ próg grupy, każde zdarzenie ma okno |
| Dane, filtry, ViewModele, eksport | [PANEL/AnomalyPanel.Core](PANEL/AnomalyPanel.Core) | Dapper + Microsoft.Data.SqlClient, CommunityToolkit.Mvvm, QuestPDF (licencja Community); bez zależności od WPF, więc ViewModele są testowalne |
| Okno | [PANEL/AnomalyPanel](PANEL/AnomalyPanel) | WPF .NET 9, MVVM |
| Testy | [PANEL/AnomalyPanel.Tests](PANEL/AnomalyPanel.Tests), [tests/test_results_db.py](tests/test_results_db.py) | xUnit 40 (37 jednostkowych, 3 integracyjne na bazie), pytest 4 |

- [x] Baza wyników `AnomalyResults` (TEST, konfiguracja `full`, 5 ziaren): 8 080 okien, 75 modeli, 5 488 alertów, 727 200 wartości cech, 43 652 zdarzenia XE, 371 incydentów; eksport ok. 10 s na ziarno. Liczba alertów i TP każdego modelu zgadza się z metrykami Fazy 7 (75/75).
- [x] Lista alertów modelu (wybór: ziarno, wariant, detektor) sortowana według RiskScore; przełącznik: score detektora (D8: norm, potem nadwyżka ogona) i chronologia; remisy chronologicznie (D21).
- [x] Filtry: użytkownik, rola, zakres dni, klasa anomalii (ground truth, w tym „norma” = fałszywe alarmy i „trudna norma”), minimalny poziom ryzyka.
- [x] **Poziomy ryzyka** (tylko do prezentacji, nie wchodzą do ewaluacji): niski < 0,70 ≤ średni < 0,80 ≤ wysoki < 0,90 ≤ krytyczny. Progi ustalone z góry; RiskScore alertu leży w [0,6; 1], a progi dzielą go mniej więcej na kwartyle alertów TEST.
- [x] Szczegóły alertu: składniki RiskScore z wkładem do wyniku i flagami kontekstu; model, hiperparametry, grupa i wartość progu głównego, metryki VAL progu; cechy o największym |z| robust (D12) z wartością, medianą i [P1; P99] jednostki modelu (TRAIN), medianą użytkownika (TRAIN) i naruszeniem reguły D13.
- [x] Ścieżka alertu: okno → zdarzenia XE (czas, operacja, tabela, czas wykonania, odczyty, wiersze) → tekst SQL i pola XE (event_uid, numer zdarzenia, czas fizyczny, połączenie, login, host, urządzenie/IP); ścieżka do eksportu LOGGER-a.
- [x] Eksport: CSV bieżącej kolejki (RFC 4180, kropka dziesiętna) i raport PDF (model, wersja konfiguracji, filtry, kolejka, szczegóły wybranego alertu ze zdarzeniami i SQL); CSV pomiarów czasu.
- [x] Pomiar czasu: każde zapytanie SQL, ładowanie kolejki, szczegóły alertu, eksport, start aplikacji (zakładka „Pomiary czasu”, pasek stanu).
- [x] Testy inżynierskie: poprawność filtrowania i sortowania (w tym remisy i granice poziomów), ViewModele (ładowanie, filtry, zmiana modelu, wyścig wolnego i szybkiego ładowania, błąd bazy, szczegóły, eksport), zgodność filtrów w pamięci z `WHERE` w SQL, ścieżka alertu do zdarzeń XE na prawdziwej bazie, czas dla N alertów, stabilność (300 cykli przeładowanie/filtr/wybór).

**Pomiary** (laptop deweloperski, SQL Server lokalnie):

| Operacja | Czas |
|---|---|
| Zapytanie o alerty modelu (75 modeli) | mediana 6 ms, maks. 19 ms |
| Ładowanie kolejki w panelu (119 alertów, z SQL) | 95–130 ms przy pierwszym połączeniu, potem ok. 35 ms |
| Szczegóły alertu (cechy + zdarzenia + incydenty) | 19–100 ms |
| Start aplikacji do pierwszej kolejki | ok. 1,9 s |
| Filtrowanie i sortowanie w pamięci, N = 1 000 / 10 000 / 50 000 | 4 / 2 / 5 ms |
| Ładowanie kolejki w ViewModelu, N = 10 000 (bez SQL) | 34 ms |
| Stabilność: 300 cykli | przyrost pamięci zarządzanej 1,1 MB, bez błędów |

**Poprawka po drodze:** domyślny parser `pandas.read_csv` zmieniał ostatni bit części score'ów zapisanych w CSV (norm alertu wychodził o 1 ULP poniżej progu). Metryk Fazy 7 to nie dotyczy, bo liczono je w pamięci przed zapisem. Eksporter czyta CSV z `float_precision="round_trip"` i sprawdza „norm alertu ≥ próg”. Błąd wykrył test integracyjny panelu.

### Faza 9: EDA, raporty i tekst pracy

- [ ] Napisać nową EDA na nowych danych (okna + zdarzenia), per rola; poprzednia wersja: [archiwum/stary_pipeline/eda_pipeline.py](archiwum/stary_pipeline/eda_pipeline.py).
- [ ] Wygenerować od nowa `RAPORT_EDA.md` i `RAPORT_MODELOWANIE_ML.md`. Obecne opisują inny profil niż kod (np. host `CEO-HOME-PC`, inne wagi tabel) i mają martwy link do `ml_anomaly_pipeline.py`.
- [ ] W pracy dodać rozdział **Ograniczenia**: dane syntetyczne, czas symulowany (kompresja czasu wykonania), IP i host deklarowane przez klienta (`WSID`), brak walidacji na danych produkcyjnych, mała liczba seedów.
- [ ] W pracy opisać jednoznacznie: jednostkę analizy, mechanizm ground truth, procedurę progowania, definicję opóźnienia, wzór RiskScore, wybór detektora statystycznego (median + MAD).

---

## 3. Mapa: uwagi recenzenta → zadania

| Uwaga z oceny | Priorytet | Gdzie w planie |
|---|---|---|
| Brak jednostki analizy | KRYTYCZNY | D1, D2, Faza 5 |
| Ground truth po XE | KRYTYCZNY | Faza 2 |
| Wielu użytkowników na rolę | WYSOKI | D4, Faza 3, Faza 4 |
| Doprecyzować baseline statystyczny | WYSOKI | Faza 6 (median + MAD per użytkownik) |
| Wspólna procedura progowania | WYSOKI | D7, Faza 6 |
| Generator zbyt łatwy / tautologiczny | WYSOKI | Faza 3 (trudne przypadki, nakładanie), Faza 6 (progi reguł z danych) |
| LOF novelty | – | już jest `novelty=True`; zostaje |
| Różne skale score’ów | ŚREDNI | D8, Faza 6 |
| RiskScore | ŚREDNI | D10, Faza 7 |
| Seedy i CI | ŚREDNI | D6, Faza 7 |
| Behawior vs naruszenia polityki | ŚREDNI | D3: naruszenia wyłączone z zakresu pracy; H3/H4 przeformułowane (METODOLOGIA, sekcja C) |
| Definicja opóźnienia | ŚREDNI | D9, Faza 7 |
| Walidacja zewnętrzna | – | Faza 9 (ograniczenia) |
| WPF | NISKI | Faza 8 ✅ |

## 4. Mapa: błędy znalezione w kodzie → zadania

| Problem | Plik | Faza |
|---|---|---|
| ML nie używa danych z XE, metryki są losowane w Pythonie | `AnomalyDataLoader.py` (archiwum) | 5 ✅ |
| Cechy per zdarzenie nie widzą wolumenu, proporcji ani rzadkości | cały pipeline (zastąpiony `windowing.py`) | 5 ✅ |
| Normalny ruch w weekend | `CEOLogGenerator._resolve_day_in_week` (usunięty) | 3 ✅ |
| Niekumulatywne odstępy w sesji | `CEOLogGenerator.py` (usunięty) | 3 ✅ |
| „Rzadki zasób” nie jest rzadki (Product ~15% SELECT-ów) | `AnomalyConfig.py` (usunięty) | 3 ✅ |
| `IsError` zawsze 0 (warunek `severity_level >= 11` na skali 1–5); cecha do usunięcia (D3) | `AnomalyDataLoader.py:90` (archiwum) | 5 ✅ |
| `DayOfWeek`: C# 0=niedziela, pandas 0=poniedziałek | `MLFeatureExporter.cs`, loader | 2 |
| `SqlTextLength` zawiera komentarz z etykietą | `XeLogReader.cs` | 2 |
| Błąd SQL daje 2 rekordy | `XeLogReader.cs` | 2 |
| `APP=` ucinane na `;` | `SessionConnectionManager.py` | 2 |
| Health-check `SELECT 1` przy każdym zapytaniu | `SessionConnectionManager.py` | 4 ✅ |
| `cleanup_simulation_data` zawsze się wywala | `DBWorkloadExecutor.py` (zastąpione restore’em) | 4 ✅ |
| Zdublowane `EventUid` z `rpc_completed` (tekst z akcji `sql_text`) | `XeLogReader.cs` | 4 ✅ |
| Dwie kolumny `operation_type` w tabeli połączonej | `xe_join.py` | 4 ✅ |
| Zła ścieżka SQL w `setup_ceo_user.py` | `setup_ceo_user.py` | 1 |
| MAD = 0 → eksplozja z-score (FPR 87%) | `RobustZScoreDetector.py` (zastąpiony `detectors.py`) | 6 ✅ |
| Grupa `operations` pominięta w ablacji | `ml_anomaly_pipeline.py` (archiwum; grupy D14 w `windowing.FEATURE_GROUPS`) | 7 |
| Hasło i ścieżki zapisane w kodzie, `bin/obj/.vs` w gicie | kilka plików | 1 |
| Wyniki, dane i raporty z różnych wersji kodu | `results_ml/`, `LOGI/`, raporty | 1, 9 |

## 5. Co zostaje bez zmian (zgodnie z oceną)

- cel badawczy, RQ1–RQ6 i H1–H5,
- cztery klasy anomalii behawioralnych,
- zestaw detektorów: reguły, robust, Isolation Forest, One-Class SVM, LOF,
- metryki: Precision, Recall, PR-AUC, MCC, FPR, P@K, NDCG@K,
- podział TRAIN (sama norma) / VAL / TEST z perturbacją,
- Extended Events jako źródło logów, LOGGER w .NET, WPF jako warstwa prezentacji,
- jawne ograniczenie zakresu do prototypu badawczo-inżynierskiego.

**Nie dodawać** nowych modeli ani nowych klas anomalii. Więcej wartości da dopracowanie fundamentów.

## 6. Kolejność prac w skrócie

1. **D1–D10** zapisane w `METODOLOGIA.md`.
2. **Faza 1** (porządki).
3. **Faza 2 + Faza 3** (ground truth + generator). Można je robić równolegle, spotykają się na `EventUid`.
4. **Faza 4** (role w SQL, orkiestracja), z pierwszym pełnym przebiegiem na 1 seedzie i 1 roli jako „pionowym przekrojem”.
5. **Faza 5 + Faza 6** (okna + detektory) na tym przekroju; sprawdzić, czy wyniki mają sens, dopiero potem skalować.
6. Rozszerzenie na wszystkie role i 5 seedów, **Faza 7** (pełna ewaluacja).
7. **Faza 8** (WPF) na gotowych wynikach w bazie.
8. **Faza 9** (EDA, raporty, tekst).

Zasada dla kroku 4: najpierw cały łańcuch dla CEO na jednym seedzie, potem skalowanie. Pozwala to wcześnie wykryć problemy z korelacją XE i oknami, zanim wykona się kilkadziesiąt tysięcy zapytań.
