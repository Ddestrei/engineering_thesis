from __future__ import annotations

import uuid
from datetime import datetime, timedelta, date
from typing import List, Optional, Tuple, Dict

import numpy as np
import pandas as pd

from CEOProfileConfig import CEOProfileConfig
from AnomalyConfig import AnomalyConfig

def _weighted_choice(rng: np.random.Generator,
                     weights: Dict[str, float]) -> str:
    """Losuje klucz z rozkładu kategorycznego"""
    keys = list(weights.keys())
    probs = np.array(list(weights.values()), dtype=float)
    probs /= probs.sum()
    return rng.choice(keys, p=probs)

def _sample_hour(rng: np.random.Generator, tw: dict) -> float:
    """
    Losuje godzinę float z rozkład normalnego okna czasowego
    """
    return _truncated_normal(
        rng, tw["hour_mean"], tw["hour_std"],
        tw["hour_clip_low"], tw["hour_clip_high"]
    )

def _random_severity(rng: np.random.Generator,
                     severity_weights: Dict[int, float]) -> int:
    levels = list(severity_weights.keys())
    probs = np.array(list(severity_weights.values()), dtype=float)
    probs /= probs.sum()
    return int(rng.choice(levels, p=probs))


def _pick_day(rng: np.random.Generator, tw: dict) -> int:
    """
    Losuje dzień tygodnia z uwzględnieniem small spillover probability
    na sąsiednie dni
    """
    if tw["spillover_days"] and rng.random() < tw["spillover_prob"]:
        return rng.choice(tw["spillover_days"])
    return rng.choice(tw["preferred_days"])

def _make_uuid(rng: np.random.Generator) -> str:
    #Generuje deterministyczny UUID z dwóch 63-bitowych losowań
    return str(uuid.UUID(
        int=int(rng.integers(0, 2**63)) | (int(rng.integers(0, 2**63)) << 64)
    ))

def _truncated_normal(rng: np.random.Generator, mean: float, std: float,
                      low: float, high: float) -> float:
    """
    Losuje wartość z rozkładu normalnego obciętego do [low, high]
    Używany zamiast uniform, aby granice były miękkie
    Gęstość przy skrajnych wartościach jest niska, więc model nie może wyuczyć się
    ostrych granic
    """
    for _ in range(1000):
        val = rng.normal(mean, std)
        if low <= val <= high:
            return val
    return np.clip(rng.normal(mean, std), low, high)


