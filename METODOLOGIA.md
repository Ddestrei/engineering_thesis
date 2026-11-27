# Metodologia eksperymentu: decyzje zamrożone

**Status:** zamrożone 25.11.2026. Przed uruchomieniem eksperymentów na zbiorze testowym.
**Zasada:** po pierwszym uruchomieniu na TEST żadnej decyzji nie zmienia się bez wpisu w sekcji „Rejestr zmian” na końcu dokumentu (data, co zmieniono, dlaczego). Chroni to przed dopasowywaniem metody do wyników testowych.

Decyzje D1–D21. Oznaczenia: TRAIN, VAL, TEST to zbiory treningowy, walidacyjny i testowy. „Okno” to próbka analityczna zdefiniowana w D1.

---

## A. Zakres i dane

### D1. Jednostka analizy
Próbką jest para **UserId + 30-minutowe okno stałe (tumbling)**. Próbką jest tylko okno z ≥ 1 operacją. Puste okna nie są próbkami, a aktywność w „martwych” godzinach widać w cechach czasowych (D14).

**Uzasadnienie:** trzy z czterech klas anomalii (wzrost wolumenu, nietypowa proporcja, rzadki zasób) to cechy agregatu, nie pojedynczego zapytania. 30 minut to kompromis: okno jest wystarczająco krótkie, żeby skok wolumenu nie rozmył się w normie, i wystarczająco długie, żeby proporcje operacji były stabilne. Analiza czułości dla 15 i 60 minut jest opcjonalna i nie należy do głównego eksperymentu.

### D2. Etykieta okna
- Okno jest **anomalne**, jeśli zawiera ≥ 1 zdarzenie wstrzykniętego incydentu. Okno przechowuje listę `IncidentId` i klas anomalii.
- Incydent obejmujący kilka okien oznacza każde z nich. Metryki per okno liczą je osobno, a metryki per incydent (D9) łączą je po `IncidentId`.
- **Metryki per klasa:** dla klasy *c* pozytywami są okna zawierające incydent klasy *c*, a negatywami wszystkie okna normalne. Okna z innymi klasami są pomijane w tej metryce.

**Uzasadnienie:** reguła „≥ 1 zdarzenie” jest jednoznaczna i odpowiada praktyce, w której analityk ma dostać alert, jeśli w oknie wydarzyło się cokolwiek podejrzanego.

### D3. Zakres benchmarku
Badane są **wyłącznie 4 klasy anomalii behawioralnych**: nietypowa pora, gwałtowny wzrost liczby operacji, nietypowa proporcja operacji i dostęp do rzadko używanego zasobu. **Naruszenia uprawnień i polityki** (operacje zabronione, błędy 229, nieznane tabele) **są poza zakresem pracy**.

Konsekwencje:
1. Generator tworzy wyłącznie operacje **zgodne z uprawnieniami** roli, zarówno w normie, jak i w anomaliach. Anomalia polega na *nietypowym użyciu legalnych uprawnień*.
2. Cechy błędów (`IsError`, numer błędu) nie wchodzą do wektora cech.
3. Hipotezy H3 i H4 zostają przeformułowane (sekcja C), bo w pierwotnym brzmieniu odwołują się do naruszeń polityki.

**Uzasadnienie:** naruszenie uprawnień jest wykrywalne deterministycznie (SQL Server zwraca błąd) i nie wymaga uczenia maszynowego. Włączenie takich scenariuszy sztucznie poprawiłoby wyniki reguł i zaciemniło porównanie metod na właściwym problemie.

### D4. Populacja
**6 ról × 5 użytkowników = 30 użytkowników.** Role i uprawnienia według dokumentu „Poprawiony schemat użycia”: CEO, CFO, Marketing Manager, Marketing, Sales Manager, Sales. Każdy użytkownik dziedziczy profil roli z indywidualną perturbacją parametrów (D15).

**Uzasadnienie:** przy jednym użytkowniku na rolę nie da się oddzielić efektu modelowania roli od modelowania konkretnej osoby (RQ4/H1). Pięciu użytkowników to minimum, przy którym model roli uczy się na rzeczywiście różnych osobach.

### D5. Podział danych
| Zbiór | Okres | Zawartość |
|---|---|---|
| TRAIN | 2 miesiące | wyłącznie norma (łącznie z trudnymi przypadkami legalnymi, D15) |
| VAL | 1 miesiąc | norma + anomalie wariantu walidacyjnego |
| TEST | 1 miesiąc | norma o perturbowanych parametrach + anomalie wariantu testowego (D16) |

Okresy następują po sobie chronologicznie i nie nakładają się.

**Uzasadnienie:** przy około 10 oknach tygodniowo na użytkownika jeden miesiąc daje za mało okien do modeli per użytkownik. Dwa miesiące to około 80 okien. Mało, ale wystarcza przy progowaniu na poziomie roli (D7).

### D6. Powtórzenia
- **5 ziaren: 11, 22, 33, 44, 55**, ustalonych z góry.
- Ziarno steruje **całym przebiegiem**: generatorem, losowaniem treści zapytań w `SQLQueryBuilder` (dziś używa nieziarnistego `random`, do poprawienia) oraz `random_state` modeli.
- Z ziarna głównego *s* wyprowadzane są niezależne pod-ziarna dla profili użytkowników, TRAIN, VAL i TEST (`numpy.random.SeedSequence(s).spawn(4)`). Zbiory są więc statystycznie niezależne, ale cały przebieg jest odtwarzalny z jednej liczby.
- Każde ziarno to pełny przebieg: przywrócenie bazy z `.bak` → wykonanie → XE → LOGGER → cechy → modele.
- Raport: średnia ± odchylenie standardowe oraz 95% CI z rozkładu t-Studenta (n = 5, 4 stopnie swobody).
- **Kryterium istotności różnicy** między dwiema metodami lub wariantami (używane w H1, H2, H3′, H4′, H5): dla każdego ziarna liczona jest różnica metryki (np. ΔMCC), a z 5 różnic 95% CI z rozkładu t. Różnica jest **istotna**, gdy przedział nie zawiera 0. Jest **porównywalna**, gdy przedział zawiera 0. Nie stosuje się testu Wilcoxona, bo przy n = 5 najmniejsze osiągalne p (dwustronnie) wynosi 0,0625, więc test nigdy nie dałby istotności na poziomie 0,05.

