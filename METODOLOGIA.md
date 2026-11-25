# Metodologia eksperymentu: decyzje zamrożone

**Status:** zamrożone 25.11.2026. Przed uruchomieniem eksperymentów na zbiorze testowym.
**Zasada:** po pierwszym uruchomieniu na TEST żadnej decyzji nie zmienia się bez wpisu w sekcji „Rejestr zmian” na końcu dokumentu (data, co zmieniono, dlaczego). Chroni to przed dopasowywaniem metody do wyników testowych.

Oznaczenia: TRAIN, VAL, TEST to zbiory treningowy, walidacyjny i testowy. „Okno” to próbka analityczna zdefiniowana w D1.

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

**Uzasadnienie:** jeden użytkownik ma około 40 normalnych okien w VAL. Próg „FPR ≤ 5%” wyznaczony na 40 oknach zależy od 2 obserwacji i jest losowy. Pula roli (około 200 okien) daje stabilny próg. Wartość 5% to robocza definicja „akceptowalnego poziomu fałszywych alarmów” z RQ1.

### D8. Kierunek i normalizacja score’u
- Każdy detektor zwraca `anomaly_score`, w którym **większa wartość oznacza większe ryzyko** (dla modeli sklearn: `−decision_function`).
- Normalizacja do [0, 1] jest liczona na **normalnych oknach VAL** danej jednostki modelowania (globalnie, per rola albo per użytkownik), wygładzoną dystrybuantą empiryczną z przedłużonym ogonem:
  - dla s ≤ s_max: `norm(s) = rank(s) / (n + 1)`,
  - dla s > s_max: `norm(s) = 1 − (1 / (n + 1)) · exp(−(s − s_max) / σ)`, gdzie σ to odporna skala (MAD · 1,4826) score’ów referencyjnych.

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

### D12. Baseline statystyczny (robust)
- Dla każdej cechy ciągłej i każdej jednostki modelowania (D17) z okien TRAIN liczone są mediana *m* i skala `s = 1,4826 · MAD`.
- **Gdy MAD = 0:** `s = IQR / 1,349`. Gdy i to jest 0: `s = max(10⁻³, 0,01 · (P99 − P1))`, a jeśli cecha jest w TRAIN całkowicie stała, `s = 1` w jednostkach cechy. Odchylenie od cechy zawsze stałej (np. `delete_ratio` = 0) pozostaje wtedy sygnałem, ale nie eksploduje.
- `z_j = |x_j − m_j| / s_j`. **Score okna = średnia z 3 największych z_j.**
- Model per użytkownik z mniej niż 30 oknami w TRAIN używa statystyk roli.

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
| **Kontekst** | `device_type` (one-hot), `ip_known_flag`, `role_id` (tylko w wariancie globalnym) |
| **Proporcje operacji** | `select_ratio`, `insert_ratio`, `update_ratio`, `delete_ratio` |
| **Zasoby** | `unique_tables`, `rare_resource_score`, `new_table_flag` |
| **Wykonanie** | `sum_logical_reads`, `sum_rows`, `mean_duration`, `avg_query_complexity` (liczba JOIN-ów, długość SQL **bez komentarza z identyfikatorem**) |

- Cechy „rzadkości” i „względem normy” (`hour_rarity`, `after_hours_flag`, `volume_ratio_to_median`, `rare_resource_score`, `new_table_flag`, `ip_known_flag`) liczone są **wyłącznie z TRAIN**, na poziomie jednostki modelowania (D17).
- **Konfiguracje ablacji:**
  - pojedyncze grupy: Czas, Wolumen, Kontekst (to bezpośrednio sprawdza H2),
  - pary: Czas+Wolumen, Czas+Kontekst, Wolumen+Kontekst,
  - Czas+Wolumen+Kontekst,
  - pełny zestaw,
  - pełny zestaw bez jednej grupy (leave-one-group-out, dla wszystkich 6 grup).
- Wyniki ablacji raportowane są **łącznie i per klasa anomalii**. Trójka Czas+Wolumen+Kontekst nie zawiera cech proporcji ani zasobów, więc z założenia słabo wykrywa klasy „nietypowa proporcja” i „rzadki zasób”. Wynik łączny bez rozbicia na klasy myliłby wpływ grup cech z udziałem klas w zbiorze.

