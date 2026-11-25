"""
Wspólne narzędzia generatora: kalendarz, losowanie z rozkładów, budowa sesji.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import numpy as np

from anomaly.domain import DELETE, SELECT
from anomaly.generator.profiles import OpMix


# --- kalendarz ---------------------------------------------------------------------

def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


def days_between(start: date, end: date) -> List[date]:
    """Dni z przedziału [start, end)."""
    return [start + timedelta(days=i) for i in range((end - start).days)]


def month_starts(start: date, end: date) -> List[date]:
    out, d = [], date(start.year, start.month, 1)
    while d < end:
        out.append(d)
        d = add_months(d, 1)
    return out


def weeks(start: date, end: date) -> Iterator[List[date]]:
    """Tygodnie wyrównane do poniedziałku, przycięte do [start, end). Naprawia błąd starego generatora,
    w którym tydzień liczony od 1. dnia miesiąca mógł dostać sesje w weekend."""
    monday = start - timedelta(days=start.weekday())
    while monday < end:
        yield [d for d in days_between(monday, monday + timedelta(days=7)) if start <= d < end]
        monday += timedelta(days=7)


def working_days(days: Sequence[date]) -> List[date]:
    return [d for d in days if d.weekday() < 5]


def last_working_days(month_start: date, n: int) -> List[date]:
    return working_days(days_between(month_start, add_months(month_start, 1)))[-n:]


# --- losowanie -------------------------------------------------------------------------

def truncated_normal(rng: np.random.Generator, mean: float, std: float, low: float, high: float) -> float:
    if std <= 0:
        return float(min(high, max(low, mean)))
    for _ in range(1000):
        v = rng.normal(mean, std)
        if low <= v <= high:
            return float(v)
    return float(np.clip(mean, low, high))


def weighted_choice(rng: np.random.Generator, weights: Dict[str, float]) -> str:
    keys = list(weights)
    p = np.array([weights[k] for k in keys], dtype=float)
    return str(keys[int(rng.choice(len(keys), p=p / p.sum()))])


def make_uuid(rng: np.random.Generator) -> str:
    return str(uuid.UUID(int=int(rng.integers(0, 2 ** 63)) | (int(rng.integers(0, 2 ** 63)) << 64)))


def sample_weekly_volume(rng: np.random.Generator, volume_range: Tuple[float, float]) -> float:
    lo, hi = volume_range
    return truncated_normal(rng, (lo + hi) / 2, (hi - lo) / 4, lo, hi)


def sample_op_shares(rng: np.random.Generator, mix: OpMix) -> Dict[str, float]:
    lo, hi = mix.select
    mean = mix.select_mean if mix.select_mean is not None else (lo + hi) / 2
    sel = truncated_normal(rng, mean, (hi - lo) / 4, lo, hi)
    dele = rng.uniform(*mix.delete) if mix.delete else 0.0
    rest_total = sum(mix.rest.values())
    shares = {SELECT: sel}
    if dele:
        shares[DELETE] = dele
    for op, w in mix.rest.items():
        shares[op] = shares.get(op, 0.0) + (1 - sel - dele) * w / rest_total
    return shares


def allocate_counts(total: int, shares: Dict[str, float]) -> Dict[str, int]:
    """Podział liczby operacji według udziałów metodą największych reszt (suma = total)."""
    raw = {k: total * v for k, v in shares.items()}
    counts = {k: int(np.floor(v)) for k, v in raw.items()}
    remainder = total - sum(counts.values())
    for k in sorted(raw, key=lambda k: raw[k] - counts[k], reverse=True)[:remainder]:
        counts[k] += 1
    return counts


def split_into_sessions(rng: np.random.Generator, n_ops: int, lam: float, max_ops: int) -> List[int]:
    sizes, left = [], n_ops
    while left > 0:
        size = min(int(rng.poisson(lam)) + 1, max_ops, left)
        sizes.append(size)
        left -= size
    return sizes


def session_timestamps(rng: np.random.Generator, day: date, hour: float, n_ops: int,
                       mean_gap_s: float) -> List[datetime]:
    """Kolejne operacje sesji z kumulowanymi odstępami Exponential(średnia) (naprawa: dawniej gap * indeks)."""
    t = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour) + timedelta(seconds=rng.uniform(0, 5))
    out = []
    for i in range(n_ops):
        if i > 0:
            t += timedelta(seconds=float(rng.exponential(mean_gap_s)))
        out.append(t)
    return out


def spread_timestamps(rng: np.random.Generator, day: date, start_hour: float, duration_h: float,
                      n_ops: int) -> List[datetime]:
    """Operacje rozłożone jednostajnie w przedziale [start, start + duration)."""
    base = datetime.combine(day, datetime.min.time()) + timedelta(hours=start_hour)
    offsets = np.sort(rng.uniform(0, duration_h * 3600, size=n_ops))
    return [base + timedelta(seconds=float(s)) for s in offsets]


def event(ts: datetime, session_id: str, user, device: str, op: str, table: str, *,
          ip: Optional[str] = None, hostname: Optional[str] = None, hard_normal: str = "",
          incident_id: str = "", anomaly_class: str = "None", intensity: str = "") -> dict:
    meta = user.device_meta[device]
    return {
        "timestamp": ts,
        "session_id": session_id,
        "user": user.user_id,
        "role": user.role,
        "login_name": user.login_name,
        "client_hostname": hostname or meta.hostname,
        "simulated_ip": ip or meta.ip,
        "device_context": device,
        "operation_type": op,
        "target_table": table,
        "incident_id": incident_id,
        "is_anomaly": int(bool(incident_id)),
        "anomaly_class": anomaly_class,
        "intensity": intensity,
        "hard_normal": hard_normal,
    }