**Uzasadnienie:** pojedynczy przebieg nie pozwala odróżnić różnicy między metodami od przypadku. Przy n = 5 przedziały ufności będą szerokie. To świadomy kompromis z kosztem wykonania zapytań na bazie i zostanie opisany w ograniczeniach.

---

## B. Detekcja

### D7. Progowanie
- Każdy detektor w każdym wariancie modelowania (D17) ma próg dobierany **wyłącznie na VAL**: spośród progów spełniających **FPR ≤ 5%** na oknach normalnych wybierany jest próg o **maksymalnym MCC**.
- Próg, hiperparametry i parametry normalizacji są **zapisywane do pliku przed uruchomieniem TEST** i nie są później zmieniane.
- **Modele per użytkownik:** model uczony jest osobno dla każdego użytkownika, ale **próg dobierany jest wspólnie dla roli**, na połączonych oknach VAL wszystkich jej użytkowników, po normalizacji score’ów (D8).

- **Drugi punkt pracy, tylko dla H1:** próg dobrany na VAL tak, aby recall = 0,80. Na TEST porównywany jest FPR wariantów global / per rola / per użytkownik przy tej samej czułości. Jest to konieczne, bo przy głównym progowaniu (FPR ≤ 5%) wszystkie warianty mają FPR bliski 5% z konstrukcji, więc hipotezy „niższy FPR przy porównywalnej czułości” nie dałoby się na nim sprawdzić.
- **Kryterium RQ1** (ustalone a priori): profil normy uznaje się za zachowujący akceptowalny poziom fałszywych alarmów, jeżeli na TEST przy głównym progu średni FPR ≤ 5%, a średni recall ≥ 0,50.

- **Hiperparametry (IF, OCSVM, LOF)** wybierane są na VAL według **PR-AUC** na połączonych, znormalizowanych (D8) score’ach wariantu. Jest jeden zestaw na wariant, wspólny dla wszystkich jego jednostek, bo jeden użytkownik ma w VAL tylko kilka okien anomalnych. Siatka: IF `n_estimators` {100, 300} × `max_samples` {64, 128, 256}; OCSVM (RBF) `nu` {0,01, 0,05, 0,1} × `gamma` {0,3, 1, 3} × `'scale'`; LOF (`novelty=True`) `n_neighbors` {5, 10, 20, 35}. `max_samples` i `n_neighbors` są przycinane do liczby okien TRAIN jednostki. Skalowanie (`StandardScaler`) dopasowywane jest na TRAIN jednostki, `random_state` = ziarno (D6).
- **Szczegóły progu:** alarm, gdy `norm_score ≥ próg`. Przy remisie MCC wybierany jest wyższy próg. Dopuszczalny jest też próg „brak alarmów”, gdy żaden inny nie spełnia FPR ≤ 5%. Punkt recall = 0,80 to najwyższy próg z recall ≥ 0,80 na VAL.
- **Zastępstwo z D12** (< 30 okien TRAIN → okna roli) dotyczy w wariancie per użytkownik wszystkich detektorów, nie tylko robust.
- **Rozdzielenie od TEST:** kod Fazy 6 nie czyta plików TEST. Konfiguracja (hiperparametry, parametry dopasowania, normalizacja, progi) zapisywana jest do `frozen_config.json` z sumą SHA-256, a modele do plików `.joblib`; scoring TEST (Faza 7) korzysta wyłącznie z nich.

**Uzasadnienie:** jeden użytkownik ma około 40 normalnych okien w VAL. Próg „FPR ≤ 5%” wyznaczony na 40 oknach zależy od 2 obserwacji i jest losowy. Pula roli (około 200 okien) daje stabilny próg. Wartość 5% to robocza definicja „akceptowalnego poziomu fałszywych alarmów” z RQ1.

### D8. Kierunek i normalizacja score’u
- Każdy detektor zwraca `anomaly_score`, w którym **większa wartość oznacza większe ryzyko** (dla modeli sklearn: `−decision_function`).
- Normalizacja do [0, 1] jest liczona na **normalnych oknach VAL** danej jednostki modelowania (globalnie, per rola albo per użytkownik), wygładzoną dystrybuantą empiryczną z przedłużonym ogonem:
  - dla s ≤ s_max: `norm(s) = rank(s) / (n + 1)`,
  - dla s > s_max: `norm(s) = 1 − (1 / (n + 1)) · exp(−(s − s_max) / σ)`, gdzie σ to odporna skala (MAD · 1,4826) score’ów referencyjnych.
  - Gdy MAD score’ów referencyjnych = 0 (np. reguły: w normie prawie zawsze 0), σ = IQR / 1,349, potem odchylenie standardowe, a na końcu 1. Bez skali zastępczej ogon byłby nieskończenie stromy.
  - Dla s − s_max > ok. 33 σ wartość `norm(s)` zaokrągla się w arytmetyce float64 do 1,0. Kolejność według D8 zachowuje się wtedy, rozstrzygając remisy nadwyżką ogona `(s − s_max) / σ`, porównywalną między jednostkami.

