"""
Orkiestrator jednego ziarna eksperymentu (PLAN Faza 4). Każde ziarno przechodzi tę samą ścieżkę:

    generator  ->  dla każdego splitu (train, validation, test):
                   restore bazy z .bak -> role i użytkownicy -> rekordy startowe -> start XE (osobny plik)
                   -> wykonanie operacji -> stop XE -> LOGGER (.xel -> JSON) -> join z ground truth

Uruchomienie (z katalogu głównego repozytorium, konto Windows z uprawnieniami sysadmin):
    python -m anomaly.run_experiment --seed 11
    python -m anomaly.run_experiment --seed 11 --splits validation        # jeden przebieg

Wymaga: hasła loginów symulacji (sim_password w config.local.json albo ANOMALY_SIM_PASSWORD),
pliku .bak bazy, katalogu plików XE (domyślnie C:\\XELogs), .NET 9 SDK (LOGGER).
Wynik: data/seed_<N>/ (generated/, xe/, joined/, run_summary.json). Kod wyjścia 0 tylko wtedy,
gdy wszystkie przebiegi wykonały się bez błędów i join ma pokrycie 100%.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from typing import List

import pandas as pd

from anomaly.execution import db_admin
from anomaly.execution.DBConfig import DBConfig
from anomaly.execution.DBWorkloadExecutor import DBWorkloadExecutor, load_events
from anomaly.execution.ExecutionConfig import ExecutionConfig
from anomaly.features.xe_join import join_split
from anomaly.generator.dataset import make_run_id
from anomaly.generator.generate import SEEDS, generate_seed
from anomaly.paths import (LOGGER_PROJECT, REPO_ROOT, SPLITS, events_csv, run_summary_json, seed_dir,
                           seed_records_csv, users_csv, xe_dir, xe_json)
from anomaly.settings import get_setting, require_sim_password

XE_FLUSH_WAIT_SEC = 5          # > MAX_DISPATCH_LATENCY sesji XE (3 s)


def _git_version() -> dict:
    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
                                  timeout=20).stdout.strip()
        except Exception:
            return ""
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def _dotnet(args: List[str], step: str) -> float:
    t0 = time.perf_counter()
    proc = subprocess.run(["dotnet", *args], cwd=REPO_ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print(proc.stdout[-3000:], proc.stderr[-3000:], sep="\n")
        raise RuntimeError(f"{step}: dotnet zakończył się kodem {proc.returncode}")
    return time.perf_counter() - t0


def run_split(seed: int, split: str, users: pd.DataFrame, delay: float) -> dict:
    run_id = make_run_id(split, seed)
    print(f"\n=== Przebieg {run_id} ===")
    out: dict = {"run_id": run_id}

    out["restore_sec"] = round(db_admin.restore_database(), 2)
    db_admin.setup_principals(users)
    seeds = pd.read_csv(seed_records_csv(split, seed), keep_default_na=False)
    out["seed_records"] = db_admin.insert_seed_records(seeds)
    print(f"Baza przywrócona ({out['restore_sec']} s), role i użytkownicy utworzeni, "
          f"rekordy startowe: {out['seed_records']['seed_records']}")

    events = load_events(events_csv(split, seed))
    file_base = f"{run_id}__{datetime.now():%Y%m%d_%H%M%S}"
    xel_pattern = db_admin.xe_start(file_base)
    out["xel_pattern"] = xel_pattern
    executor = DBWorkloadExecutor(DBConfig(), ExecutionConfig(delay_sec=delay))
    try:
        out["execution"] = executor.execute_events(events, users, name=run_id)
    finally:
        time.sleep(XE_FLUSH_WAIT_SEC)
        db_admin.xe_stop()

    json_path = xe_json(run_id, seed)
    xe_dir(seed).mkdir(parents=True, exist_ok=True)
    out["logger_sec"] = round(_dotnet(["run", "--project", str(LOGGER_PROJECT), "-c", "Release", "--no-build",
                                       "--", xel_pattern, str(json_path)], "LOGGER"), 2)
    t0 = time.perf_counter()
    report = join_split(seed, split, json_path)
    out["join_sec"] = round(time.perf_counter() - t0, 2)
    out["join_ok"] = report.ok
    out["joined_events"] = report.joined_events
    out["ok"] = bool(out["execution"]["ok"] and report.ok)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Cały łańcuch eksperymentu dla jednego ziarna.")
    parser.add_argument("--seed", type=int, required=True, help=f"ziarno (D6: {SEEDS})")
    parser.add_argument("--splits", nargs="+", choices=SPLITS, default=list(SPLITS))
    parser.add_argument("--skip-generate", action="store_true", help="użyj istniejących plików generatora")
    parser.add_argument("--delay", type=float, default=0.0, help="odstęp między zapytaniami [s]")
    args = parser.parse_args(argv)

    password = require_sim_password()
    summary = {"seed": args.seed, "started": datetime.now().isoformat(timespec="seconds"),
               "code_version": _git_version(), "database": get_setting("database"),
               "backup_file": db_admin.backup_file(), "runs": {}}
    t_all = time.perf_counter()

    if not args.skip_generate:
        t0 = time.perf_counter()
        generate_seed(args.seed, verbose=False)
        summary["generate_sec"] = round(time.perf_counter() - t0, 2)
        print(f"Generator: ziarno {args.seed} zapisane w {seed_dir(args.seed)}")
    users = pd.read_csv(users_csv(args.seed), keep_default_na=False)

    summary["logger_build_sec"] = round(_dotnet(["build", str(LOGGER_PROJECT), "-c", "Release", "--nologo"],
                                                 "kompilacja LOGGER"), 2)
    db_admin.ensure_logins(users, password)
    db_admin.ensure_xe_session()
    db_admin.xe_stop()

    ok = True
    for split in args.splits:
        try:
            result = run_split(args.seed, split, users, args.delay)
        except Exception as ex:
            result = {"run_id": make_run_id(split, args.seed), "ok": False, "exception": repr(ex)}
            print(f"[BŁĄD] {result['run_id']}: {ex!r}")
        summary["runs"][split] = result
        ok = ok and result["ok"]
        if not result["ok"]:
            break                           # przebieg odrzucony: nie wykonujemy kolejnych

    summary["total_sec"] = round(time.perf_counter() - t_all, 2)
    summary["ok"] = ok and len(summary["runs"]) == len(args.splits)
    run_summary_json(args.seed).parent.mkdir(parents=True, exist_ok=True)
    run_summary_json(args.seed).write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                                           encoding="utf-8")
    print(f"\nPodsumowanie: {run_summary_json(args.seed)}")
    for split, r in summary["runs"].items():
        ex = r.get("execution", {})
        print(f"  {r['run_id']:<18} {'OK' if r['ok'] else 'BŁĄD':<5} operacje {ex.get('executed', '-')}/"
              f"{ex.get('total', '-')}, błędy {ex.get('errors', '-')}, join {r.get('joined_events', '-')}, "
              f"{ex.get('queries_per_sec', '-')} zapytań/s")
    print("WYNIK: " + ("POPRAWNY (wszystkie przebiegi, pokrycie 100%)" if summary["ok"] else "BŁĘDY"))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
