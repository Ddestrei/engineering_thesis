"""
===============================================================================
CEO Behavioral Log Generator – Stochastic Synthetic Data for ML Experiments
===============================================================================
Generator syntetycznych logów operacji bazodanowych symulujących profil
"Dyrektora Generalnego (CEO)" dla potrzeb detekcji anomalii behawioralnych.

Architektura:
    - Wszystkie parametry rozkładów probabilistycznych są konfigurowalne.
    - Zamiast sztywnych reguł (always/never) stosowane są "miękkie" granice
      probabilistyczne, aby modele ML nie mogły wykryć anomalii przez trywialne
      zapamiętanie granic generatora.
    - Pełna deterministyczność: każde wywołanie z tym samym seedem → identyczny
      wynik, dzięki izolowanym obiektom numpy.random.Generator.

===============================================================================
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, date
from typing import List, Optional, Tuple, Dict

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 1.  KONFIGURACJA PROFILU – PARAMETRY ROZKŁADÓW
# ---------------------------------------------------------------------------

@dataclass
class CEOProfileConfig:
    """
    Konfiguracja profilu CEO.
    Każdy parametr opisuje *rozkład*, nigdy twardą regułę – dzięki temu
    granice danych są „miękkie" i nie da się ich trywialnie wyuczyć.
    """

    # === WOLUMEN TYGODNIOWY ===
    # Rozkład normalny obcięty (truncated normal) zamiast Uniform:
    # pozwala na naturalne "ogonki" – sporadycznie mniej lub więcej operacji.
    weekly_ops_mean: float = 31.0           # μ wolumenu tygodniowego
    weekly_ops_std: float = 8.0             # σ – naturalny rozrzut
    weekly_ops_clip_low: float = 8.0        # dolne obcięcie (twarda ochrona)
    weekly_ops_clip_high: float = 60.0      # górne obcięcie

    # === PROPORCJA SELECT / INSERT ===
    # Rozkład Beta parametryzuje udział SELECT w całości operacji.
    # Beta(α=5.5, β=4.5) → tryb ~ 0.56, 90% masy w [0.35, 0.75].
    # Dzięki ciągłemu rozkładowi proporcja naturalnie fluktuuje tydzień
    # do tygodnia, eliminując sztywne progi 40-70%.
    select_ratio_alpha: float = 5.5
    select_ratio_beta: float = 4.5

    # === TABELE – PRAWDOPODOBIEŃSTWA (softmax-like) ===
    # Dla SELECT tabela jest losowana z rozkładu kategorycznego –
    # drobna szansa na „niestandardowy" wybór zapobiega hard-boundariom.
    select_table_weights: Dict[str, float] = field(default_factory=lambda: {
        "SalesOrderHeader": 0.48,
        "Customer": 0.35,
        "Product": 0.17,
    })
    # INSERT – CEO pisze niemal wyłącznie do Customer.
    # Mikro-prawdopodobieństwo (0.01) pozwala na sporadyczne „pomyłki"
    # w normalnym ruchu, dzięki czemu model nie zapamięta, że INSERT
    # w inną tabelę = anomalia.
    insert_table_weights: Dict[str, float] = field(default_factory=lambda: {
        "Customer": 0.98,
        "SalesOrderHeader": 0.01,
        "Product": 0.01,
    })

    # === TOŻSAMOŚĆ UŻYTKOWNIKA ===
    user_name: str = "CEO"
    login_name: str = "CEO_User"
    user_role: str = "Executive"

    # === URZĄDZENIA I METADANE SIECIOWE ===
    # Wielomianowy rozkład dzienny. Wartości oznaczają *wagę*, nie procent.
    device_weights: Dict[str, float] = field(default_factory=lambda: {
        "Office_PC": 0.75,
        "Home_PC": 0.15,
        "Mobile_Laptop": 0.10,
    })

    # Konkretne metadane sprzętowo-sieciowe powiązane z urządzeniami
    device_metadata: Dict[str, dict] = field(default_factory=lambda: {
        "Office_PC": {
            "client_hostname": "CEO-DESKTOP-OFFICE",
            "simulated_ip": "10.0.4.15",
            "network_zone": "Corporate_LAN",
        },
        "Home_PC": {
            "client_hostname": "CEO-HOME-LAPTOP",
            "simulated_ip": "192.168.1.102",
            "network_zone": "Home_Broadband",
        },
        "Mobile_Laptop": {
            "client_hostname": "CEO-MACBOOK-AIR",
            "simulated_ip": "172.16.88.24",
            "network_zone": "VPN_Remote",
        },
    })

    # === OKNA CZASOWE (na urządzenie) ===
    # Każde okno to (start_hour, end_hour, peak_hour, spread_hours).
    # Czas operacji losowany jest z rozkładu normalnego obciętego do okna –
    # to daje gęstsze centrum i rzadkie brzegi (soft boundary).
    device_time_windows: Dict[str, dict] = field(default_factory=lambda: {
        "Office_PC": {
            # Główne okno Wt-Cz 10-15, z 1-2% szans na Pon/Pt
            "preferred_days": [1, 2, 3],        # wt, śr, czw (0=pon)
            "spillover_days": [0, 4],            # pon, pt
            "spillover_prob": 0.015,             # 1.5% szans na spillover day
            "hour_mean": 12.5,
            "hour_std": 1.5,
            "hour_clip_low": 9.0,
            "hour_clip_high": 16.0,
        },
        "Home_PC": {
            "preferred_days": [0, 1, 2, 3, 4],  # dni robocze
            "spillover_days": [],
            "spillover_prob": 0.0,
            "hour_mean": 19.0,
            "hour_std": 1.0,
            "hour_clip_low": 17.0,
            "hour_clip_high": 21.5,
        },
        "Mobile_Laptop": {
            "preferred_days": [0, 1, 2, 3, 4],
            "spillover_days": [],
            "spillover_prob": 0.0,
            "hour_mean": 13.0,
            "hour_std": 2.0,
            "hour_clip_low": 9.0,
            "hour_clip_high": 17.0,
        },
    })

    # === SESJE ===
    # Operacje CEO grupowane w krótkie sesje (burst model).
    # Liczba operacji w sesji ~ Poisson(λ) + 1, obcięte do [1, max].
    session_ops_lambda: float = 3.0
    session_ops_max: int = 12
    # Odstęp (sekundy) pomiędzy operacjami w jednej sesji: Exponential(β).
    intra_session_interval_beta: float = 45.0  # średni odstęp wewnątrz sesji


# ---------------------------------------------------------------------------
# 2.  KONFIGURACJA ANOMALII
# ---------------------------------------------------------------------------

@dataclass
class AnomalyConfig:
    """
    Parametry kontrolujące wstrzykiwanie anomalii do zbiorów Val/Test.
    Każda anomalia jest niezależnym „incydentem" nakładanym na normalny ruch.
    """
    # Ile incydentów każdej klasy wstrzyknąć w miesiąc.
    # Wartości dobrane tak, aby anomalie stanowiły ~10-20% całego ruchu
    # (normalny ruch ~120-180 rekordów/miesiąc).
    unusual_time_count: int = 2
    volume_spike_count: int = 1
    unusual_ratio_count: int = 2
    rare_resource_count: int = 2

    # --- Parametry poszczególnych anomalii ---

    # A) Nietypowa pora: nocne godziny + weekendy
    unusual_time_hour_range: Tuple[int, int] = (1, 5)
    unusual_time_devices: Tuple[str, ...] = ("Home_PC", "Mobile_Laptop")

    # B) Gwałtowny wzrost: ile dodatkowych operacji w 1-godzinnym burście.
    #    Zakres ograniczony, aby pojedynczy spike nie zdominował zbioru.
    volume_spike_ops_range: Tuple[int, int] = (15, 30)

    # C) Nietypowa proporcja: stosunek INSERT w serii anomalii
    unusual_ratio_insert_frac_range: Tuple[float, float] = (0.85, 0.98)
    unusual_ratio_ops_range: Tuple[int, int] = (8, 16)

    # D) Rzadki zasób: zmasowany dostęp do jednej nietypowej tabeli
    rare_resource_table: str = "Product"
    rare_resource_ops_range: Tuple[int, int] = (8, 18)

    # Poziomy severity losowane z dyskretnego rozkładu
    severity_weights: Dict[int, float] = field(default_factory=lambda: {
        1: 0.10, 2: 0.25, 3: 0.35, 4: 0.20, 5: 0.10,
    })


# ---------------------------------------------------------------------------
# 3.  GENERATORY POMOCNICZE
# ---------------------------------------------------------------------------

def _make_uuid(rng: np.random.Generator) -> str:
    """Generuje deterministyczny UUID z dwóch 63-bitowych losowań (workaround
    dla ograniczenia rng.integers do int64)."""
    return str(uuid.UUID(
        int=int(rng.integers(0, 2**63)) | (int(rng.integers(0, 2**63)) << 64)
    ))


def _truncated_normal(rng: np.random.Generator, mean: float, std: float,
                      low: float, high: float) -> float:
    """
    Losuje wartość z rozkładu normalnego obciętego do [low, high].
    Używany zamiast uniform, aby granice były „miękkie" – gęstość przy
    skrajnych wartościach jest niska, więc model nie może wyuczyć się
    ostrych cut-offów.
    """
    for _ in range(1000):
        val = rng.normal(mean, std)
        if low <= val <= high:
            return val
    return np.clip(rng.normal(mean, std), low, high)


def _sample_hour(rng: np.random.Generator, tw: dict) -> float:
    """
    Losuje godzinę (float) z „miękkiego" okna czasowego:
    rozkład normalny obcięty, z gęstszym centrum i rzadszymi brzegami.
    """
    return _truncated_normal(
        rng, tw["hour_mean"], tw["hour_std"],
        tw["hour_clip_low"], tw["hour_clip_high"]
    )


def _pick_day(rng: np.random.Generator, tw: dict) -> int:
    """
    Losuje dzień tygodnia z uwzględnieniem small spillover probability
    na sąsiednie dni (np. Pon/Pt dla Office_PC).
    """
    if tw["spillover_days"] and rng.random() < tw["spillover_prob"]:
        return rng.choice(tw["spillover_days"])
    return rng.choice(tw["preferred_days"])


def _weighted_choice(rng: np.random.Generator,
                     weights: Dict[str, float]) -> str:
    """Losuje klucz z rozkładu kategorycznego (wagi nie muszą sumować się do 1)."""
    keys = list(weights.keys())
    probs = np.array(list(weights.values()), dtype=float)
    probs /= probs.sum()
    return rng.choice(keys, p=probs)


def _random_severity(rng: np.random.Generator,
                     severity_weights: Dict[int, float]) -> int:
    """Losuje severity z rozkładu dyskretnego."""
    levels = list(severity_weights.keys())
    probs = np.array(list(severity_weights.values()), dtype=float)
    probs /= probs.sum()
    return int(rng.choice(levels, p=probs))


# ---------------------------------------------------------------------------
# 4.  GŁÓWNA KLASA GENERATORA
# ---------------------------------------------------------------------------

class CEOLogGenerator:
    """
    Stochastyczny generator logów aktywności CEO.

    Użycie:
        gen = CEOLogGenerator(train_seed=42, val_seed=123, test_seed=999)
        df_train = gen.generate_train()
        df_val   = gen.generate_val()
        df_test  = gen.generate_test()

    Każdy zbiór obejmuje 1 miesiąc kalendarowy.
    """

    def __init__(
        self,
        train_seed: int = 42,
        val_seed: int = 123,
        test_seed: int = 7777,
        profile: Optional[CEOProfileConfig] = None,
        anomaly_cfg: Optional[AnomalyConfig] = None,
        start_date: Optional[date] = None,
    ):
        self.train_seed = train_seed
        self.val_seed = val_seed
        self.test_seed = test_seed

        self.profile = profile or CEOProfileConfig()
        self.anomaly_cfg = anomaly_cfg or AnomalyConfig()

        # Każdy zbiór zaczyna się 1. dnia kolejnego miesiąca
        base = start_date or date(2026, 1, 1)
        self._train_start = base
        self._val_start = date(base.year, base.month + 1, 1) if base.month < 12 \
            else date(base.year + 1, 1, 1)
        m = self._val_start.month
        self._test_start = date(self._val_start.year, m + 1, 1) if m < 12 \
            else date(self._val_start.year + 1, 1, 1)

    # -----------------------------------------------------------------------
    #  PUBLIC API
    # -----------------------------------------------------------------------

    def generate_train(self) -> pd.DataFrame:
        """
        Zbiór treningowy: wyłącznie normalna aktywność, brak anomalii.
        Modele nienadzorowane uczą się na nim wzorca normy.
        """
        rng = np.random.default_rng(self.train_seed)
        records = self._generate_normal_month(rng, self._train_start)
        return self._to_dataframe(records)

    def generate_val(self) -> pd.DataFrame:
        """
        Zbiór walidacyjny: normalna aktywność + kontrolowane anomalie.
        Służy do doboru progów i parametrów modelu.
        """
        rng = np.random.default_rng(self.val_seed)
        normal = self._generate_normal_month(rng, self._val_start)
        anomalies = self._inject_anomalies(rng, self._val_start, self.anomaly_cfg)
        return self._to_dataframe(normal + anomalies)

    def generate_test(self) -> pd.DataFrame:
        """
        Zbiór testowy: nowy seed + delikatnie zaburzone parametry rozkładów
        + warianty anomalii – testuje generalizację modelu i zapobiega
        wyuczeniu się artefaktów generatora.
        """
        rng = np.random.default_rng(self.test_seed)

        # Perturbacja profilu: drobne przesunięcia parametrów, aby test set
        # nie był kopią val set z innym seedem.
        test_profile = self._perturbed_profile(rng)
        original = self.profile
        self.profile = test_profile

        normal = self._generate_normal_month(rng, self._test_start)

        # Anomalie z nieznacznie zmienionymi parametrami
        test_anomaly_cfg = self._perturbed_anomaly_config(rng)
        anomalies = self._inject_anomalies(rng, self._test_start, test_anomaly_cfg)

        self.profile = original  # przywrócenie oryginału
        return self._to_dataframe(normal + anomalies)

    # -----------------------------------------------------------------------
    #  GENEROWANIE NORMALNEGO RUCHU
    # -----------------------------------------------------------------------

    def _generate_normal_month(
        self, rng: np.random.Generator, month_start: date
    ) -> List[dict]:
        """
        Generuje normalny ruch CEO na przestrzeni 1 miesiąca.

        Algorytm:
        1. Iteruje po tygodniach miesiąca.
        2. Dla każdego tygodnia losuje wolumen z truncated-normal
           (miękkie granice zamiast uniform).
        3. Rozdziela operacje na sesje (Poisson-burst model).
        4. Dla każdej sesji losuje urządzenie, dzień, godzinę, operacje.
        """
        month_end = self._month_end(month_start)
        records: List[dict] = []

        week_start = month_start
        while week_start < month_end:
            week_end = min(week_start + timedelta(days=7), month_end)

            # --- Wolumen tygodniowy z rozkładu normalnego obciętego ---
            weekly_ops = int(round(_truncated_normal(
                rng,
                self.profile.weekly_ops_mean,
                self.profile.weekly_ops_std,
                self.profile.weekly_ops_clip_low,
                self.profile.weekly_ops_clip_high,
            )))

            # --- Proporcja SELECT z rozkładu Beta (ciągłe miękkie granice) ---
            select_ratio = rng.beta(
                self.profile.select_ratio_alpha,
                self.profile.select_ratio_beta,
            )
            n_select = int(round(weekly_ops * select_ratio))
            n_insert = weekly_ops - n_select

            # Lista operacji do rozdzielenia w sesje
            ops_queue: List[str] = (
                ["SELECT"] * n_select + ["INSERT"] * n_insert
            )
            rng.shuffle(ops_queue)

            # --- Podział na sesje (Poisson burst) ---
            idx = 0
            while idx < len(ops_queue):
                session_len = min(
                    int(rng.poisson(self.profile.session_ops_lambda)) + 1,
                    self.profile.session_ops_max,
                    len(ops_queue) - idx,
                )
                session_ops = ops_queue[idx: idx + session_len]
                idx += session_len

                # Losowanie kontekstu sesji
                device = _weighted_choice(rng, self.profile.device_weights)
                tw = self.profile.device_time_windows[device]

                day_of_week = _pick_day(rng, tw)
                # Mapowanie dnia tygodnia na konkretną datę w bieżącym tygodniu
                session_date = self._resolve_day_in_week(
                    week_start, week_end, day_of_week, rng
                )
                if session_date is None or session_date >= month_end:
                    session_date = week_start  # fallback

                base_hour = _sample_hour(rng, tw)
                session_id = _make_uuid(rng)

                for op_idx, op_type in enumerate(session_ops):
                    # Intra-session gap – wykładniczy odstęp
                    gap_sec = rng.exponential(
                        self.profile.intra_session_interval_beta
                    ) if op_idx > 0 else 0.0

                    # Czas = base_hour + skumulowany gap (z szumem sekund)
                    ts = datetime.combine(session_date, datetime.min.time()) + \
                         timedelta(hours=base_hour) + \
                         timedelta(seconds=gap_sec * op_idx) + \
                         timedelta(seconds=rng.uniform(0, 5))

                    # Wybór tabeli z rozkładu kategorycznego
                    if op_type == "SELECT":
                        table = _weighted_choice(
                            rng, self.profile.select_table_weights
                        )
                    else:
                        table = _weighted_choice(
                            rng, self.profile.insert_table_weights
                        )

                    dev_meta = self.profile.device_metadata.get(
                        device, {
                            "client_hostname": "CEO-DESKTOP-OFFICE",
                            "simulated_ip": "10.0.4.15",
                        }
                    )

                    records.append({
                        "timestamp": ts,
                        "session_id": session_id,
                        "user": self.profile.user_name,
                        "login_name": self.profile.login_name,
                        "client_hostname": dev_meta["client_hostname"],
                        "simulated_ip": dev_meta["simulated_ip"],
                        "device_context": device,
                        "operation_type": op_type,
                        "target_table": table,
                        "scenario_id": "",
                        "is_anomaly": 0,
                        "anomaly_class": "None",
                        "severity_level": "",
                    })

            week_start = week_end

        return records

    # -----------------------------------------------------------------------
    #  WSTRZYKIWANIE ANOMALII
    # -----------------------------------------------------------------------

    def _inject_anomalies(
        self, rng: np.random.Generator, month_start: date,
        cfg: AnomalyConfig
    ) -> List[dict]:
        """
        Generuje anomalie i nakłada je na normalny miesiąc.
        Każda anomalia to osobny „incydent" z unikalnym scenario_id.
        """
        month_end = self._month_end(month_start)
        all_anomalies: List[dict] = []

        # A) NIETYPOWA PORA
        for _ in range(cfg.unusual_time_count):
            scenario_id = f"ANOM_TIME_{rng.integers(0, 2**32):08x}"
            day = self._random_date_in_range(rng, month_start, month_end)
            # Weekendowa aktywność lub nocna w dzień roboczy
            if rng.random() < 0.5:
                # weekend
                while day.weekday() < 5:
                    day += timedelta(days=1)
                    if day >= month_end:
                        day = month_start + timedelta(
                            days=int(rng.integers(0, (month_end - month_start).days))
                        )
                hour = rng.uniform(9, 18)
            else:
                hour = rng.uniform(*cfg.unusual_time_hour_range)

            device = rng.choice(list(cfg.unusual_time_devices))
            # 30% szans na logowanie z całkowicie obcego urządzenia / podejrzanego IP
            if rng.random() < 0.30:
                dev_host = "ROGUE-EXT-PC"
                dev_ip = f"185.220.101.{int(rng.integers(2, 250))}"
            else:
                dev_meta = self.profile.device_metadata.get(device, {})
                dev_host = dev_meta.get("client_hostname", "CEO-HOME-LAPTOP")
                dev_ip = dev_meta.get("simulated_ip", "192.168.1.102")

            n_ops = int(rng.integers(3, 10))
            severity = _random_severity(rng, cfg.severity_weights)
            session_id = _make_uuid(rng)

            for i in range(n_ops):
                ts = datetime.combine(day, datetime.min.time()) + \
                     timedelta(hours=hour, minutes=rng.uniform(0, 30),
                               seconds=rng.uniform(0, 60))
                op = _weighted_choice(rng, {"SELECT": 0.7, "INSERT": 0.3})
                table = _weighted_choice(rng, self.profile.select_table_weights)
                all_anomalies.append({
                    "timestamp": ts,
                    "session_id": session_id,
                    "user": self.profile.user_name,
                    "login_name": self.profile.login_name,
                    "client_hostname": dev_host,
                    "simulated_ip": dev_ip,
                    "device_context": device,
                    "operation_type": op,
                    "target_table": table,
                    "scenario_id": scenario_id,
                    "is_anomaly": 1,
                    "anomaly_class": "unusual_time",
                    "severity_level": severity,
                })

        # B) GWAŁTOWNY WZROST WOLUMENU
        for _ in range(cfg.volume_spike_count):
            scenario_id = f"ANOM_VOLUME_{rng.integers(0, 2**32):08x}"
            day = self._random_weekday(rng, month_start, month_end)
            hour = rng.uniform(10, 15)
            n_ops = int(rng.integers(*cfg.volume_spike_ops_range))
            severity = _random_severity(rng, cfg.severity_weights)
            device = "Office_PC"
            dev_meta = self.profile.device_metadata.get(device, {})
            dev_host = dev_meta.get("client_hostname", "CEO-DESKTOP-OFFICE")
            dev_ip = dev_meta.get("simulated_ip", "10.0.4.15")
            session_id = _make_uuid(rng)

            for i in range(n_ops):
                # Operacje ściśnięte w ~60 min
                ts = datetime.combine(day, datetime.min.time()) + \
                     timedelta(hours=hour,
                               seconds=rng.uniform(0, 3600))
                op = _weighted_choice(rng, {"SELECT": 0.65, "INSERT": 0.35})
                table = _weighted_choice(rng, self.profile.select_table_weights)
                all_anomalies.append({
                    "timestamp": ts,
                    "session_id": session_id,
                    "user": self.profile.user_name,
                    "login_name": self.profile.login_name,
                    "client_hostname": dev_host,
                    "simulated_ip": dev_ip,
                    "device_context": device,
                    "operation_type": op,
                    "target_table": table,
                    "scenario_id": scenario_id,
                    "is_anomaly": 1,
                    "anomaly_class": "volume_spike",
                    "severity_level": severity,
                })

        # C) NIETYPOWA PROPORCJA OPERACJI
        for _ in range(cfg.unusual_ratio_count):
            scenario_id = f"ANOM_RATIO_{rng.integers(0, 2**32):08x}"
            day = self._random_weekday(rng, month_start, month_end)
            hour = rng.uniform(10, 15)
            n_ops = int(rng.integers(*cfg.unusual_ratio_ops_range))
            insert_frac = rng.uniform(*cfg.unusual_ratio_insert_frac_range)
            severity = _random_severity(rng, cfg.severity_weights)
            device = "Office_PC"
            dev_meta = self.profile.device_metadata.get(device, {})
            dev_host = dev_meta.get("client_hostname", "CEO-DESKTOP-OFFICE")
            dev_ip = dev_meta.get("simulated_ip", "10.0.4.15")
            session_id = _make_uuid(rng)

            for i in range(n_ops):
                ts = datetime.combine(day, datetime.min.time()) + \
                     timedelta(hours=hour,
                               seconds=rng.uniform(0, 5400))
                # Anomalia: odwrócona proporcja – dominacja INSERT
                op = "INSERT" if rng.random() < insert_frac else "SELECT"
                table = _weighted_choice(rng, self.profile.insert_table_weights)
                all_anomalies.append({
                    "timestamp": ts,
                    "session_id": session_id,
                    "user": self.profile.user_name,
                    "login_name": self.profile.login_name,
                    "client_hostname": dev_host,
                    "simulated_ip": dev_ip,
                    "device_context": device,
                    "operation_type": op,
                    "target_table": table,
                    "scenario_id": scenario_id,
                    "is_anomaly": 1,
                    "anomaly_class": "unusual_ratio",
                    "severity_level": severity,
                })

        # D) DOSTĘP DO RZADKO UŻYWANEGO ZASOBU
        for _ in range(cfg.rare_resource_count):
            scenario_id = f"ANOM_RARE_{rng.integers(0, 2**32):08x}"
            day = self._random_weekday(rng, month_start, month_end)
            hour = rng.uniform(10, 15)
            n_ops = int(rng.integers(*cfg.rare_resource_ops_range))
            severity = _random_severity(rng, cfg.severity_weights)
            device = "Office_PC"
            dev_meta = self.profile.device_metadata.get(device, {})
            dev_host = dev_meta.get("client_hostname", "CEO-DESKTOP-OFFICE")
            dev_ip = dev_meta.get("simulated_ip", "10.0.4.15")
            session_id = _make_uuid(rng)

            for i in range(n_ops):
                ts = datetime.combine(day, datetime.min.time()) + \
                     timedelta(hours=hour,
                               seconds=rng.uniform(0, 3600))
                # Anomalia: wyłącznie tabela Product (rzadka samodzielnie)
                all_anomalies.append({
                    "timestamp": ts,
                    "session_id": session_id,
                    "user": self.profile.user_name,
                    "login_name": self.profile.login_name,
                    "client_hostname": dev_host,
                    "simulated_ip": dev_ip,
                    "device_context": device,
                    "operation_type": "SELECT",
                    "target_table": cfg.rare_resource_table,
                    "scenario_id": scenario_id,
                    "is_anomaly": 1,
                    "anomaly_class": "rare_resource_access",
                    "severity_level": severity,
                })

        return all_anomalies

    # -----------------------------------------------------------------------
    #  PERTURBACJA PARAMETRÓW DLA ZBIORU TESTOWEGO
    # -----------------------------------------------------------------------

    def _perturbed_profile(self, rng: np.random.Generator) -> CEOProfileConfig:
        """
        Tworzy kopię profilu z nieznacznie zaburzonymi parametrami.
        Cel: zbiór testowy nie jest kopią walidacyjnego z innym seedem –
        drobne przesunięcia rozkładów testują, czy model generalizuje
        poza artefaktami generatora.
        """
        p = self.profile

        def jitter(val: float, pct: float = 0.10) -> float:
            return val * (1.0 + rng.uniform(-pct, pct))

        return CEOProfileConfig(
            user_name=p.user_name,
            login_name=p.login_name,
            user_role=p.user_role,
            device_metadata=p.device_metadata.copy(),
            weekly_ops_mean=jitter(p.weekly_ops_mean),
            weekly_ops_std=jitter(p.weekly_ops_std, 0.15),
            weekly_ops_clip_low=p.weekly_ops_clip_low,
            weekly_ops_clip_high=p.weekly_ops_clip_high,
            select_ratio_alpha=jitter(p.select_ratio_alpha, 0.08),
            select_ratio_beta=jitter(p.select_ratio_beta, 0.08),
            select_table_weights=p.select_table_weights.copy(),
            insert_table_weights=p.insert_table_weights.copy(),
            device_weights=p.device_weights.copy(),
            device_time_windows=p.device_time_windows.copy(),
            session_ops_lambda=jitter(p.session_ops_lambda, 0.12),
            session_ops_max=p.session_ops_max,
            intra_session_interval_beta=jitter(
                p.intra_session_interval_beta, 0.10
            ),
        )

    def _perturbed_anomaly_config(
        self, rng: np.random.Generator
    ) -> AnomalyConfig:
        """
        Zwraca AnomalyConfig ze zmienionymi licznikami i zakresami,
        aby warianty anomalii testowych różniły się od walidacyjnych.
        """
        cfg = self.anomaly_cfg

        def jitter_int(val: int, delta: int = 1) -> int:
            return max(1, val + int(rng.integers(-delta, delta + 1)))

        def jitter_range(r: Tuple[int, int], delta: int = 5) -> Tuple[int, int]:
            lo = max(1, r[0] + int(rng.integers(-delta, delta + 1)))
            hi = max(lo + 1, r[1] + int(rng.integers(-delta, delta + 1)))
            return (lo, hi)

        return AnomalyConfig(
            unusual_time_count=jitter_int(cfg.unusual_time_count),
            volume_spike_count=jitter_int(cfg.volume_spike_count),
            unusual_ratio_count=jitter_int(cfg.unusual_ratio_count),
            rare_resource_count=jitter_int(cfg.rare_resource_count),
            unusual_time_hour_range=cfg.unusual_time_hour_range,
            unusual_time_devices=cfg.unusual_time_devices,
            volume_spike_ops_range=jitter_range(cfg.volume_spike_ops_range),
            unusual_ratio_insert_frac_range=cfg.unusual_ratio_insert_frac_range,
            unusual_ratio_ops_range=jitter_range(cfg.unusual_ratio_ops_range, 3),
            rare_resource_table=cfg.rare_resource_table,
            rare_resource_ops_range=jitter_range(cfg.rare_resource_ops_range),
            severity_weights=cfg.severity_weights.copy(),
        )

    # -----------------------------------------------------------------------
    #  UTILITY METHODS
    # -----------------------------------------------------------------------

    @staticmethod
    def _month_end(month_start: date) -> date:
        """Zwraca pierwszy dzień następnego miesiąca (exclusive bound)."""
        if month_start.month == 12:
            return date(month_start.year + 1, 1, 1)
        return date(month_start.year, month_start.month + 1, 1)

    @staticmethod
    def _random_date_in_range(
        rng: np.random.Generator, start: date, end: date
    ) -> date:
        """Losowa data z zakresu [start, end)."""
        delta = (end - start).days
        if delta <= 0:
            return start
        return start + timedelta(days=int(rng.integers(0, delta)))

    @staticmethod
    def _random_weekday(
        rng: np.random.Generator, start: date, end: date
    ) -> date:
        """Losowy dzień roboczy z zakresu [start, end)."""
        for _ in range(200):
            d = CEOLogGenerator._random_date_in_range(rng, start, end)
            if d.weekday() < 5:
                return d
        return start  # fallback

    @staticmethod
    def _resolve_day_in_week(
        week_start: date, week_end: date, target_dow: int,
        rng: np.random.Generator
    ) -> Optional[date]:
        """
        Mapuje dzień tygodnia (0=pon) na konkretną datę w bieżącym tygodniu.
        Jeśli nie mieści się w zakresie, zwraca losowy dzień z tygodnia.
        """
        # Szukamy najbliższej daty o danym dow w przedziale
        d = week_start
        while d < week_end:
            if d.weekday() == target_dow:
                return d
            d += timedelta(days=1)
        # Fallback: losowy dzień w tygodniu
        delta = (week_end - week_start).days
        if delta <= 0:
            return week_start
        return week_start + timedelta(days=int(rng.integers(0, delta)))

    @staticmethod
    def _to_dataframe(records: List[dict]) -> pd.DataFrame:
        """Konwertuje rekordy na DataFrame, sortuje chronologicznie.
        Zapewnia spójne typy kolumn i czytelne wartości domyślne."""
        df = pd.DataFrame(records)
        if df.empty:
            return df
        df = df.sort_values("timestamp").reset_index(drop=True)
        # Zamiana pustych stringów na pd.NA zamiast float NaN –
        # zachowuje czytelność przy zapisie do CSV.
        df["scenario_id"] = df["scenario_id"].replace("", pd.NA)
        df["severity_level"] = df["severity_level"].replace("", pd.NA)
        # Jawne typy
        df["is_anomaly"] = df["is_anomaly"].astype(int)
        df["anomaly_class"] = df["anomaly_class"].astype(str)
        return df


# ---------------------------------------------------------------------------
# 5.  PUNKT WEJŚCIA – GENEROWANIE I ZAPIS DO CSV
# ---------------------------------------------------------------------------

def main():
    """
    Generuje trzy zbiory (Train, Val, Test) i zapisuje je do plików CSV
    w katalogu wyjściowym.
    """
    import os

    OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "generated_data")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=" * 70)
    print("  CEO Log Generator – Stochastic Synthetic Data for ML")
    print("=" * 70)

    gen = CEOLogGenerator(
        train_seed=42,
        val_seed=123,
        test_seed=7777,
        start_date=date(2026, 1, 1),
    )

    # --- TRAIN ---
    print("\n[1/3] Generowanie zbioru treningowego (Train)...")
    df_train = gen.generate_train()
    train_path = os.path.join(OUTPUT_DIR, "ceo_train.csv")
    df_train.to_csv(train_path, index=False)
    print(f"  -> Zapisano {len(df_train)} rekordów -> {train_path}")
    print(f"  -> Anomalie: {df_train['is_anomaly'].sum()} (oczekiwane: 0)")
    _print_summary(df_train, "Train")

    # --- VALIDATION ---
    print("\n[2/3] Generowanie zbioru walidacyjnego (Validation)...")
    df_val = gen.generate_val()
    val_path = os.path.join(OUTPUT_DIR, "ceo_validation.csv")
    df_val.to_csv(val_path, index=False)
    n_anom = df_val["is_anomaly"].sum()
    print(f"  -> Zapisano {len(df_val)} rekordów -> {val_path}")
    print(f"  -> Anomalie: {n_anom} ({100*n_anom/len(df_val):.1f}%)")
    _print_summary(df_val, "Validation")

    # --- TEST ---
    print("\n[3/3] Generowanie zbioru testowego (Test)...")
    df_test = gen.generate_test()
    test_path = os.path.join(OUTPUT_DIR, "ceo_test.csv")
    df_test.to_csv(test_path, index=False)
    n_anom = df_test["is_anomaly"].sum()
    print(f"  -> Zapisano {len(df_test)} rekordów -> {test_path}")
    print(f"  -> Anomalie: {n_anom} ({100*n_anom/len(df_test):.1f}%)")
    _print_summary(df_test, "Test")

    print("\n" + "=" * 70)
    print("  Generowanie zakończone pomyślnie.")
    print("=" * 70)


def _print_summary(df: pd.DataFrame, name: str):
    """Drukuje podstawowe statystyki zbioru."""
    print(f"\n  --- {name} Summary ---")
    print(f"  Zakres dat:       {df['timestamp'].min()} -> {df['timestamp'].max()}")
    print(f"  Operacje (SELECT/INSERT):")
    for op, cnt in df["operation_type"].value_counts().items():
        print(f"    {op}: {cnt} ({100*cnt/len(df):.1f}%)")
    print(f"  Tabele:")
    for tbl, cnt in df["target_table"].value_counts().items():
        print(f"    {tbl}: {cnt} ({100*cnt/len(df):.1f}%)")
    print(f"  Urządzenia:")
    for dev, cnt in df["device_context"].value_counts().items():
        print(f"    {dev}: {cnt} ({100*cnt/len(df):.1f}%)")
    if df["is_anomaly"].sum() > 0:
        print(f"  Klasy anomalii:")
        for cls, cnt in df.loc[df["is_anomaly"] == 1, "anomaly_class"] \
                .value_counts().items():
            print(f"    {cls}: {cnt}")


if __name__ == "__main__":
    main()