**Uzasadnienie:**
- Zwykła ECDF z TRAIN nada wartość 1,0 każdemu oknu powyżej maksimum treningowego, czyli prawie wszystkim anomaliom. Najwyżej oceniane alerty remisowałyby wtedy ze sobą, a Precision@K/NDCG@K (H5) straciłyby sens. Przedłużony ogon zachowuje kolejność.
- Score’y na TRAIN są zaniżone, bo model widział te dane. VAL jest niezależny.
- Transformacja jest monotoniczna, więc nie zmienia Precision, Recall, PR-AUC ani MCC pojedynczego modelu. Służy tylko porównywalności skal (pula roli w D7, RiskScore w D10).

### D9. Opóźnienie wykrycia
- `delay = koniec pierwszego okna incydentu oznaczonego alarmem − czas (symulowany) pierwszego zdarzenia incydentu`.
- Raportowane jako mediana i P90 dla incydentów wykrytych. **Odsetek incydentów niewykrytych** jest raportowany osobno, a nie jako delay = ∞.

**Uwaga:** alert powstaje na koniec okna, więc opóźnienie ma naturalną podłogę do 30 minut. To właściwość jednostki analizy (D1), a nie wada modelu.

### D10. RiskScore (RQ6, H5)
```
RiskScore = 0.60 · norm_anomaly_score
          + 0.25 · severity_weight
          + 0.15 · context_weight
```
Wagi ustalone a priori i niezmieniane po obejrzeniu wyników TEST. Składniki pochodzą **wyłącznie z obserwowalnych danych okna**, nigdy z etykiety generatora:

- `norm_anomaly_score`: według D8.
- `severity_weight`: maksimum wrażliwości tabel dotkniętych w oknie, z mnożnikiem 1,0 dla operacji zapisu i 0,7 dla odczytu. Wrażliwość tabel:

  | Tabele | Wrażliwość | Powód |
  |---|---|---|
  | Customer, Address, CustomerAddress | 1,0 | dane osobowe |
  | SalesOrderHeader, SalesOrderDetail | 0,8 | dane finansowe |
  | Product, ProductModel | 0,4 | dane katalogowe |
  | ProductDescription, ProductCategory, ProductModelProductDescription | 0,2 | treści marketingowe |

- `context_weight`: średnia z trzech flag binarnych: praca po godzinach względem profilu użytkownika, urządzenie inne niż podstawowe, IP niewidziane w TRAIN dla danego użytkownika.

**Ocena rankingu:** relewancja w NDCG@K to severity incydentu z ground truth, wyznaczona według D19 (1–5; okno normalne = 0; przy kilku incydentach maksimum). Etykieta służy **tylko do oceny**, nie do liczenia RiskScore. K ∈ {10, 20, 50}. Porównanie trzech sortowań: chronologiczne, według `norm_anomaly_score` i według RiskScore.

### D11. Okna czasowe: szczegóły techniczne
- Okna wyrównane do pełnych półgodzin (hh:00–hh:30, hh:30–hh+1:00), przedział lewostronnie domknięty.
- Czas to **czas symulowany** (`SimTime` z generatora), naiwny (bez strefy czasowej), interpretowany jako czas lokalny firmy. Metryki wykonania (duration, reads, rows) są **rzeczywiste** z Extended Events.
- Zdarzenia przypisywane są do okien po joinie z ground truth po `EventUid`. Zdarzenie XE bez pary w generatorze przerywa przebieg (wymagane pokrycie 100%).
- **Źródło danych w tabeli połączonej:** z Extended Events pochodzą wszystkie fakty obserwowalne: metryki wykonania, tekst SQL (bez znacznika), login, host (`WSID`), typ urządzenia i IP (nazwa aplikacji klienta). Z generatora pochodzą wyłącznie czas symulowany i etykiety (kolumny `gt_*`), które służą tylko do oceny. Do SQL Server trafia jedynie `EventUid`; etykieta nigdy nie przechodzi przez bazę.
- **Wykonanie przebiegu (Faza 4):** przed każdym przebiegiem (split × ziarno) baza jest przywracana z oryginalnego `AdventureWorksLT2025.bak`, a potem dostaje role, 30 loginów `SIM_<ROLA>_<nr>` i rekordy startowe (D20). Dzięki temu metryki wykonania (odczyty logiczne, liczba wierszy) nie zależą od wcześniejszych przebiegów. Operacje wykonywane są w kolejności czasu symulowanego; każda sesja generatora to osobne połączenie z loginem użytkownika. Każdy przebieg ma osobny plik `.xel`.
- **Utrata zdarzeń XE:** sesja działa w trybie `ALLOW_SINGLE_EVENT_LOSS` (SQL Server nie dopuszcza `NO_EVENT_LOSS` dla zdarzenia `error_reported`), z buforem 32 MB. Utrata zdarzenia nie przechodzi niezauważona: brak pary dla zdarzenia generatora odrzuca przebieg (pokrycie 100%).

