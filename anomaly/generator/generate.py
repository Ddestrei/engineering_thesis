"""
Generator danych dla jednego ziarna (METODOLOGIA D4–D6, D15–D19).

Uruchomienie (z katalogu głównego repozytorium):
    python -m anomaly.generator.generate --seed 11

Zapisuje w data/seed_<N>/generated/:
    users.csv                    użytkownicy (role, loginy, urządzenia, rzadkie tabele)
    events_<split>.csv           ground truth per operacja (event_uid, record_key, etykiety gt)
    incidents_<split>.csv        ground truth per incydent (klasa, intensywność, severity)
    seed_records_<split>.csv     rekordy startowe wstawiane przed wykonaniem (D20)
    generator_report.txt         raport zgodności z profilami ról
"""
from __future__ import annotations

import argparse
from datetime import date

from anomaly.generator.dataset import DEFAULT_START, build_dataset, users_table
from anomaly.generator.report import build_report
from anomaly.paths import (events_csv, generated_dir, generator_report_txt, incidents_csv, seed_records_csv,
                           users_csv)

SEEDS = (11, 22, 33, 44, 55)   # METODOLOGIA D6


def generate_seed(seed: int, start: date = DEFAULT_START, verbose: bool = True) -> str:
    """Generuje i zapisuje wszystkie pliki ziarna; zwraca raport zgodności."""
    if seed not in SEEDS:
        print(f"[UWAGA] Ziarno {seed} spoza listy D6 {SEEDS}: wyniki nie wejdą do eksperymentu.")
    users, splits = build_dataset(seed, start)
    generated_dir(seed).mkdir(parents=True, exist_ok=True)
    users_table(users).to_csv(users_csv(seed), index=False)
    for split, data in splits.items():
        data.events.to_csv(events_csv(split, seed), index=False)
        data.incidents.to_csv(incidents_csv(split, seed), index=False)
        data.seeds.to_csv(seed_records_csv(split, seed), index=False)
    report = build_report(splits)
    generator_report_txt(seed).write_text(report, encoding="utf-8")
    if verbose:
        print(report)
        print(f"\nZapisano pliki ziarna {seed} w {generated_dir(seed)}")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generuje dane symulacji dla jednego ziarna.")
    parser.add_argument("--seed", type=int, default=SEEDS[0], help=f"ziarno główne (D6: {SEEDS})")
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START, help="początek TRAIN (RRRR-MM-DD)")
    args = parser.parse_args(argv)
    generate_seed(args.seed, args.start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
