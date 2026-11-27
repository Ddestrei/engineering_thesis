"""
Faza 7: konfiguracje ablacji (METODOLOGIA D14, D21) i ich uczenie (tylko TRAIN/VAL, procedura Fazy 6).

Każda konfiguracja to podzbiór grup cech D14 z własnym strojeniem hiperparametrów, normalizacją
i progami na VAL; wynik trafia do results/seed_<N>/detectors/<konfiguracja>/.

Uruchomienie:  python -m anomaly.evaluation.ablation --seed 11 22 33 44 55     (ok. 25 min na ziarno)
"""
from __future__ import annotations

import argparse
import time
from typing import Dict, List, Optional

from anomaly.features.windowing import FEATURE_GROUPS
from anomaly.models.training import train_seed
from anomaly.paths import frozen_config_json

GROUPS = list(FEATURE_GROUPS)       # time, volume, context, ratios, resources, execution

ABLATION_CONFIGS: Dict[str, List[str]] = {
    "time": ["time"], "volume": ["volume"], "context": ["context"],
    "time+volume": ["time", "volume"], "time+context": ["time", "context"], "volume+context": ["volume", "context"],
    "time+volume+context": ["time", "volume", "context"],
    "full": GROUPS,
    **{f"no_{g}": [x for x in GROUPS if x != g] for g in GROUPS},
}
SINGLE_H2 = ("time", "volume", "context")
TRIPLE_H2 = "time+volume+context"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Uczenie konfiguracji ablacji D14 (bez TEST).")
    parser.add_argument("--seed", type=int, nargs="+", required=True)
    parser.add_argument("--configs", nargs="+", choices=list(ABLATION_CONFIGS),
                        default=[c for c in ABLATION_CONFIGS if c != "full"])
    parser.add_argument("--skip-existing", action="store_true", help="pomiń konfiguracje z istniejącym frozen_config.json")
    args = parser.parse_args(argv)
    for seed in args.seed:
        for config in args.configs:
            if args.skip_existing and frozen_config_json(seed, config).exists():
                continue
            t0 = time.perf_counter()
            train_seed(seed, groups=ABLATION_CONFIGS[config], config=config, verbose=False)
            print(f"ziarno {seed}  {config:22s} zamrożona ({time.perf_counter() - t0:.0f} s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
