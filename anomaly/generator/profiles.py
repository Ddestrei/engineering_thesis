"""
Profile behawioralne 6 ról (dokument „Poprawiony schemat użycia”, sekcja „Profile behawioralne”,
oraz METODOLOGIA D15).

Konwencje:
- dni tygodnia: 0 = poniedziałek ... 6 = niedziela,
- godziny jako liczby zmiennoprzecinkowe (12.5 = 12:30),
- wolumen: liczba operacji w pełnym tygodniu roboczym (5 dni); w tygodniach niepełnych
  skalowany proporcjonalnie do liczby dni roboczych,
- proporcje operacji: udział SELECT (i opcjonalnie DELETE) z przedziału, reszta dzielona
  w stałym stosunku między pozostałe operacje.

Wartości oznaczone [założenie] nie wynikają wprost z dokumentu i zostały dobrane tak,
aby były zgodne z opisem słownym roli.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from anomaly.domain import (CEO, CFO, DELETE, INSERT, MKT, MKT_MGR, PERMISSIONS, SALES, SALES_MGR,
                            SELECT, UPDATE)

WEEKDAYS = (0, 1, 2, 3, 4)


@dataclass(frozen=True)
class DeviceSpec:
    name: str                      # Office_PC, Home_PC, Mobile_Laptop, Private_PC
    weight: float                  # udział w sesjach regularnych
    days: Tuple[int, ...]          # dni tygodnia, w których urządzenie bywa używane
    hour_mean: float
    hour_std: float
    hour_low: float
    hour_high: float


@dataclass(frozen=True)
class OpMix:
    """Tygodniowe proporcje operacji."""
    select: Tuple[float, float]                 # przedział udziału SELECT
    rest: Dict[str, float]                      # podział pozostałych operacji (wagi względne)
    delete: Optional[Tuple[float, float]] = None  # osobny przedział udziału DELETE (jeśli nie ma go w rest)
    select_mean: Optional[float] = None         # gdy średnia nie jest środkiem przedziału


@dataclass(frozen=True)
class Regime:
    """Tryb pracy (np. środek miesiąca vs koniec miesiąca u CFO)."""
    weekly_volume: Tuple[float, float]
    mix: OpMix
    p_after_hours: float = 0.0                  # udział sesji po godzinach w tym trybie
    after_hours_device: Optional[str] = None
    hard_normal_tag: str = ""                   # tag dla wszystkich zdarzeń w tym trybie


@dataclass(frozen=True)
class RoleProfile:
    code: str
    devices: Tuple[DeviceSpec, ...]
    select_weights: Dict[str, float]
    write_weights: Dict[str, Dict[str, float]]
    normal: Regime
    month_end: Optional[Regime] = None
    month_end_days: Tuple[int, int] = (3, 5)    # ostatnie N dni roboczych miesiąca (CFO)
    session_lambda: float = 3.0                 # operacji w sesji: Poisson(λ) + 1   [założenie]
    session_max_ops: int = 12
    intra_session_gap_s: float = 45.0           # średni odstęp między operacjami   [założenie]
    # Trudne przypadki legalne (D15), udział sesji regularnych:
    p_after_hours: float = 0.0
    after_hours_windows: Tuple[Tuple[float, float], ...] = ((17.0, 20.0),)
    after_hours_device: Optional[str] = None    # None = to samo urządzenie co sesja
    p_weekend: float = 0.0
    p_alt_ip: float = 0.0
    alt_ip_device: str = "Mobile_Laptop"
    # Sales Manager: comiesięczne porządkowanie duplikatów klientów (DELETE Customer 1–2 razy).
    monthly_cleanup_deletes: Tuple[int, int] = (0, 0)

    def __post_init__(self):
        # Spójność z uprawnieniami: profil nie może przewidywać operacji bez uprawnień (D3).
        perms = PERMISSIONS[self.code]
        for table in self.select_weights:
            assert table in perms[SELECT], f"{self.code}: SELECT {table} bez uprawnień"
        for op, weights in self.write_weights.items():
            for table in weights:
                assert table in perms[op], f"{self.code}: {op} {table} bez uprawnień"


def _office(days=WEEKDAYS, mean=13.0, std=2.0, low=9.0, high=17.0, weight=1.0) -> DeviceSpec:
    return DeviceSpec("Office_PC", weight, days, mean, std, low, high)


ROLE_PROFILES: Dict[str, RoleProfile] = {
    # CEO: biuro wt–czw 10–15 (~75% ruchu), dom 17–21 w dni robocze (~15%), laptop 9–17 (~10%).
    # 14–48 operacji/tydz.; SELECT 85–95%, INSERT 5–15% wyłącznie do Customer.
    CEO: RoleProfile(
        code=CEO,
        devices=(
            _office(days=(1, 2, 3), mean=12.5, std=1.5, low=10.0, high=15.0, weight=0.75),
            DeviceSpec("Home_PC", 0.15, WEEKDAYS, 19.0, 1.0, 17.0, 21.0),
            DeviceSpec("Mobile_Laptop", 0.10, WEEKDAYS, 13.0, 2.0, 9.0, 17.0),
        ),
        select_weights={"SalesOrderHeader": 0.48, "Customer": 0.35, "Product": 0.17},
        write_weights={INSERT: {"Customer": 1.0}},
        normal=Regime(weekly_volume=(14, 48), mix=OpMix(select=(0.85, 0.95), rest={INSERT: 1.0})),
        session_lambda=3.0,
    ),
    # CFO: pn–pt 9–17, komputer firmowy. Dni 1–24: 27–54 op./tydz., SELECT 20–30%, UPDATE:DELETE = 45:67.
    # Ostatnie 3–5 dni robocze: 43–104 op./tydz., SELECT 71–89%, UPDATE:DELETE = 32:68,
    # praca po 17:00 wyłącznie wtedy, na komputerze prywatnym.
    CFO: RoleProfile(
        code=CFO,
        devices=(_office(),),
        select_weights={"SalesOrderHeader": 0.50, "SalesOrderDetail": 0.35, "Address": 0.15},
        write_weights={UPDATE: {"SalesOrderHeader": 1.0},
                       DELETE: {"SalesOrderDetail": 0.6, "SalesOrderHeader": 0.4}},       # [założenie]
        normal=Regime(weekly_volume=(27, 54), mix=OpMix(select=(0.20, 0.30), rest={UPDATE: 45, DELETE: 67})),
        month_end=Regime(weekly_volume=(43, 104), mix=OpMix(select=(0.71, 0.89), rest={UPDATE: 32, DELETE: 68}),
                         p_after_hours=0.30, after_hours_device="Private_PC",               # [założenie] 30%
                         hard_normal_tag="HARD_NORMAL_MONTH_END"),
        session_lambda=4.0,
    ),
    # Marketing Manager: pn–pt 9–17, firmowy; bardzo rzadko po 17 na prywatnym. 34–53 op./tydz.
    # SELECT 31–41%, DELETE 2–5%, reszta INSERT:UPDATE = 42:67.
    MKT_MGR: RoleProfile(
        code=MKT_MGR,
        devices=(_office(),),
        select_weights={"Product": 0.35, "ProductModel": 0.20, "ProductDescription": 0.15,
                        "ProductModelProductDescription": 0.10, "ProductCategory": 0.20},
        write_weights={INSERT: {"Product": 0.7, "ProductModel": 0.3},
                       UPDATE: {"Product": 0.75, "ProductModel": 0.25},
                       DELETE: {"Product": 0.7, "ProductModel": 0.3}},
        normal=Regime(weekly_volume=(34, 53),
                      mix=OpMix(select=(0.31, 0.41), delete=(0.02, 0.05), rest={INSERT: 42, UPDATE: 67})),
        session_lambda=4.0,
        p_after_hours=0.03, after_hours_device="Private_PC",
    ),
    # Marketing: pn–pt 9–17; incydentalnie weekend, przed 9 / po 17, alternatywne IP. 47–63 op./tydz.
    # SELECT 41–49%, reszta INSERT:UPDATE = 19:31, brak DELETE.
    MKT: RoleProfile(
        code=MKT,
        devices=(_office(),),
        select_weights={"Product": 0.20, "ProductModel": 0.15, "ProductDescription": 0.30,
                        "ProductModelProductDescription": 0.20, "ProductCategory": 0.15},
        write_weights={INSERT: {"ProductDescription": 0.5, "ProductCategory": 0.15, "ProductModelProductDescription": 0.35},
                       UPDATE: {"ProductDescription": 0.6, "ProductCategory": 0.15, "ProductModelProductDescription": 0.25}},
        normal=Regime(weekly_volume=(47, 63), mix=OpMix(select=(0.41, 0.49), rest={INSERT: 19, UPDATE: 31})),
        session_lambda=4.0,
        p_after_hours=0.03, after_hours_windows=((17.0, 20.0), (7.0, 9.0)),
        p_weekend=0.01, p_alt_ip=0.02,
    ),
    # Sales Manager: pn–pt 9–17; rzadko weekend, przed 9 / po 17, alternatywne IP. 39–61 op./tydz.
    # SELECT średnio ~52% (tygodniowo 45–60%), INSERT:UPDATE = 9:11, DELETE Customer 1–2 razy w miesiącu.
    SALES_MGR: RoleProfile(
        code=SALES_MGR,
        devices=(_office(),),
        select_weights={"SalesOrderHeader": 0.30, "SalesOrderDetail": 0.20, "Customer": 0.30,
                        "Address": 0.10, "CustomerAddress": 0.10},
        write_weights={INSERT: {"SalesOrderHeader": 0.35, "SalesOrderDetail": 0.45, "Customer": 0.20},
                       UPDATE: {"Customer": 1.0},
                       DELETE: {"Customer": 1.0}},
        normal=Regime(weekly_volume=(39, 61),
                      mix=OpMix(select=(0.45, 0.60), select_mean=0.52, rest={INSERT: 9, UPDATE: 11})),
        session_lambda=4.0,
        p_after_hours=0.03, after_hours_windows=((17.0, 20.0), (7.0, 9.0)),
        p_weekend=0.01, p_alt_ip=0.02,
        monthly_cleanup_deletes=(1, 2),
    ),
    # Sales: pn–pt 9–17; rzadko weekend, przed 9 / po 17, alternatywne IP. 42–73 op./tydz.
    # SELECT 31–39%, DELETE CustomerAddress 1–3%, reszta INSERT:UPDATE = 13:17.
    SALES: RoleProfile(
        code=SALES,
        devices=(_office(),),
        select_weights={"Customer": 0.35, "Address": 0.25, "CustomerAddress": 0.20, "Product": 0.20},
        write_weights={INSERT: {"Customer": 0.30, "Address": 0.35, "CustomerAddress": 0.35},
                       UPDATE: {"Address": 0.60, "CustomerAddress": 0.40},
                       DELETE: {"CustomerAddress": 1.0}},
        normal=Regime(weekly_volume=(42, 73),
                      mix=OpMix(select=(0.31, 0.39), delete=(0.01, 0.03), rest={INSERT: 13, UPDATE: 17})),
        session_lambda=4.0,
        p_after_hours=0.03, after_hours_windows=((17.0, 20.0), (7.0, 9.0)),
        p_weekend=0.01, p_alt_ip=0.02,
    ),
}
