"""
Normalna aktywność użytkownika (etykieta: norma), łącznie z trudnymi przypadkami legalnymi (METODOLOGIA D15).

Wolumen liczony jest per tydzień wyrównany do poniedziałku i skalowany do liczby dni roboczych
w tygodniu (naprawa błędu „pełny wolumen w niepełnym tygodniu na końcu miesiąca”).
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Sequence, Set, Tuple

import numpy as np

from anomaly.domain import DELETE, INSERT, MKT_MGR, SALES, SELECT
from anomaly.generator.profiles import Regime
from anomaly.generator.sampling import (add_months, allocate_counts, days_between, event, last_working_days, make_uuid,
                                        month_starts, sample_op_shares, sample_weekly_volume, session_timestamps,
                                        split_into_sessions, spread_timestamps, truncated_normal, weeks,
                                        weighted_choice, working_days)
from anomaly.generator.users import SimUser

# Tagi trudnych przypadków legalnych (D15). Zdarzenie może mieć kilka tagów rozdzielonych ';'.
TAG_MONTH_END = "HARD_NORMAL_MONTH_END"
TAG_AFTER_HOURS = "HARD_NORMAL_AFTER_HOURS"
TAG_WEEKEND = "HARD_NORMAL_WEEKEND"
TAG_ALT_IP = "HARD_NORMAL_ALT_IP"
TAG_RARE_TABLE = "HARD_NORMAL_RARE_TABLE"
TAG_SELECT_SPIKE = "HARD_NORMAL_SELECT_SPIKE"

MONTH_END_AFTER_HOURS = (17.0, 21.0)
SELECT_SPIKE_MULTIPLIER = (2.0, 3.0)     # D15: ×2–3 mediany operacji w oknie, przez ~1 h
SELECT_SPIKE_DURATION_H = 1.0


def ops_from_shares(rng: np.random.Generator, user: SimUser, shares: Dict[str, float], n: int
                    ) -> List[Tuple[str, str]]:
    """Lista n par (operacja, tabela) o zadanych udziałach operacji; tabele wg wag użytkownika."""
    ops: List[Tuple[str, str]] = []
    for op, count in allocate_counts(n, shares).items():
        weights = user.select_weights if op == SELECT else user.write_weights[op]
        ops += [(op, weighted_choice(rng, weights)) for _ in range(count)]
    order = rng.permutation(len(ops))
    return [ops[i] for i in order]


def apply_session_sequences(user: SimUser, ops: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Sekwencje z profili ról:
    - Marketing Manager: DELETE szkicu następuje po INSERT tej samej tabeli w tej samej sesji,
    - Sales: DELETE powiązania CustomerAddress jest częścią zmiany adresu
      (INSERT Address -> INSERT CustomerAddress -> DELETE starego CustomerAddress).
    Pozostałe role bez zmian.
    """
    if user.role == SALES:
        out: List[Tuple[str, str]] = []
        for op, table in ops:
            if op == DELETE:
                out += [(INSERT, "Address"), (INSERT, "CustomerAddress")]
            out.append((op, table))
        return out
    if user.role == MKT_MGR:
        out, open_drafts = [], {}
        for op, table in ops:
            if op == INSERT:
                open_drafts[table] = open_drafts.get(table, 0) + 1
            if op == DELETE:
                if open_drafts.get(table, 0) == 0:
                    out.append((INSERT, table))
                else:
                    open_drafts[table] -= 1
            out.append((op, table))
        return out
    return ops


def _alt_ip(rng: np.random.Generator) -> str:
    """Adres spoza sieci znanych użytkownikowi (praca zdalna / podróż), za każdym razem inny."""
    return f"{int(rng.integers(78, 95))}.{int(rng.integers(0, 256))}.{int(rng.integers(0, 256))}.{int(rng.integers(2, 255))}"


def _regular_session(rng: np.random.Generator, user: SimUser, regime: Regime, days: Sequence[date],
                     week_days: Sequence[date], ops: List[Tuple[str, str]]) -> List[dict]:
    p = user.profile
    available = [(d, [day for day in days if day.weekday() in d.days]) for d in user.devices if d.weight > 0]
    available = [(d, dd) for d, dd in available if dd]
    if not available:
        return []
    choice = weighted_choice(rng, {str(i): d.weight for i, (d, _) in enumerate(available)})
    spec, spec_days = available[int(choice)]
    day = spec_days[int(rng.integers(len(spec_days)))]
    hour = truncated_normal(rng, spec.hour_mean, spec.hour_std, spec.hour_low, spec.hour_high)
    device, ip, tags = spec.name, None, [regime.hard_normal_tag] if regime.hard_normal_tag else []

    if regime.p_after_hours and rng.random() < regime.p_after_hours:
        hour = rng.uniform(*MONTH_END_AFTER_HOURS)
        device = regime.after_hours_device or device
    elif p.p_after_hours and rng.random() < p.p_after_hours:
        window = p.after_hours_windows[int(rng.integers(len(p.after_hours_windows)))]
        hour = rng.uniform(*window)
        device = p.after_hours_device or device
        tags.append(TAG_AFTER_HOURS)
    if p.p_weekend and rng.random() < p.p_weekend:
        weekend = [d for d in week_days if d.weekday() >= 5]
        if weekend:
            day = weekend[int(rng.integers(len(weekend)))]
            tags.append(TAG_WEEKEND)
    if p.p_alt_ip and rng.random() < p.p_alt_ip:
        device, ip = p.alt_ip_device, _alt_ip(rng)
        tags.append(TAG_ALT_IP)

    ops = apply_session_sequences(user, ops)
    session_id = make_uuid(rng)
    out = []
    for ts, (op, table) in zip(session_timestamps(rng, day, hour, len(ops), p.intra_session_gap_s), ops):
        t = tags + ([TAG_RARE_TABLE] if op == SELECT and table in user.rare_tables else [])
        out.append(event(ts, session_id, user, device, op, table, ip=ip, hard_normal=";".join(t)))
    return out


