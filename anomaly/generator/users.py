"""
Użytkownicy symulacji: 5 na rolę (METODOLOGIA D4), każdy z indywidualną perturbacją profilu roli (D15).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Tuple

import numpy as np

from anomaly.domain import DELETE, INSERT, ROLES, SELECT, UPDATE, permitted_operations, select_only_tables
from anomaly.generator.profiles import ROLE_PROFILES, DeviceSpec, OpMix, Regime, RoleProfile

USERS_PER_ROLE = 5

# Indywidualne odchylenia (D15).
VOLUME_FACTOR_RANGE = (0.85, 1.15)
HOUR_SHIFT_MAX = 1.0
TABLE_WEIGHT_FACTOR_RANGE = (0.7, 1.3)
DEVICE_SHARE_DELTA = 0.05
# Rzadko używane tabele (D15): waga 1–3% udziału SELECT, tylko tabele wyłącznie odczytywane.
RARE_TABLE_WEIGHT_RANGE = (0.01, 0.03)

# Zaburzenie normy w zbiorze testowym (D16): ±10% wolumenu, proporcji i długości sesji.
TEST_PERTURBATION = 0.10


@dataclass(frozen=True)
class DeviceMeta:
    hostname: str
    ip: str


@dataclass(frozen=True)
class SimUser:
    user_id: str                 # np. CEO_01
    login_name: str              # login SQL Server, np. SIM_CEO_01 (tworzony w Fazie 4)
    role: str
    profile: RoleProfile         # profil roli po indywidualnej perturbacji
    devices: Tuple[DeviceSpec, ...]
    device_meta: Dict[str, DeviceMeta]
    volume_factor: float
    session_lambda: float
    select_weights: Dict[str, float]
    write_weights: Dict[str, Dict[str, float]]
    rare_tables: Tuple[str, ...]  # rzadko używane tabele tego użytkownika (zbiór A = [0], B = [1] jeśli istnieje)

    @property
    def primary_device(self) -> DeviceSpec:
        return max(self.devices, key=lambda d: d.weight)

    @property
    def typical_window(self) -> Tuple[float, float]:
        """Typowe okno pracy (najwcześniejszy początek, najpóźniejszy koniec) urządzeń regularnych."""
        regular = [d for d in self.devices if d.weight > 0]
        return min(d.hour_low for d in regular), max(d.hour_high for d in regular)

    @property
    def median_ops_per_window(self) -> float:
        """Przybliżenie mediany operacji w oknie 30 min: oczekiwana długość sesji (Poisson(λ)+1)."""
        return self.session_lambda + 1.0

    def expected_op_shares(self) -> Dict[str, float]:
        """Oczekiwane udziały operacji w normie (tryb podstawowy), z porządkowaniem bazy u Sales Managera."""
        mix = self.profile.normal.mix
        sel = mix.select_mean if mix.select_mean is not None else sum(mix.select) / 2
        dele = sum(mix.delete) / 2 if mix.delete else 0.0
        rest_total = sum(mix.rest.values())
        shares = {SELECT: sel, INSERT: 0.0, UPDATE: 0.0, DELETE: dele}
        for op, w in mix.rest.items():
            shares[op] += (1 - sel - dele) * w / rest_total
        lo, hi = self.profile.monthly_cleanup_deletes
        if hi > 0:
            monthly_ops = sum(self.profile.normal.weekly_volume) / 2 * 4.3 * self.volume_factor
            shares[DELETE] += ((lo + hi) / 2) / monthly_ops
        return {op: s for op, s in shares.items() if op in permitted_operations(self.role)}


def _perturb_weights(weights: Dict[str, float], rng: np.random.Generator) -> Dict[str, float]:
    w = {t: v * rng.uniform(*TABLE_WEIGHT_FACTOR_RANGE) for t, v in weights.items()}
    total = sum(w.values())
    return {t: v / total for t, v in w.items()}


def _with_rare_tables(weights: Dict[str, float], role: str, rng: np.random.Generator
                      ) -> Tuple[Dict[str, float], Tuple[str, ...]]:
    candidates = sorted(select_only_tables(role) & set(weights))
    n_rare = 1 if len(weights) <= 3 else 2
    n_rare = min(n_rare, len(candidates))
    if n_rare == 0:
        return weights, ()
    rare = tuple(rng.choice(candidates, size=n_rare, replace=False).tolist())
    rare_w = {t: rng.uniform(*RARE_TABLE_WEIGHT_RANGE) for t in rare}
    others = {t: v for t, v in weights.items() if t not in rare}
    scale = (1 - sum(rare_w.values())) / sum(others.values())
    return {**{t: v * scale for t, v in others.items()}, **rare_w}, rare


def _perturb_devices(devices: Tuple[DeviceSpec, ...], rng: np.random.Generator) -> Tuple[DeviceSpec, ...]:
    """D15: przesunięcie ŚRODKA okna godzinowego o ±1 h; granice okna z dokumentu pozostają
    (np. CFO nie zaczyna sesji regularnych po 17:00 poza końcem miesiąca)."""
    shift = rng.uniform(-HOUR_SHIFT_MAX, HOUR_SHIFT_MAX)
    if len(devices) > 1:
        raw = [max(0.01, d.weight + rng.uniform(-DEVICE_SHARE_DELTA, DEVICE_SHARE_DELTA)) for d in devices]
    else:
        raw = [1.0]
    total = sum(raw)
    out = []
    for d, w in zip(devices, raw):
        mean = float(np.clip(d.hour_mean + shift, d.hour_low, d.hour_high))
        out.append(replace(d, weight=w / total, hour_mean=mean))
    return tuple(out)


def _device_meta(role: str, n: int, role_idx: int, profile: RoleProfile) -> Dict[str, DeviceMeta]:
    names = {d.name for d in profile.devices}
    names |= {profile.alt_ip_device}
    for regime in (profile.normal, profile.month_end):
        if regime and regime.after_hours_device:
            names.add(regime.after_hours_device)
    if profile.after_hours_device:
        names.add(profile.after_hours_device)
    short = {"Office_PC": "OFFICE", "Home_PC": "HOME", "Mobile_Laptop": "LAPTOP", "Private_PC": "PRIVATE"}
    ips = {
        "Office_PC": f"10.{10 + role_idx}.0.{10 + n}",          # sieć firmowa, podsieć roli
        "Home_PC": f"192.168.{role_idx * 10 + n}.10",           # sieć domowa
        "Private_PC": f"192.168.{role_idx * 10 + n}.20",
        "Mobile_Laptop": f"172.16.{role_idx}.{10 + n}",         # VPN
    }
    code = role.replace("_", "")
    return {name: DeviceMeta(f"{code}{n:02d}-{short[name]}", ips[name]) for name in sorted(names)}


def make_users(rng: np.random.Generator, users_per_role: int = USERS_PER_ROLE) -> List[SimUser]:
    users: List[SimUser] = []
    for role_idx, role in enumerate(ROLES):
        profile = ROLE_PROFILES[role]
        for n in range(1, users_per_role + 1):
            select_w, rare = _with_rare_tables(_perturb_weights(profile.select_weights, rng), role, rng)
            write_w = {op: _perturb_weights(w, rng) for op, w in profile.write_weights.items()}
            users.append(SimUser(
                user_id=f"{role}_{n:02d}",
                login_name=f"SIM_{role}_{n:02d}",
                role=role,
                profile=profile,
                devices=_perturb_devices(profile.devices, rng),
                device_meta=_device_meta(role, n, role_idx, profile),
                volume_factor=rng.uniform(*VOLUME_FACTOR_RANGE),
                session_lambda=profile.session_lambda,
                select_weights=select_w,
                write_weights=write_w,
                rare_tables=rare,
            ))
    return users


def _scale_mix(mix: OpMix, factor: float) -> OpMix:
    """Przesunięcie udziału SELECT o czynnik (zakres i średnia), z ograniczeniem do [0.02, 0.98]."""
    clip = lambda v: float(min(0.98, max(0.02, v * factor)))
    return replace(mix, select=(clip(mix.select[0]), clip(mix.select[1])),
                   select_mean=clip(mix.select_mean) if mix.select_mean is not None else None)


def perturb_for_test(user: SimUser, rng: np.random.Generator) -> SimUser:
    """Zaburzenie normy w zbiorze testowym (D16): wolumen, proporcje i długość sesji ±10%."""
    p = TEST_PERTURBATION
    mix_factor = rng.uniform(1 - p, 1 + p)
    profile = user.profile
    profile = replace(
        profile,
        normal=replace(profile.normal, mix=_scale_mix(profile.normal.mix, mix_factor)),
        month_end=(replace(profile.month_end, mix=_scale_mix(profile.month_end.mix, mix_factor))
                   if profile.month_end else None),
    )
    return replace(
        user,
        profile=profile,
        volume_factor=user.volume_factor * rng.uniform(1 - p, 1 + p),
        session_lambda=user.session_lambda * rng.uniform(1 - p, 1 + p),
    )