### D12. Baseline statystyczny (robust)
- Dla każdej cechy ciągłej i każdej jednostki modelowania (D17) z okien TRAIN liczone są mediana *m* i skala `s = 1,4826 · MAD`.
- **Gdy MAD = 0:** `s = IQR / 1,349`. Gdy i to jest 0: `s = max(10⁻³, 0,01 · (P99 − P1))`, a jeśli cecha jest w TRAIN całkowicie stała, `s = 1` w jednostkach cechy. Odchylenie od cechy zawsze stałej (np. `delete_ratio` = 0) pozostaje wtedy sygnałem, ale nie eksploduje.
- `z_j = |x_j − m_j| / s_j`. **Score okna = średnia z 3 największych z_j.**
- Model per użytkownik z mniej niż 30 oknami w TRAIN używa statystyk roli.
- **Cechy ciągłe** to wszystkie cechy D14 poza binarnymi (`is_weekend`, `after_hours_flag`, `new_table_flag`, `ip_known_flag`, one-hot roli). Udziały urządzeń i operacji są ciągłe. Skala poniżej 10⁻⁹ · max(1, |mediana|) traktowana jest jak 0 (szum zmiennoprzecinkowy, np. `hour_sin`), żeby nie dawała z rzędu 10¹⁵.
- **Obserwacja z VAL (ziarno 11):** dla cech prawie stałych w TRAIN (np. udziały urządzeń, `delete_ratio`, `rare_resource_score` per użytkownik) poziom `0,01 · (P99 − P1)` daje z rzędu 100, a przy P99 = P1 (skala 10⁻³) nawet 1000. Wariant łagodniejszy (`(P99 − P1) / 4,653`, skala zgodna z rozkładem normalnym) nie był na VAL jednoznacznie lepszy: PR-AUC wyniosło 0,26 wobec 0,15 dla wariantu global, ale 0,27 wobec 0,40 dla wariantu per rola. Dlatego D12 pozostaje bez zmian, a obserwacja trafia do analizy wyników.

**Uzasadnienie:** mediana i MAD są odporne na wartości skrajne (Iglewicz & Hoaglin, 1993). Średnia z top-3 jest mniej wrażliwa na przypadkowe odchylenie jednej cechy niż maksimum. W obecnej wersji kodu maksimum z MAD ≈ 0 dawało FPR 87%.

### D13. Baseline regułowy
- Progi wyznaczane z percentyli **okien TRAIN** danej jednostki modelowania, **nie z parametrów generatora**:
  1. `operation_count > P99`,
  2. `hour_rarity > P99`,
  3. `rare_resource_score > P99`,
  4. dowolny z udziałów `select/insert/update/delete_ratio` poza przedziałem [P1, P99].
- Score = liczba naruszonych reguł (0–4). Próg alarmowy dobierany jak dla innych detektorów (D7).

**Uzasadnienie:** progi skopiowane z generatora sprawiłyby, że reguły „znałyby odpowiedź”, a eksperyment byłby tautologiczny (uwaga recenzenta). Progi z danych stawiają reguły w tej samej sytuacji co modele: znają tylko normę z TRAIN.

### D14. Cechy i grupy do ablacji (RQ2, H2)
| Grupa | Cechy |
|---|---|
| **Czas** | `hour_sin`, `hour_cos`, `dow_sin`, `dow_cos`, `is_weekend`, `after_hours_flag`, `hour_rarity` |
| **Wolumen** | `operation_count`, `session_count`, `ops_per_session`, `volume_ratio_to_median` |
| **Kontekst** | `device_type` (one-hot: udział operacji okna z każdego z 4 typów urządzeń), `ip_known_flag`, `role_id` (one-hot, tylko w wariancie globalnym) |
| **Proporcje operacji** | `select_ratio`, `insert_ratio`, `update_ratio`, `delete_ratio` |
| **Zasoby** | `unique_tables`, `rare_resource_score`, `new_table_flag` (z tabeli docelowej operacji, `TargetTable`) |
| **Wykonanie** | `sum_logical_reads`, `sum_rows`, `mean_duration`, `avg_join_count` (średnia liczba słów `JOIN` w zapytaniu), `avg_sql_length` (średnia długość SQL **bez komentarza z identyfikatorem**) |

- **Tabela operacji:** cechy grupy Zasoby liczone są z tabeli docelowej (`TargetTable` z logu XE: pierwsza tabela po `FROM`/`INTO`/`UPDATE`/`DELETE FROM`). Pełna lista tabel zapytania (`Tables`) zawiera też tabele dołączane JOIN-em i tabele rodziców wyszukiwanych podzapytaniem w INSERT (np. model i opis przy powiązaniu `ProductModelProductDescription`); nie jest używana w cechach. JOIN-y w zapytaniach normy łączą wyłącznie tabele dozwolone dla roli i nie dołączają rzadkich tabel użytkownika (D15).
- Cechy „rzadkości” i „względem normy” (`hour_rarity`, `after_hours_flag`, `volume_ratio_to_median`, `rare_resource_score`, `new_table_flag`, `ip_known_flag`) liczone są **wyłącznie z TRAIN**, na poziomie jednostki modelowania (D17). Definicje (jednostka = populacja, rola albo użytkownik; `p̂` z wygładzeniem Laplace’a):
  - `hour_rarity = −log p̂(godzina startu okna)`, gdzie `p̂(h) = (liczba okien TRAIN jednostki startujących w godzinie h + 1) / (liczba okien + 24)`;
  - `after_hours_flag = 1`, gdy którakolwiek operacja okna wypada poza przedziałem [P1, P99] godzin operacji jednostki w TRAIN (te same percentyle co w D13). Godzin pracy z profilu generatora nie używa się, z tego samego powodu co w D13;
  - `volume_ratio_to_median = operation_count / mediana operation_count okien TRAIN jednostki`;
  - `rare_resource_score = Σ −log p̂(tabela)` po operacjach okna na tabelach **rzadkich**, czyli o udziale `p̂(t) = (n_t + 1) / (N + 10) < 5%` operacji jednostki w TRAIN. Suma po operacjach odróżnia pojedynczy legalny odczyt rzadkiej tabeli (D15) od serii odczytów. Przy samym maksimum oba przypadki dawałyby tę samą wartość, a reguła D13 (> P99) prawie nigdy by nie zadziałała;
  - `new_table_flag = 1`, gdy okno dotyka tabeli, której jednostka nie używała w TRAIN;
  - `ip_known_flag = 1`, gdy wszystkie adresy IP okna wystąpiły w TRAIN jednostki.