def _select_spike(rng: np.random.Generator, user: SimUser, days: Sequence[date]) -> List[dict]:
    """Skok SELECT bez incydentu, np. przygotowanie raportu (D15): ~1 raz w miesiącu."""
    spec = user.primary_device
    day = days[int(rng.integers(len(days)))]
    hour = truncated_normal(rng, spec.hour_mean, spec.hour_std, spec.hour_low,
                            max(spec.hour_low, spec.hour_high - SELECT_SPIKE_DURATION_H))
    windows = SELECT_SPIKE_DURATION_H * 2
    n = max(1, int(round(rng.uniform(*SELECT_SPIKE_MULTIPLIER) * user.median_ops_per_window * windows)))
    session_id = make_uuid(rng)
    out = []
    for ts in spread_timestamps(rng, day, hour, SELECT_SPIKE_DURATION_H, n):
        table = weighted_choice(rng, user.select_weights)
        tags = [TAG_SELECT_SPIKE] + ([TAG_RARE_TABLE] if table in user.rare_tables else [])
        out.append(event(ts, session_id, user, spec.name, SELECT, table, hard_normal=";".join(tags)))
    return out


def _cleanup_sessions(rng: np.random.Generator, user: SimUser, month_start: date) -> List[dict]:
    """Sales Manager: porządkowanie duplikatów klientów 1–2 razy w miesiącu, w ostatnim tygodniu,
    usunięcie poprzedzone odczytami wyszukującymi duplikaty."""
    lo, hi = user.profile.monthly_cleanup_deletes
    if hi == 0:
        return []
    spec = user.primary_device
    last_week = last_working_days(month_start, 5)
    out = []
    for _ in range(int(rng.integers(lo, hi + 1))):
        day = last_week[int(rng.integers(len(last_week)))]
        hour = truncated_normal(rng, spec.hour_mean, spec.hour_std, spec.hour_low, spec.hour_high)
        ops = [(SELECT, "Customer")] * int(rng.integers(2, 5)) + [(DELETE, "Customer")]
        session_id = make_uuid(rng)
        for ts, (op, table) in zip(session_timestamps(rng, day, hour, len(ops), user.profile.intra_session_gap_s), ops):
            out.append(event(ts, session_id, user, spec.name, op, table))
    return out


def month_end_days(rng: np.random.Generator, user: SimUser, month_start: date) -> Set[date]:
    if not user.profile.month_end:
        return set()
    lo, hi = user.profile.month_end_days
    return set(last_working_days(month_start, int(rng.integers(lo, hi + 1))))


def generate_normal(rng: np.random.Generator, user: SimUser, start: date, end: date) -> List[dict]:
    """Normalna aktywność użytkownika w [start, end) (pełne miesiące)."""
    p = user.profile
    events: List[dict] = []
    for m_start in month_starts(start, end):
        m_end = min(add_months(m_start, 1), end)
        me_days = month_end_days(rng, user, m_start)
        for week_days in weeks(m_start, m_end):
            wd = working_days(week_days)
            regimes = [(p.normal, [d for d in wd if d not in me_days])]
            if p.month_end:
                regimes.append((p.month_end, [d for d in wd if d in me_days]))
            for regime, days in regimes:
                if not days:
                    continue
                weekly = sample_weekly_volume(rng, regime.weekly_volume) * user.volume_factor
                n = int(round(weekly * len(days) / 5))
                if n == 0:
                    continue
                ops = ops_from_shares(rng, user, sample_op_shares(rng, regime.mix), n)
                i = 0
                for size in split_into_sessions(rng, n, user.session_lambda, p.session_max_ops):
                    events += _regular_session(rng, user, regime, days, week_days, ops[i:i + size])
                    i += size
        spike_days = [d for d in working_days(days_between(m_start, m_end))
                      if d not in me_days and d.weekday() in user.primary_device.days]
        if spike_days:
            events += _select_spike(rng, user, spike_days)
        events += _cleanup_sessions(rng, user, m_start)
    return events

