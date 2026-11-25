"""
Generator danych dla jednego ziarna (METODOLOGIA D4–D6, D15–D19).

Uruchomienie (z katalogu głównego repozytorium):
    python -m anomaly.generator.generate --seed 11

Zapisuje w data/generated/:
    users.csv                    użytkownicy (role, loginy, urządzenia, rzadkie tabele)
    events_<split>.csv           ground truth per operacja (event_uid, record_key, etykiety gt)
    incidents_<split>.csv        ground truth per incydent (klasa, intensywność, severity)
    seed_records_<split>.csv     rekordy startowe do wstawienia przed wykonaniem (Faza 4)
    generator_report.txt         raport zgodności z profilami ról
"""
from __future__ import annotations

import argparse
from datetime import date

from anomaly.generator.dataset import DEFAULT_START, build_dataset, users_table
from anomaly.generator.report import build_report
from anomaly.paths import GENERATED_DIR, events_csv, incidents_csv, seed_records_csv, users_csv

SEEDS = (11, 22, 33, 44, 55)   # METODOLOGIA D6


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generuje dane symulacji dla jednego ziarna.")
    parser.add_argument("--seed", type=int, default=SEEDS[0], help=f"ziarno główne (D6: {SEEDS})")
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START, help="początek TRAIN (RRRR-MM-DD)")
    args = parser.parse_args(argv)
    if args.seed not in SEEDS:
        print(f"[UWAGA] Ziarno {args.seed} spoza listy D6 {SEEDS}: wyniki nie wejdą do eksperymentu.")

    users, splits = build_dataset(args.seed, args.start)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    users_table(users).to_csv(users_csv(), index=False)
    for split, data in splits.items():
        data.events.to_csv(events_csv(split), index=False)
        data.incidents.to_csv(incidents_csv(split), index=False)
        data.seeds.to_csv(seed_records_csv(split), index=False)

    report = build_report(splits)
    (GENERATED_DIR / "generator_report.txt").write_text(report, encoding="utf-8")
    print(report)
    print(f"\nZapisano pliki ziarna {args.seed} w {GENERATED_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