### D15. Profile, użytkownicy i trudne przypadki legalne
- **Rozstrzygnięcie sprzeczności w profilu CEO:** udział SELECT wynosi **85–95%**, a INSERT (wyłącznie do `Customer`) **5–15%** operacji tygodniowo. Zgadza się to z opisem „dodawanie rekordów skrajnie rzadkie” i „aktywność to głównie raporty zarządcze”. Zapis „30–60% INSERT” w profilu był błędny. Wolumen tygodniowy: 14–48 operacji.
- **Perturbacja użytkownika w roli:** wolumen tygodniowy ×U(0,85; 1,15), środek okna godzinowego ±U(0; 1) h, wagi tabel mnożone przez U(0,7; 1,3) i renormalizowane, udział urządzeń ±5 pp.
- **Trudne przypadki legalne** (etykieta: norma, tag `HARD_NORMAL_*` tylko do analizy błędów). Występują we **wszystkich** zbiorach, także w TRAIN:

  | Przypadek | Role | Częstość |
  |---|---|---|
  | koniec miesiąca: skok wolumenu i zmiana proporcji | CFO | ostatnie 3–5 dni roboczych każdego miesiąca (z profilu) |
  | praca po godzinach (17–20) | Sales, Sales Manager, Marketing, Marketing Manager | ~3% sesji |
  | aktywność weekendowa | Sales, Sales Manager, Marketing | ~1% sesji |
  | rzadki legalny dostęp do tabeli | wszystkie | ~2% operacji (tabela o wadze < 3%) |
  | skok SELECT bez incydentu (przygotowanie raportu) | wszystkie | ~1 sesja na miesiąc, ×2–3 mediany wolumenu |
  | alternatywne IP/urządzenie | Sales, Sales Manager, Marketing | ~2% sesji |

- **Intensywność anomalii:** każda anomalia ma poziom zapisany w `incidents.csv`:
  - **silna** (wyraźnie poza normą, np. spike ×4–6 mediany, godziny 1–5),
  - **słaba** (rozkłady nakładają się na normę, np. spike ×1,5–2,5, godzina tuż za oknem pracy).

  Proporcja 50/50.

**Uzasadnienie:** bez legalnych odstępstw każde nietypowe okno byłoby anomalią, a FPR nie dałoby się rzetelnie ocenić. Poziom intensywności pozwala sprawdzić H4 bez dodawania nowych klas anomalii.

### D16. Generalizacja: czym TEST różni się od VAL
| Element | VAL | TEST |
|---|---|---|
| ziarno | pod-ziarno VAL (D6) | pod-ziarno TEST (D6) |
| parametry normy | profil bazowy | profil z perturbacją ±10% (wolumen, proporcje, długość sesji) |
| nietypowa pora, silna | godziny 1–5 lub weekend | godziny 22–24 lub 5–7 lub weekend |
| nietypowa pora, słaba | 1–2 h **po** końcu typowego okna pracy użytkownika | 1–2 h **przed** początkiem typowego okna pracy użytkownika |
| wzrost wolumenu | silna ×4–6, słaba ×1,5–2,5 mediany, w ciągu 1 h | te same zakresy intensywności, rozłożone na 2–3 h |
| nietypowa proporcja | przesunięcie w stronę **najrzadszego** w normie użytkownika typu operacji, do którego rola ma uprawnienia | przesunięcie w stronę **innego** dozwolonego typu niż na VAL (np. SELECT, jeśli na VAL był zapis); tylko typy z uprawnień roli (CFO nie ma INSERT, Marketing nie ma DELETE, CEO ma tylko SELECT/INSERT) |
| rzadki zasób | losowe tabele rzadkie, zbiór A | inne tabele rzadkie, zbiór B (rozłączny z A, jeśli rola ma ich dość) |
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
- Naruszenia uprawnień są poza zakresem (D3).

---

## Rejestr zmian

| Data | Decyzja | Zmiana | Powód |
|---|---|---|---|
| 25.11.2026 | – | wersja początkowa | – |
