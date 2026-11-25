"""
Wstrzykiwanie incydentów (anomalii behawioralnych) do zbiorów VAL i TEST.

- 4 klasy (dokument „Poprawiony schemat użycia”): nietypowa pora, wzrost wolumenu,
  nietypowa proporcja operacji, dostęp do rzadko używanego zasobu,
- liczba: 3 incydenty każdej klasy na rolę na miesiąc w VAL, w TEST 3 ± 1 (METODOLOGIA D18),
- intensywność silna / słaba, po połowie (D15), warianty VAL i TEST według D16,
- severity wyznaczana deterministycznie ze wzoru D19.

Wszystkie operacje incydentów mieszczą się w uprawnieniach roli (D3): anomalia to nietypowe
użycie legalnych uprawnień.
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np

from anomaly.domain import INSERT, ROLES, SELECT, TABLE_SENSITIVITY, UPDATE, DELETE
from anomaly.generator.normal_activity import ops_from_shares
from anomaly.generator.sampling import (add_months, days_between, event, last_working_days, make_uuid,
                                        sample_op_shares, session_timestamps, spread_timestamps,
                                        truncated_normal, weighted_choice, working_days)
from anomaly.generator.users import SimUser

UNUSUAL_TIME, VOLUME_SPIKE, UNUSUAL_RATIO, RARE_RESOURCE = (
    "unusual_time", "volume_spike", "unusual_ratio", "rare_resource_access")
CLASSES = (UNUSUAL_TIME, VOLUME_SPIKE, UNUSUAL_RATIO, RARE_RESOURCE)
CLASS_CODE = {UNUSUAL_TIME: "TIME", VOLUME_SPIKE: "VOLUME", UNUSUAL_RATIO: "RATIO", RARE_RESOURCE: "RARE"}
STRONG, WEAK = "strong", "weak"

INCIDENTS_PER_CLASS_ROLE = 3            # D18 (VAL); TEST: ±1

# D19: wartości bazowe severity
SEVERITY_BASE = {UNUSUAL_TIME: 1, RARE_RESOURCE: 2, UNUSUAL_RATIO: 2, VOLUME_SPIKE: 2}
SENSITIVE_THRESHOLD = 0.8

# D16: intensywność
SPIKE_MULTIPLIER = {STRONG: (4.0, 6.0), WEAK: (1.5, 2.5)}     # × mediana operacji na okno 30 min
SPIKE_WINDOWS = {"validation": (2, 2), "test": (3, 4)}         # 1 h w VAL, 1,5–2 h w TEST
RATIO_TARGET_SHARE = {STRONG: (0.85, 0.98), WEAK: (0.50, 0.70)}
RATIO_OPS = (8, 16)
RARE_OPS = {STRONG: (8, 18), WEAK: (2, 4)}
TIME_OPS = (3, 10)


def severity(cls: str, intensity: str, ops: Sequence[Tuple[str, str]]) -> int:
    """METODOLOGIA D19."""
    sens = [TABLE_SENSITIVITY[t] for _, t in ops]
    sensitive_write = any(op != SELECT and TABLE_SENSITIVITY[t] >= SENSITIVE_THRESHOLD for op, t in ops)
    score = (SEVERITY_BASE[cls] + int(max(sens) >= SENSITIVE_THRESHOLD)
             + int(intensity == STRONG) + int(sensitive_write))
    return min(5, score)


def incident_days(user: SimUser, month_start: date, device=None) -> List[date]:
    """Dni robocze miesiąca, w których dane urządzenie (domyślnie główne) jest zwykle używane,
    tak aby incydent był anomalią tylko w swojej klasie (np. wzrost wolumenu CEO nie wypada w poniedziałek
    w biurze). U ról z trybem końca miesiąca (CFO) bez ostatnich 5 dni roboczych, żeby incydent
    nie nakładał się na legalny skok aktywności."""
    device = device or user.primary_device
    days = [d for d in working_days(days_between(month_start, add_months(month_start, 1)))
            if d.weekday() in device.days]
    if user.profile.month_end:
        excluded = set(last_working_days(month_start, 5))
        days = [d for d in days if d not in excluded]
    return days


def _pick(rng, seq):
    return seq[int(rng.integers(len(seq)))]


def _office_hour(rng, user: SimUser, duration_h: float = 0.0) -> float:
    spec = user.primary_device
    return truncated_normal(rng, spec.hour_mean, spec.hour_std, spec.hour_low,
                            max(spec.hour_low, spec.hour_high - duration_h))


def _emit(user, device, ops, timestamps, incident_id, cls, intensity, labelled=None) -> List[dict]:
    """Zdarzenia incydentu. labelled: maska, które operacje należą do incydentu (domyślnie wszystkie)."""
    labelled = labelled or [True] * len(ops)
    sev = severity(cls, intensity, [o for o, l in zip(ops, labelled) if l])
    session_id = f"{incident_id}-S"
    out = []
    for ts, (op, table), lab in zip(timestamps, ops, labelled):
        ev = event(ts, session_id, user, device, op, table,
                   incident_id=incident_id if lab else "", anomaly_class=cls if lab else "None",
                   intensity=intensity if lab else "")
        ev["severity_level"] = sev if lab else None
        out.append(ev)
    return out


# --- klasy ------------------------------------------------------------------------------

def _unusual_time(rng, user: SimUser, intensity: str, variant: str, month_start: date, incident_id: str):
    spec = _pick(rng, [d for d in user.devices if d.weight > 0])
    start_w, end_w = user.typical_window
    month_days = days_between(month_start, add_months(month_start, 1))
    if intensity == STRONG:
        if rng.random() < 0.5:
            day, hour = _pick(rng, [d for d in month_days if d.weekday() >= 5]), rng.uniform(9, 18)
        else:
            day = _pick(rng, incident_days(user, month_start, spec))
            if variant == "validation":
                hour = rng.uniform(1, 5)
            else:
                hour = rng.uniform(22, 23.9) if rng.random() < 0.5 else rng.uniform(5, 7)
    else:
        day = _pick(rng, incident_days(user, month_start, spec))
        hour = end_w + rng.uniform(1, 2) if variant == "validation" else start_w - rng.uniform(1, 2)
        hour = float(np.clip(hour, 0.0, 23.9))
    n = int(rng.integers(TIME_OPS[0], TIME_OPS[1] + 1))
    ops = ops_from_shares(rng, user, sample_op_shares(rng, user.profile.normal.mix), n)
    ts = session_timestamps(rng, day, hour, n, user.profile.intra_session_gap_s)
    return _emit(user, spec.name, ops, ts, incident_id, UNUSUAL_TIME, intensity)


def _volume_spike(rng, user: SimUser, intensity: str, variant: str, month_start: date, incident_id: str):
    lo, hi = SPIKE_WINDOWS[variant]
    windows = int(rng.integers(lo, hi + 1))
    duration = windows * 0.5
    n = max(2, int(round(rng.uniform(*SPIKE_MULTIPLIER[intensity]) * user.median_ops_per_window * windows)))
    day = _pick(rng, incident_days(user, month_start))
    ops = ops_from_shares(rng, user, sample_op_shares(rng, user.profile.normal.mix), n)
    ts = spread_timestamps(rng, day, _office_hour(rng, user, duration), duration, n)
    return _emit(user, user.primary_device.name, ops, ts, incident_id, VOLUME_SPIKE, intensity)


def ratio_target(user: SimUser, variant: str) -> str:
    """D16: VAL – najrzadszy w normie użytkownika dozwolony typ operacji, TEST – inny (drugi najrzadszy).
    Brane są tylko typy o udziale < 50% w normie (przesunięcie w stronę typu dominującego nie jest anomalią);
    gdy taki typ jest jeden (CEO: tylko INSERT), TEST używa tego samego."""
    shares = user.expected_op_shares()
    eligible = sorted((s, op) for op, s in shares.items() if s < 0.5)
    ops = [op for _, op in eligible]
    return ops[0] if variant == "validation" or len(ops) == 1 else ops[1]


def _unusual_ratio(rng, user: SimUser, intensity: str, variant: str, month_start: date, incident_id: str):
    target = ratio_target(user, variant)
    n = int(rng.integers(RATIO_OPS[0], RATIO_OPS[1] + 1))
    n_target = max(1, int(round(n * rng.uniform(*RATIO_TARGET_SHARE[intensity]))))
    rest_shares = {op: s for op, s in user.expected_op_shares().items() if op != target and s > 0}
    total = sum(rest_shares.values())
    rest = ops_from_shares(rng, user, {op: s / total for op, s in rest_shares.items()}, n - n_target) if total else []
    weights = user.select_weights if target == SELECT else user.write_weights[target]
    ops = [(target, weighted_choice(rng, weights)) for _ in range(n_target)] + rest
    ops = [ops[i] for i in rng.permutation(len(ops))]
    day = _pick(rng, incident_days(user, month_start))
    ts = spread_timestamps(rng, day, _office_hour(rng, user, 1.0), 1.0, len(ops))
    return _emit(user, user.primary_device.name, ops, ts, incident_id, UNUSUAL_RATIO, intensity)


def _rare_resource(rng, user: SimUser, intensity: str, variant: str, month_start: date, incident_id: str):
    rare = user.rare_tables
    table = rare[0] if variant == "validation" or len(rare) == 1 else rare[1]
    n_rare = int(rng.integers(RARE_OPS[intensity][0], RARE_OPS[intensity][1] + 1))
    day = _pick(rng, incident_days(user, month_start))
    if intensity == STRONG:
        ops = [(SELECT, table)] * n_rare
        ts = spread_timestamps(rng, day, _office_hour(rng, user, 1.0), 1.0, n_rare)
        return _emit(user, user.primary_device.name, ops, ts, incident_id, RARE_RESOURCE, intensity)
    # Słaba: kilka odczytów rzadkiej tabeli wplecionych w zwykłą sesję; do incydentu należą tylko te odczyty.
    normal = ops_from_shares(rng, user, sample_op_shares(rng, user.profile.normal.mix), int(rng.integers(3, 7)))
    ops = normal + [(SELECT, table)] * n_rare
    order = rng.permutation(len(ops))
    ops = [ops[i] for i in order]
    labelled = [i >= len(normal) for i in order]
    ts = session_timestamps(rng, day, _office_hour(rng, user), len(ops), user.profile.intra_session_gap_s)
    return _emit(user, user.primary_device.name, ops, ts, incident_id, RARE_RESOURCE, intensity, labelled)


BUILDERS: Dict[str, Callable] = {
    UNUSUAL_TIME: _unusual_time,
    VOLUME_SPIKE: _volume_spike,
    UNUSUAL_RATIO: _unusual_ratio,
    RARE_RESOURCE: _rare_resource,
}


def inject_incidents(rng: np.random.Generator, users: Sequence[SimUser], month_start: date,
                     variant: str, run_id: str, counter_start: int = 0) -> List[dict]:
    """Incydenty jednego miesiąca. variant: 'validation' albo 'test'."""
    events: List[dict] = []
    counter = counter_start
    for role in ROLES:
        role_users = [u for u in users if u.role == role]
        for cls in CLASSES:
            n = INCIDENTS_PER_CLASS_ROLE
            if variant == "test":
                n += int(rng.integers(-1, 2))
            intensities = [STRONG] * (n // 2) + [WEAK] * (n // 2)
            if n % 2:
                intensities.append(STRONG if rng.random() < 0.5 else WEAK)
            intensities = [intensities[i] for i in rng.permutation(len(intensities))]
            for intensity in intensities:
                counter += 1
                user = _pick(rng, role_users)
                incident_id = f"{run_id}-{CLASS_CODE[cls]}-{counter:03d}"
                events += BUILDERS[cls](rng, user, intensity, variant, month_start, incident_id)
    return events