- Zastępstwo z D12 (statystyki roli dla użytkownika z < 30 oknami TRAIN) dotyczy także statystyk cech względnych w wariancie per użytkownik.
- **Konfiguracje ablacji:**
  - pojedyncze grupy: Czas, Wolumen, Kontekst (to bezpośrednio sprawdza H2),
  - pary: Czas+Wolumen, Czas+Kontekst, Wolumen+Kontekst,
  - Czas+Wolumen+Kontekst,
  - pełny zestaw,
  - pełny zestaw bez jednej grupy (leave-one-group-out, dla wszystkich 6 grup).
- Wyniki ablacji raportowane są **łącznie i per klasa anomalii**. Trójka Czas+Wolumen+Kontekst nie zawiera cech proporcji ani zasobów, więc z założenia słabo wykrywa klasy „nietypowa proporcja” i „rzadki zasób”. Wynik łączny bez rozbicia na klasy myliłby wpływ grup cech z udziałem klas w zbiorze.

### D15. Profile, użytkownicy i trudne przypadki legalne
- **Rozstrzygnięcie sprzeczności w profilu CEO:** udział SELECT wynosi **85–95%**, a INSERT (wyłącznie do `Customer`) **5–15%** operacji tygodniowo. Zgadza się to z opisem „dodawanie rekordów skrajnie rzadkie” i „aktywność to głównie raporty zarządcze”. Zapis „30–60% INSERT” w profilu był błędny. Wolumen tygodniowy: 14–48 operacji.
- **Perturbacja użytkownika w roli:** wolumen tygodniowy ×U(0,85; 1,15), środek okna godzinowego ±U(0; 1) h (granice okna z dokumentu pozostają, np. CFO nie zaczyna sesji regularnych po 17:00), wagi tabel mnożone przez U(0,7; 1,3) i renormalizowane, udział urządzeń ±5 pp.
- **Rzadko używane tabele (zasób dla klasy „rzadki zasób”):** każdy użytkownik ma 1 (role z ≤ 3 tabelami do odczytu) albo 2 takie tabele, wybierane spośród tabel, które rola **wyłącznie odczytuje** (żeby zapis nie podnosił ich częstości). Ich waga to 1–3% odczytów danego użytkownika. Rzadkość jest więc cechą użytkownika, nie roli (ważne dla H1).
- **Trudne przypadki legalne** (etykieta: norma, tag `HARD_NORMAL_*` tylko do analizy błędów). Występują we **wszystkich** zbiorach, także w TRAIN:

  | Przypadek | Role | Częstość |
  |---|---|---|
  | koniec miesiąca: skok wolumenu i zmiana proporcji | CFO | ostatnie 3–5 dni roboczych każdego miesiąca (z profilu) |
  | praca po godzinach: 17–20 (Marketing Manager, na komputerze prywatnym) oraz 17–20 lub 7–9 (Sales, Sales Manager, Marketing) | jak obok | ~3% sesji |
  | aktywność weekendowa | Sales, Sales Manager, Marketing | ~1% sesji |
  | rzadki legalny dostęp do tabeli | wszystkie | 1–3% odczytów użytkownika (rzadko używane tabele, patrz wyżej) |
  | skok SELECT bez incydentu (przygotowanie raportu) | wszystkie | 1 sesja na miesiąc, ×2–3 mediany operacji **na okno 30 min** przez 1 h (ok. 10% operacji TRAIN) |
  | koniec miesiąca CFO: po 17:00 na komputerze prywatnym | CFO | 30% sesji w ostatnich 3–5 dniach roboczych |
  | alternatywne IP/urządzenie | Sales, Sales Manager, Marketing | ~2% sesji |

- **Intensywność anomalii:** każda anomalia ma poziom zapisany w `incidents.csv`:
  - **silna** (wyraźnie poza normą, np. wzrost wolumenu ×4–6 mediany na okno, godziny 1–5),
  - **słaba** (rozkłady nakładają się na normę, np. wzrost ×1,5–2,5, godzina tuż za oknem pracy).
  Szczegóły per klasa w D16. Mediana operacji na okno użytkownika jest przybliżana oczekiwaną długością sesji (λ + 1).

  Proporcja 50/50.

**Uzasadnienie:** bez legalnych odstępstw każde nietypowe okno byłoby anomalią, a FPR nie dałoby się rzetelnie ocenić. Poziom intensywności pozwala sprawdzić H4 bez dodawania nowych klas anomalii.

### D16. Generalizacja: czym TEST różni się od VAL
| Element | VAL | TEST |
|---|---|---|
| ziarno | pod-ziarno VAL (D6) | pod-ziarno TEST (D6) |
| parametry normy | profil bazowy | profil z perturbacją ±10% (wolumen, proporcje, długość sesji) |
| nietypowa pora, silna | godziny 1–5 lub weekend | godziny 22–24 lub 5–7 lub weekend |
| nietypowa pora, słaba | 1–2 h **po** końcu typowego okna pracy użytkownika | 1–2 h **przed** początkiem typowego okna pracy użytkownika |
| wzrost wolumenu | tempo: silna ×4–6, słaba ×1,5–2,5 mediany operacji **na okno 30 min**, przez 1 h (2 okna) | to samo tempo, przez 1,5–2 h (3–4 okna) |
| nietypowa proporcja (8–16 operacji w 1 h; udział typu docelowego: silna 85–98%, słaba 50–70%) | przesunięcie w stronę **najrzadszego** w normie użytkownika typu operacji, do którego rola ma uprawnienia (brane są tylko typy o udziale < 50% w normie) | przesunięcie w stronę **innego** dozwolonego typu niż na VAL (np. SELECT, jeśli na VAL był zapis); tylko typy z uprawnień roli (CFO nie ma INSERT, Marketing nie ma DELETE, CEO ma tylko SELECT/INSERT) |
| rzadki zasób (silna: 8–18 odczytów rzadkiej tabeli w 1 h; słaba: 2–4 odczyty wplecione w zwykłą sesję, do incydentu należą tylko one) | pierwsza rzadka tabela użytkownika | druga rzadka tabela użytkownika (jeśli ma tylko jedną: ta sama) |
| liczba incydentów | według D18 | według D18, ±1 na klasę i rolę |

