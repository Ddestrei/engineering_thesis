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
2. Pipeline ML **nie czyta logów z Extended Events**: [AnomalyDataLoader.py:93-121](anomaly/features/AnomalyDataLoader.py#L93-L121) losuje metryki SQL w Pythonie.
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
- [x] Hasło usunięte z kodu. Python pobiera je przez [anomaly/settings.py](anomaly/settings.py) ze zmiennej `ANOMALY_DB_PASSWORD_<LOGIN>` albo z `config.local.json` (wzór: [config.example.json](config.example.json)); [create_ceo_user.sql](sql_files/create_ceo_user.sql) używa zmiennej SQLCMD `$(CEO_PASSWORD)`. **Hasło zostaje w historii gita** (zostało wypchnięte na GitHub), więc trzeba je zmienić na serwerze SQL.
- [x] Ścieżka `C:\Users\ddest\...\LOGI` w [Program.cs](LOGGER/Program.cs) zastąpiona wyszukaniem katalogu repozytorium (`data/xe_json/`), z nadpisaniem przez argument albo `ANOMALY_XE_JSON_DIR`.
- [x] [setup_ceo_user.py](anomaly/execution/setup_ceo_user.py): poprawna ścieżka do `sql_files/`, podział batchy po liniach `GO`, kod wyjścia ≠ 0 przy błędach.
- [x] [requirements.txt](requirements.txt) z wersjami.
- [x] Pakiet [anomaly/](anomaly/) z podkatalogami `generator/`, `execution/`, `features/`, `models/`, `evaluation/`; wspólne ścieżki w [anomaly/paths.py](anomaly/paths.py). Uruchamianie: `python -m anomaly.<moduł>` z katalogu głównego.
- [x] Stare wyniki i dane przeniesione do [archiwum/](archiwum/README.md) z opisem, dlaczego są nieaktualne.
- [x] [README.md](README.md) z instrukcją uruchomienia.

**Sprawdzone:** 25 modułów się importuje; generator, podgląd wykonania (`--dry-run`), eksperyment ML i EDA działają z nowych ścieżek; LOGGER kompiluje się bez błędów i ostrzeżeń. Nie sprawdzono połączenia z SQL Server (wymaga hasła i działającej instancji).

### Faza 2: Ground truth i korelacja z XE ✅ (zakończona 25.11.2026)

Cel: każde zdarzenie z XE da się w 100% deterministycznie powiązać z wierszem generatora. Etykieta nie trafia do cech.

**Przyjęty podział** (decyzja z 25.11): z logów XE pochodzą wszystkie fakty obserwowalne (metryki wykonania, tekst SQL, host, urządzenie, IP); z generatora wyłącznie **czas symulowany i etykiety** (kolumny `gt_*`).

- [x] **Generator nadaje identyfikatory** ([CEOLogGenerator.py](anomaly/generator/CEOLogGenerator.py)): `run_id` (`<split>-s<seed>`), `event_uid` (`<run_id>-<nr>`), `incident_id`. Zapisuje `data/generated/events_<split>.csv` i `incidents_<split>.csv`. Kolumna `intensity` jest na razie pusta (wypełni ją Faza 3, D15).
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
- eksperyment ML (`ml_anomaly_pipeline`) nadal czyta pliki generatora, a nie `data/joined/`; zmienia to Faza 5,
- `eda_pipeline` czyta tylko stary format JSON (archiwum); przepisanie w Fazie 9,
- health-check `SELECT 1` nadal generuje zdarzenia XE bez znacznika (LOGGER je pomija); usunięcie w Fazie 4,
- `cleanup_simulation_data` nie działa (Faza 4). W tabeli Customer jest 849 rekordów symulacyjnych.

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

### Faza 4: SQL Server i wykonanie obciążenia

- [ ] Skrypt SQL tworzący **role bazodanowe** (`CREATE ROLE`) z uprawnieniami z dokumentu oraz 30 loginów/użytkowników przypisanych do ról (zamiast jednego `create_ceo_user.sql`).
- [ ] Dodać do bazy tabele, których dziś nie używa żaden profil (SalesOrderDetail, Address, CustomerAddress, ProductModel, ProductDescription, ProductCategory…), w szablonach SQL ([SQLQueryBuilder.py](anomaly/execution/SQLQueryBuilder.py)), łącznie z UPDATE/DELETE i JOIN-ami opisanymi w profilach.
- [ ] Usuwanie/aktualizacja danych symulacyjnych: operacje UPDATE/DELETE muszą dotyczyć **rekordów symulacyjnych** (np. wstawionych wcześniej z markerem `SIM_`), żeby nie niszczyć bazy. Przed każdym przebiegiem przywracać bazę z `.bak` (powtarzalność metryk XE).
- [ ] Naprawić `cleanup_simulation_data` ([DBWorkloadExecutor.py:153](anomaly/execution/DBWorkloadExecutor.py#L153)): złe argumenty, a CEO ma `DENY DELETE`. Wykonywać jako admin albo zastąpić restore’em z `.bak`.
- [ ] Usunąć health-check `SELECT 1` przy każdym zapytaniu ([SessionConnectionManager.py:93](anomaly/execution/SessionConnectionManager.py#L93)), bo generuje zbędne zdarzenia.
- [ ] Sesja XE: dodać `sql_statement_completed` lub pozostać przy batchach (udokumentować wybór). Każdy przebieg zapisywać do osobnego pliku `.xel` (`<RunId>.xel`).
- [ ] **Jeden skrypt orkiestrujący** `run_experiment.py --seed N`: restore bazy → generator → wykonanie → odczekanie na flush XE → LOGGER → join → cechy. Wszystkie seedy przechodzą tę samą ścieżkę.

- [ ] **Format z Fazy 3** (D20): wykonawca czyta `record_key` i `parent_keys` z `events_<split>.csv`; klucz zamienia na deterministyczny `rowguid` (np. `uuid5(namespace, record_key)`), a UPDATE/DELETE i odwołania do rodziców robi po `rowguid` (każda tabela SalesLT ma unikalną kolumnę `rowguid`). Rodzic z `SalesOrderHeader` w INSERT pozycji przez podzapytanie po `rowguid`.
- [ ] **Rekordy startowe:** przed każdym przebiegiem (po restore z `.bak`) wstawić `seed_records_<split>.csv` jako administrator, w kolejności rodzice → dzieci, z `rowguid` z klucza. Bez znacznika gt, więc LOGGER je pomija.
- [ ] **30 loginów** `SIM_<ROLA>_<nr>` (lista w `data/generated/users.csv`), przypisanych do ról bazodanowych; hasła przez `config.local.json` / zmienne środowiskowe (Faza 1).

**Gotowe, gdy:** jeden seed przechodzi cały łańcuch bez ręcznych kroków, a pokrycie joinu = 100%.

### Faza 5: Inżynieria cech (okna czasowe)

- [ ] Nowy moduł `features/windowing.py`: z rekordów XE (po joinie) budować okna `UserId × 30 min`. **Usunąć** syntetyczne metryki z [AnomalyDataLoader.py](anomaly/features/AnomalyDataLoader.py) i odczyt CSV generatora jako źródła cech.
- [ ] Cechy okna i ich grupy dokładnie według tabeli D14 (6 grup: Czas, Wolumen, Kontekst, Proporcje operacji, Zasoby, Wykonanie). Okna wyrównane według D11.

- [ ] Wszystkie statystyki „rzadkości” i mediany liczyć **wyłącznie z TRAIN** (zapobiega przeciekowi), na poziomie wariantu modelowania: populacja, rola albo użytkownik (D17).
- [ ] Usunąć cechy błędów (`IsError`, `ErrorNumber`) z wektora cech (D3).

**Gotowe, gdy:** jedna tabela okien na split z kolumnami `UserId, RoleId, WindowStart, IsAnomaly, IncidentIds, <cechy>`, bez kolumn wyprowadzonych z etykiet.

### Faza 6: Detektory i progowanie

- [ ] **Reguły** ([RuleBasedDetector.py](anomaly/models/RuleBasedDetector.py)): przepisać według D13. 4 reguły z progami z percentyli TRAIN danej jednostki modelowania, score = liczba naruszonych reguł. Obecne reguły (noc, weekend, nieznane urządzenie, błąd, UPDATE/DELETE) wyrzucić; część z nich dotyczy naruszeń, które są poza zakresem (D3).
- [ ] **Robust statistics** ([RobustZScoreDetector.py](anomaly/models/RobustZScoreDetector.py)): przepisać według D12. Mediana + MAD per jednostka modelowania, kaskada zastępczych skal przy MAD = 0 (bez wykluczania cech stałych), score = średnia z 3 największych |z|, statystyki roli dla użytkowników z < 30 oknami w TRAIN.
- [ ] **Isolation Forest / One-Class SVM / LOF (`novelty=True`, już jest)**: hiperparametry z siatki strojonej na VAL (IF: `n_estimators`, `max_samples`; OCSVM: `nu`, `gamma`; LOF: `n_neighbors`). Skalowanie dopasowywane tylko na TRAIN.
- [ ] **Wspólny interfejs**: każdy detektor ma `fit(train) → score(X)`, gdzie większy score oznacza większe ryzyko, oraz normalizację do [0,1] według D8 (wygładzona ECDF na normalnych oknach VAL z przedłużonym ogonem).
- [ ] **Progowanie** ([ThresholdTuner.py](anomaly/models/ThresholdTuner.py)) według D7:
  - główny punkt pracy: FPR ≤ 5% na VAL, w ramach tego maksymalne MCC (dziś domyślny jest tryb `mcc` bez ograniczenia FPR, przez co FPR na teście wynosił 31–87%),
  - dla modeli per użytkownik próg wspólny dla roli, na połączonych i znormalizowanych (D8) score’ach VAL,
  - drugi punkt pracy dla H1: próg dający recall = 0,80 na VAL,
  - progi, hiperparametry i parametry normalizacji zapisywane do pliku przed uruchomieniem TEST.
- [ ] **Trzy warianty modelowania (RQ4/H1)**: global (wszyscy użytkownicy), per rola, per użytkownik. Te same cechy i ta sama procedura progowania.

### Faza 7: Protokół ewaluacji

- [ ] Metryki per okno na TEST: Precision, Recall, PR-AUC, MCC, FPR, **osobno dla każdej klasy anomalii** (wymagane przez RQ5) i łącznie.
- [ ] Metryki per incydent: recall incydentów i **opóźnienie wykrycia** (D9): mediana i P90.
- [ ] **Ablacja (RQ2/H2)**: konfiguracje dokładnie według D14 (pojedyncze grupy Czas/Wolumen/Kontekst, ich pary, trójka, pełny zestaw, leave-one-group-out dla 6 grup). Wyniki łącznie **i per klasa anomalii**.
- [ ] **Udział anomalii**: raportować rzeczywisty odsetek okien anomalnych w VAL i TEST (cel 5–10%, D18) jako poziom odniesienia dla Precision i PR-AUC.
- [ ] **Wyniki per klasa × intensywność (RQ5, H4′)**: osobno dla anomalii silnych i słabych (METODOLOGIA D15).
- [ ] **Ranking alertów (RQ6/H5)**: Precision@K i NDCG@K (K = 10, 20, 50; relewancja = severity incydentu według D19) dla trzech sortowań: chronologicznego, według pojedynczego anomaly score i według RiskScore.
- [ ] **Powtarzalność**: 5 seedów (D6), mean ± std, 95% CI. Porównania metod i wariantów według kryterium istotności z D6 (95% CI różnicy sparowanej po ziarnach).
- [ ] **Analiza błędów**: ile FP pochodzi z `HARD_NORMAL_*`; to bezpośrednio odpowiada na RQ1 (akceptowalny FPR).
- [ ] **Wydajność systemu**: czas importu XE, zdarzenia/s w LOGGER, czas budowy cech, czas scoringu.
- [ ] Wszystkie wyniki zapisywać z `RunId`, wersją kodu (hash commita) i konfiguracją, żeby każdą liczbę w pracy dało się odtworzyć.

**Mapowanie wyników na hipotezy:**

| Hipoteza / RQ | Eksperyment | Kryterium potwierdzenia |
|---|---|---|
| RQ1 | wszystkie warianty na TEST, główny próg | średni FPR ≤ 5% i średni recall ≥ 0,50 (D7); analiza FP z `HARD_NORMAL` |
| H1 / RQ4 | global vs per rola vs per użytkownik, punkt pracy recall = 0,80 (D7) | per rola/użytkownik ma istotnie niższy FPR niż global (kryterium D6), przy recall na TEST różnym o ≤ 5 pp |
| H2 / RQ2 | ablacja | Czas+Wolumen+Kontekst istotnie lepsze (PR-AUC/MCC, kryterium D6) niż każda z tych grup osobno; dodatkowo per klasa |
| H3′ / RQ3 | 5 detektorów przy FPR ≤ 5% | ≥ 1 model ML ma wyższe PR-AUC lub MCC niż reguły na anomaliach behawioralnych |
| H4′ / RQ5 | wyniki per klasa × intensywność | anomalie silne: reguły ≈ ML (różnica MCC w granicach CI); anomalie słabe: statystyka/ML > reguły (PR-AUC) |
| H5 / RQ6 | ranking (relewancja według D19) | RiskScore istotnie lepszy w P@K/NDCG@K niż chronologia i niż sam anomaly score |

### Faza 8: Panel WPF (wymaganie inżynierskie)

Obecnie w repozytorium nie ma projektu WPF.

- [ ] Wyniki pipeline’u zapisywać do bazy (osobna baza, np. `AnomalyResults`): tabele `Runs`, `Windows`, `WindowFeatures`, `Alerts`, `Incidents`, `Models`.
- [ ] Projekt WPF (.NET 9, MVVM), funkcje zgodne z oceną:
  - lista alertów sortowana po RiskScore,
  - filtry: użytkownik, rola, zakres czasu, klasa anomalii, poziom ryzyka,
  - szczegóły alertu: cechy, które najbardziej odbiegały od normy (np. największe |z| z robust), oraz porównanie z profilem normalnym użytkownika,
  - model i próg, który wygenerował alert,
  - ścieżka alertu: okno → zdarzenia XE → tekst SQL,
  - eksport raportu (PDF/CSV),
  - pomiar czasu ładowania i czasu zapytań SQL.
- [ ] Testy inżynierskie panelu: poprawność filtrowania (testy jednostkowe ViewModeli), czas ładowania dla N alertów, stabilność.

### Faza 9: EDA, raporty i tekst pracy

- [ ] Przepisać [eda_pipeline.py](anomaly/evaluation/eda_pipeline.py) na nowe dane (okna + zdarzenia), per rola.
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
| WPF | NISKI | Faza 8 |

## 4. Mapa: błędy znalezione w kodzie → zadania

| Problem | Plik | Faza |
|---|---|---|
| ML nie używa danych z XE, metryki są losowane w Pythonie | `AnomalyDataLoader.py` | 5 |
| Cechy per zdarzenie nie widzą wolumenu, proporcji ani rzadkości | cały pipeline | 5 |
| Normalny ruch w weekend | `CEOLogGenerator._resolve_day_in_week` (usunięty) | 3 ✅ |
| Niekumulatywne odstępy w sesji | `CEOLogGenerator.py` (usunięty) | 3 ✅ |
| „Rzadki zasób” nie jest rzadki (Product ~15% SELECT-ów) | `AnomalyConfig.py` (usunięty) | 3 ✅ |
| `IsError` zawsze 0 (warunek `severity_level >= 11` na skali 1–5); cecha do usunięcia (D3) | `AnomalyDataLoader.py:90` | 5 |
| `DayOfWeek`: C# 0=niedziela, pandas 0=poniedziałek | `MLFeatureExporter.cs`, loader | 2 |
| `SqlTextLength` zawiera komentarz z etykietą | `XeLogReader.cs` | 2 |
| Błąd SQL daje 2 rekordy | `XeLogReader.cs` | 2 |
| `APP=` ucinane na `;` | `SessionConnectionManager.py` | 2 |
| Health-check `SELECT 1` przy każdym zapytaniu | `SessionConnectionManager.py` | 4 |
| `cleanup_simulation_data` zawsze się wywala | `DBWorkloadExecutor.py` | 4 |
| Zła ścieżka SQL w `setup_ceo_user.py` | `setup_ceo_user.py` | 1 |
| MAD = 0 → eksplozja z-score (FPR 87%) | `RobustZScoreDetector.py` | 6 |
| Grupa `operations` pominięta w ablacji | `ml_anomaly_pipeline.py` | 7 |
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