class CEOLogGenerator:
    """
    Stochastyczny generator logów aktywności CEO
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

    def generate_train(self) -> pd.DataFrame:
        """
        Zbiór treningowy zawiera wyłącznie normalną aktywność, brak anomalii
        Modele nienadzorowane uczą się na nim normy
        """
        rng = np.random.default_rng(self.train_seed)
        records = self._generate_normal_month(rng, self._train_start)
        return self._to_dataframe(records)

    def generate_val(self) -> pd.DataFrame:
        """
        Zbiór walidacyjny zawiera normalnż aktywność + kontrolowane anomalie
        Służy do doboru progów i parametrów modelu
        """
        rng = np.random.default_rng(self.val_seed)
        normal = self._generate_normal_month(rng, self._val_start)
        anomalies = self._inject_anomalies(rng, self._val_start, self.anomaly_cfg)
        return self._to_dataframe(normal + anomalies)

    def generate_test(self) -> pd.DataFrame:
        """
        Zbiór testowy powstaje na podstawie nowego seed-a i 
        zawiera delikatnie zaburzone parametry rozkładów wariantów anomalii
        Testuje generalizację modelu i zapobiega wyuczeniu się artefaktów generatora
        """
        rng = np.random.default_rng(self.test_seed)

        # Perturbacja profilu dodaje drobne przesunięcia parametrów, aby test
        # nie był kopią val z innym seedem
        test_profile = self._perturbed_profile(rng)
        original = self.profile
        self.profile = test_profile

        normal = self._generate_normal_month(rng, self._test_start)

        # Anomalie z nieznacznie zmienionymi parametrami
        test_anomaly_cfg = self._perturbed_anomaly_config(rng)
        anomalies = self._inject_anomalies(rng, self._test_start, test_anomaly_cfg)

        self.profile = original
        return self._to_dataframe(normal + anomalies)

    def _generate_normal_month(
        self, rng: np.random.Generator, month_start: date
    ) -> List[dict]:
        """
        Generuje normalny ruch CEO na przestrzeni 1 miesiąca
        """
        month_end = self._month_end(month_start)
        records: List[dict] = []

        week_start = month_start
        while week_start < month_end:
            week_end = min(week_start + timedelta(days=7), month_end)

            
            weekly_ops = int(round(_truncated_normal(
                rng,
                self.profile.weekly_ops_mean,
                self.profile.weekly_ops_std,
                self.profile.weekly_ops_clip_low,
                self.profile.weekly_ops_clip_high,
            )))

            select_ratio = rng.beta(
                self.profile.select_ratio_alpha,
                self.profile.select_ratio_beta,
            )
            n_select = int(round(weekly_ops * select_ratio))
            n_insert = weekly_ops - n_select

            ops_queue: List[str] = (
                ["SELECT"] * n_select + ["INSERT"] * n_insert
            )
            rng.shuffle(ops_queue)

            idx = 0
            while idx < len(ops_queue):
                session_len = min(
                    int(rng.poisson(self.profile.session_ops_lambda)) + 1,
                    self.profile.session_ops_max,
                    len(ops_queue) - idx,
                )
                session_ops = ops_queue[idx: idx + session_len]
                idx += session_len

                device = _weighted_choice(rng, self.profile.device_weights)
                tw = self.profile.device_time_windows[device]

                day_of_week = _pick_day(rng, tw)
                session_date = self._resolve_day_in_week(
                    week_start, week_end, day_of_week, rng
                )
                if session_date is None or session_date >= month_end:
                    session_date = week_start  

                base_hour = _sample_hour(rng, tw)
                session_id = _make_uuid(rng)

                for op_idx, op_type in enumerate(session_ops):
                    gap_sec = rng.exponential(
                        self.profile.intra_session_interval_beta
                    ) if op_idx > 0 else 0.0

                    ts = datetime.combine(session_date, datetime.min.time()) + \
                         timedelta(hours=base_hour) + \
                         timedelta(seconds=gap_sec * op_idx) + \
                         timedelta(seconds=rng.uniform(0, 5))

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


    def _inject_anomalies(
        self, rng: np.random.Generator, month_start: date,
        cfg: AnomalyConfig
    ) -> List[dict]:
        """
        Generuje anomalie i nakłada je na normalny miesiąc
        Każda anomalia to osobny incydent z unikalnym scenario_id
        """
        month_end = self._month_end(month_start)
        all_anomalies: List[dict] = []

        #Nietypowa pora
        for _ in range(cfg.unusual_time_count):
            scenario_id = f"ANOM_TIME_{rng.integers(0, 2**32):08x}"
            day = self._random_date_in_range(rng, month_start, month_end)
            #Weekendowa aktywność lub nocna w dzień roboczy
            if rng.random() < 0.5:
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

        # B)Gwałtowny wzrost wolumenu
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

        # C)Nietypowa pora operacji
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

        # D)Dostęp do rzadko urzywanego zasobu 
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

    def _perturbed_profile(self, rng: np.random.Generator) -> CEOProfileConfig:
        """
        Tworzy kopię profilu z nieznacznie zaburzonymi parametrami
        Celem tego działania jest to że zbiór testowy nie jest kopią walidacyjnego z innym seedem
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


    @staticmethod
    def _month_end(month_start: date) -> date:
        if month_start.month == 12:
            return date(month_start.year + 1, 1, 1)
        return date(month_start.year, month_start.month + 1, 1)

    @staticmethod
    def _random_date_in_range(
        rng: np.random.Generator, start: date, end: date
    ) -> date:
        delta = (end - start).days
        if delta <= 0:
            return start
        return start + timedelta(days=int(rng.integers(0, delta)))

    @staticmethod
    def _random_weekday(
        rng: np.random.Generator, start: date, end: date
    ) -> date:
        for _ in range(200):
            d = CEOLogGenerator._random_date_in_range(rng, start, end)
            if d.weekday() < 5:
                return d
        return start

    @staticmethod
    def _resolve_day_in_week(
        week_start: date, week_end: date, target_dow: int,
        rng: np.random.Generator
    ) -> Optional[date]:
        d = week_start
        while d < week_end:
            if d.weekday() == target_dow:
                return d
            d += timedelta(days=1)
        delta = (week_end - week_start).days
        if delta <= 0:
            return week_start
        return week_start + timedelta(days=int(rng.integers(0, delta)))

    @staticmethod
    def _to_dataframe(records: List[dict]) -> pd.DataFrame:
        """
        Konwertuje rekordy na DataFrame, sortuje chronologicznie
        Zapewnia spójne typy kolumn i czytelne wartości domyślne
        """
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