**Uzasadnienie:** detektor ma wykrywać *schemat* anomalii, a nie konkretne wartości z VAL, na których stroiliśmy progi.

### D17. Warianty modelowania (RQ4, H1)
| Wariant | Liczba modeli | Statystyki „rzadkości” (D14) i robust (D12) | Próg (D7) |
|---|---|---|---|
| **Global** | 1 | z całej populacji | jeden globalny |
| **Per rola** | 6 | z okien roli | per rola |
| **Per użytkownik** | 30 | z okien użytkownika | wspólny dla roli |

- Wszystkie warianty używają **tego samego zbioru cech, tych samych detektorów i tej samej procedury progowania**.
- Statystyki rzadkości liczone są na poziomie wariantu. Gdyby we wszystkich wariantach liczyć je per użytkownik, model globalny byłby „personalizowany przez cechy” i porównanie H1 straciłoby sens.

### D18. Liczba incydentów i udział anomalii
- W VAL: **3 incydenty każdej klasy na rolę na miesiąc**, przypisane losowo do użytkowników tej roli. Daje to 6 ról × 4 klasy × 3 = **72 incydenty** na przebieg, po połowie silnych i słabych (D15).
- W TEST: liczba według D16 (±1 na klasę i rolę).
- Docelowy udział okien anomalnych: **5–10%** wszystkich okien VAL i TEST. Rzeczywisty udział jest raportowany przy wynikach, bo od niego zależy bazowy poziom Precision i PR-AUC (losowy klasyfikator ma PR-AUC równe temu udziałowi).
- **Zmierzone na generatorze (25.11.2026, ziarna 11–55):** VAL 9,5–10,0%, TEST 10,6–13,0%. TEST jest powyżej celu, bo incydent obejmuje 2–4 okna, a liczba incydentów w TEST losowo rośnie (±1 na klasę i rolę). Generatora nie dostrajano pod ten cel; rzeczywisty udział jest raportowany per ziarno.

**Uzasadnienie:** bez ustalonej liczby incydentów Precision i PR-AUC nie są porównywalne między VAL, TEST i przebiegami. 3 incydenty na klasę i rolę dają około 18 incydentów każdej klasy na przebieg, a po 5 ziarnach około 90, co wystarcza do metryk per klasa.

### D19. Severity incydentu (ground truth dla NDCG@K)
Severity (1–5) **nie jest losowana**. Wyznacza się ją deterministycznie z cech incydentu, przed eksperymentem:

`severity = min(5, bazowa_klasy + [tabela o wrażliwości ≥ 0,8] + [intensywność silna] + [operacje zapisu na tabeli o wrażliwości ≥ 0,8])`

| Klasa | Wartość bazowa |
|---|---|
| nietypowa pora | 1 |
| rzadki zasób | 2 |
| nietypowa proporcja | 2 |
| wzrost wolumenu | 2 |

(Nawiasy kwadratowe oznaczają 1, gdy warunek jest spełniony, w przeciwnym razie 0. Wrażliwość tabel według D10.)

**Uzasadnienie:** obecny generator losuje severity niezależnie od przebiegu incydentu. Przy takiej etykiecie żaden scoring nie może poprawić NDCG@K, więc H5 byłaby nietestowalna. Severity odpowiada wpływowi incydentu, czyli temu, jakich danych dotyczył i jak bardzo odbiegał od normy.

**Ograniczenie:** RiskScore (D10) korzysta z tej samej tabeli wrażliwości. Część przewagi RiskScore w H5 wynika więc z tego, że oba opisują ten sam czynnik wpływu. Trzeba to uczciwie opisać w pracy. Właściwym testem H5 jest to, czy RiskScore porządkuje alerty lepiej niż **sam** anomaly score i niż chronologia, a nie jego wartość bezwzględna.

### D20. Rekordy dla operacji zapisu (decyzja z 25.11.2026)
- **INSERT** tworzy nowy rekord symulacyjny. Rekord zależny odwołuje się do rodzica wstawionego wcześniej **w tej samej sesji**, jeśli istnieje (zamówienie, potem pozycje; nowy adres, potem powiązanie z klientem; nowy opis, potem powiązanie z modelem). W przeciwnym razie odwołuje się do rekordu z puli aktualizacji.
- **UPDATE** trafia w rekord z **puli aktualizacji** danej tabeli (do 20 rekordów startowych na tabelę i przebieg, nigdy nieusuwanych).
- **DELETE** trafia w **świeży rekord startowy bez rekordów zależnych**, użyty tylko raz. Wyjątek z profilu Marketing Managera: usunięcie szkicu wstawionego wcześniej w tej samej sesji.
- **Rekordy startowe** (`seed_records_<split>.csv`) wstawia orkiestrator jako administrator przed przebiegiem, po przywróceniu bazy z `.bak`. Nie niosą znacznika gt, więc nie trafiają do danych eksperymentu.
- **Adresowanie rekordu:** klucz generatora (`record_key`) zamieniany jest na deterministyczny `rowguid` (UUID v5); UPDATE i DELETE mają postać `WHERE rowguid = '…'`, a INSERT rekordu zależnego wyszukuje rodzica podzapytaniem po `rowguid`. Każdy zapis musi zmienić dokładnie jeden wiersz, inaczej przebieg jest odrzucany.

