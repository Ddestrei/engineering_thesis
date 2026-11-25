# Archiwum: wyniki sprzed planu naprawczego

Pliki w tym katalogu pochodzą z wersji projektu sprzed 25.11.2026 (commity do `6719f6c`). Są zachowane dla porównania, ale **nie są aktualnymi wynikami pracy** i nie należy ich cytować.

| Element | Skąd pochodzi | Dlaczego jest nieaktualny |
|---|---|---|
| `results_ml/` | `ml_anomaly_pipeline.py`, 7.11.2026 | Metryki wykonania (czas, odczyty, wiersze) były losowane w Pythonie, a nie brane z Extended Events. Próbką było pojedyncze zdarzenie zamiast okna 30 min. Wyniki pochodzą ze starszej wersji danych niż `generated_data/`. |
| `generated_data/` | `ceo_log_generator.py`, 8.11.2026 | Stary profil CEO (SELECT 40–70%, INSERT także do tabel bez uprawnień) i błąd generatora dający normalny ruch w weekend. |
| `LOGI/` | program LOGGER, 6.11.2026 | Zawiera tylko styczeń i luty (zbiór testowy nie przeszedł przez bazę). Etykiety i długość SQL zawierają przeciek etykiety z komentarza w zapytaniu. |
| `eda_plots/`, `RAPORT_EDA.md` | `eda_pipeline.py` na `LOGI/`, 6.11.2026 | Opisuje inny profil CEO niż obecny (np. host `CEO-HOME-PC`, inne wagi tabel). |
| `RAPORT_MODELOWANIE_ML.md` | na podstawie `results_ml/`, 7.11.2026 | Jak wyżej dla `results_ml/`. |
| `ceo_generator_report.md` | 29.10.2026 | Opis wcześniejszej wersji generatora. |
| `stary_ceo_user/` | `create_ceo_user.sql`, `setup_ceo_user.py`, do 25.11.2026 | Jeden login `CEO_User` zastąpiły 30 loginów `SIM_<ROLA>_<nr>` z rolami bazodanowymi (Faza 4, `anomaly/execution/db_admin.py`). Login `CEO_User` jest usuwany z serwera przez `run_experiment.py`. |

Szczegóły problemów: [PLAN_NAPRAWCZY.md](../PLAN_NAPRAWCZY.md), sekcje 0 i 4. Nowe wyniki powstaną w katalogu `results/` według [METODOLOGIA.md](../METODOLOGIA.md).
