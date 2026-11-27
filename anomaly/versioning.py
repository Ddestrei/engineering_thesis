"""Wersja kodu zapisywana przy każdym wyniku (PLAN Faza 7: każdą liczbę da się odtworzyć)."""
import hashlib
import subprocess
from pathlib import Path

from anomaly.paths import REPO_ROOT


def git_version() -> dict:
    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
                                  timeout=20).stdout.strip()
        except Exception:
            return ""
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