**Uzasadnienie:** wolumen DELETE (np. CFO około 90 tygodniowo na 5 użytkowników) wielokrotnie przekracza liczbę rekordów, które tworzą inne role, i liczbę zamówień w AdventureWorksLT (32). Pula startowa daje prawdziwe `row_count` i `writes` dla operacji zapisu i zero błędów kluczy obcych (D3).

**Konsekwencje dla incydentów (D3):** incydenty używają wyłącznie urządzeń i kont danego użytkownika. Obce urządzenie z zewnętrznym IP (dawniej „ROGUE-EXT-PC” w starym generatorze) zostało usunięte, bo to naruszenie bezpieczeństwa spoza 4 klas behawioralnych. Dzień incydentu wypada w dniu, w którym użyte urządzenie jest zwykle używane, żeby incydent był anomalią tylko w swojej klasie (np. wzrost wolumenu CEO nie wypada w poniedziałek w biurze).

### D21. Protokół ewaluacji na TEST (decyzja z 27.11.2026, przed pierwszym uruchomieniem TEST)
- **Źródło score’ów:** TEST oceniany jest wyłącznie modelami `.joblib` i plikiem `frozen_config.json` z Fazy 6, po sprawdzeniu sumy SHA-256. Nic nie jest dopasowywane na TEST.
- **Metryki per okno** (dla każdego ziarna, konfiguracji cech, wariantu D17 i detektora): Precision, Recall, FPR, MCC przy głównym progu D7 oraz PR-AUC (average precision). PR-AUC liczone jest według kolejności D8 (`norm`, remisy według nadwyżki ogona). Przy progu recall = 0,80 raportowane są FPR i recall (H1).
- **Podzbiory (D2):**
  - per klasa: pozytywy to okna z incydentem klasy *c*, negatywy to wszystkie okna normalne, a okna innych klas są pomijane;
  - per intensywność i per klasa × intensywność: tak samo, z incydentami danej intensywności (`incidents.csv`).
- **Metryki per incydent (D9):** incydent jest wykryty, jeśli alarm przy głównym progu ma ≥ 1 okno zawierające jego zdarzenie. `delay = koniec pierwszego takiego okna − czas pierwszego zdarzenia incydentu`. Raportowane: recall incydentów, mediana i P90 opóźnienia wykrytych (w minutach), odsetek niewykrytych; łącznie i per klasa.
- **RiskScore (D10), składniki z obserwowalnych danych okna:**
  - `severity_weight`: maksimum po operacjach okna z wrażliwości tabeli docelowej × 1,0 (INSERT/UPDATE/DELETE) albo × 0,7 (SELECT);
  - `context_weight`: średnia trzech flag: `after_hours_flag` z wariantu per użytkownik; urządzenie inne niż podstawowe, gdzie podstawowe to typ urządzenia z największą liczbą operacji użytkownika w TRAIN, a flaga = 1, gdy okno ma operację z innego urządzenia; `1 − ip_known_flag` z wariantu per użytkownik.
  - Te same składniki są używane we wszystkich wariantach i detektorach; zmienia się tylko `norm_anomaly_score`.
- **Ranking (H5):** sortowana jest **kolejka alertów**, czyli okna TEST z alarmem przy głównym progu, jedna kolejka na ziarno, wariant i detektor. Trzy sortowania:
  - chronologiczne: `WindowStart`, potem `UserId`;
  - według score’u: kolejność D8;
  - według RiskScore: malejąco, remisy według kolejności D8;
  - pozostałe remisy (np. reguły z dyskretnym score’em) rozstrzygane są chronologicznie, tak jak analityk przegląda alerty o równym priorytecie.
- **Miary rankingu:** `P@K` = liczba okien anomalnych w pierwszych K / K; przy mniej niż K alertach brakujące pozycje liczone są jako nietrafione. `NDCG@K` z zyskiem liniowym `rel / log2(i + 1)`, gdzie rel = severity D19. IDCG liczone jest z tej samej kolejki alertów; jeśli kolejka nie zawiera okna anomalnego, NDCG jest nieokreślone i nie wchodzi do średniej (liczba takich przypadków jest raportowana).
- **Analiza błędów (RQ1):** odsetek FP przy głównym progu, które są oknami `HARD_NORMAL_*` (łącznie i per tag), na tle udziału takich okien wśród wszystkich okien normalnych. Raportowany jest też FPR osobno na oknach trudnych i zwykłych.
- **Ablacja (H2):** 14 konfiguracji z D14 dla wszystkich 5 detektorów i 3 wariantów. Każda konfiguracja ma własne strojenie hiperparametrów, normalizację i progi na VAL (procedura Fazy 6). Reguły korzystają tylko z tych swoich reguł, których cechy są w konfiguracji; bez żadnej reguły score jest stały (brak alarmów).
- **Kryteria hipotez** (różnice sparowane po 5 ziarnach, 95% CI t-Studenta, D6; „istotnie lepszy” = CI różnicy całkowicie powyżej 0, „porównywalny” = CI zawiera 0). Oceniane osobno dla każdego wariantu i detektora:
  - **RQ1:** średni FPR ≤ 5% i średni recall ≥ 0,50 przy głównym progu;
  - **H1:** dla każdego detektora per rola vs global i per użytkownik vs global, przy progu recall = 0,80: FPR istotnie niższy, a |średnia różnica recall na TEST| ≤ 5 pp;
  - **H2:** konfiguracja Czas+Wolumen+Kontekst istotnie lepsza w PR-AUC od każdej z grup Czas, Wolumen, Kontekst osobno (MCC raportowane pomocniczo); także per klasa;
  - **H3′:** co najmniej jeden z IF, OCSVM, LOF istotnie lepszy od reguł w PR-AUC lub MCC (konfiguracja pełna);
  - **H4′:** dla każdej metody *m* ∈ {robust, IF, OCSVM, LOF}: na anomaliach silnych różnica MCC (*m* − reguły) porównywalna, a na słabych PR-AUC *m* istotnie wyższe niż reguł;
  - **H5:** RiskScore istotnie lepszy od chronologii i od samego score’u w NDCG@K i P@K, osobno dla K = 10, 20, 50.
- **Wiele porównań:** nie stosuje się poprawki na wielokrotne porównania. Liczba porównań jest raportowana, a wnioski opierają się na spójności wyników między wariantami i detektorami (ograniczenie, sekcja E).

---

## C. Hipotezy po zmianie zakresu (D3)

Pierwotne H3 i H4 odwołują się do naruszeń polityki, które wyłączono z zakresu. Nowe brzmienie:

- **H3′.** Dla anomalii behawioralnych co najmniej jedna z metod ML (Isolation Forest, One-Class SVM, LOF) osiągnie na zbiorze testowym wyższą PR-AUC lub MCC niż baseline regułowy przy FPR ≤ 5%.
- **H4′.** Dla anomalii o **silnej** intensywności baseline regułowy osiągnie skuteczność porównywalną z metodami statystycznymi i ML (różnica MCC w granicach 95% CI), przy wyższej interpretowalności. Dla anomalii o **słabej** intensywności, nakładających się na normę, metody statystyczne i ML osiągną istotnie wyższą PR-AUC niż reguły.

RQ5 („dla których klas anomalii ML daje przewagę”) odpowiada się wynikami per klasa × intensywność.

H1, H2 i H5 pozostają bez zmian.

---

## D. Mapowanie decyzji na pytania i hipotezy

| RQ / H | Decyzje |
|---|---|
| Wszystkie (protokół TEST) | D21 |
| RQ1 | D1, D7 (kryterium RQ1: FPR ≤ 5% i recall ≥ 0,50 na TEST), D15 (trudne przypadki) |
| RQ2 / H2 | D14 (łącznie i per klasa) |
| RQ3 / H3′ | D7, D12, D13 |
| RQ4 / H1 | D4, D17, D7 (drugi punkt pracy: recall = 0,80) |
| RQ5 / H4′ | D2 (metryki per klasa), D15 (intensywność), D18 |
| RQ6 / H5 | D8, D10, D19 |
| Generalizacja | D16 |
| Wiarygodność | D6 |

---

## E. Ograniczenia przyjęte świadomie

- Dane są syntetyczne. Wyniki nie dowodzą skuteczności w środowisku produkcyjnym.
- Czas zdarzeń jest symulowany. Zapytania wykonywane są w skompresowanym czasie, więc obciążenie serwera nie odpowiada rzeczywistemu rozkładowi dobowemu.
- Host i IP są deklarowane przez klienta (`WSID`, znacznik symulacji), a nie weryfikowane sieciowo.
- n = 5 powtórzeń daje szerokie przedziały ufności.
- Porównań jest wiele (warianty × detektory × hipotezy), a nie stosuje się poprawki na wielokrotne testowanie (D21). Pojedynczy „istotny” wynik należy czytać razem z kierunkiem pozostałych.
- Naruszenia uprawnień są poza zakresem (D3).
- Treść zapytań pochodzi z szablonów (kilka do kilkunastu na tabelę i operację). Baza AdventureWorksLT jest mała, więc metryki wykonania (odczyty, czas) mają niewielką zmienność; grupa cech Wykonanie może przez to wnosić mało informacji.

---

## Rejestr zmian

| Data | Decyzja | Zmiana | Powód |
|---|---|---|---|
| 25.11.2026 | – | wersja początkowa | – |
| 25.11.2026 | D11, D14, D20 | Faza 4: restore z `.bak` przed przebiegiem, `TargetTable` jako źródło cech Zasoby, adresowanie po `rowguid`, tryb utraty zdarzeń XE | doprecyzowanie przed pierwszym uruchomieniem na TEST (wynika z implementacji, nie z wyników) |
| 27.11.2026 | D14 | Faza 5: `avg_query_complexity` rozbite na `avg_join_count` i `avg_sql_length` (grupa Wykonanie ma 5 cech); spisane definicje cech względnych (`hour_rarity`, `after_hours_flag` z [P1, P99] godzin TRAIN, `rare_resource_score` jako suma po operacjach na tabelach o udziale < 5%, `volume_ratio_to_median`, `new_table_flag`, `ip_known_flag`); `device_type` jako udziały 4 typów urządzeń | D14 nie definiował tych cech jednoznacznie, a `avg_query_complexity` łączyło dwie wielkości w różnych jednostkach. Ustalone przed uczeniem jakiegokolwiek detektora, bez oglądania wyników |
| 27.11.2026 | D7, D8, D12 | Faza 6: kryterium wyboru hiperparametrów (PR-AUC na VAL) i siatka; zasady remisu i progu „brak alarmów”; zastępstwo D12 dla wszystkich detektorów; σ zastępcze w D8 i rozstrzyganie remisów na 1,0; definicja cech ciągłych i tolerancja numeryczna w D12 | D7, D8 i D12 nie określały tych przypadków. Uzupełnione przed jakimkolwiek scoringiem TEST. Kaskada D12 bez zmian mimo obserwacji z VAL (patrz D12) |
| 27.11.2026 | D21 (nowa), D10 | Protokół ewaluacji TEST: metryki per okno, podzbiór, incydent, ranking kolejki alertów (P@K, NDCG@K z zyskiem liniowym), składniki RiskScore (urządzenie podstawowe = najczęstsze w TRAIN), zakres ablacji, dokładne kryteria hipotez | spisane przed pierwszym scoringiem TEST |
