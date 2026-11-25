from pathlib import Path
from typing import Union, List
import pandas as pd
import numpy as np


FEATURE_NAMES: List[str] = [
    "DurationMicroseconds",   # 0: Czas trwania wykonania w mikrosekundach
    "CpuTimeMicroseconds",    # 1: Czas pracy procesora CPU
    "LogicalReads",           # 2: Odczyty logiczne z bufora pamięci
    "Writes",                 # 3: Zapisy fizyczne
    "RowCount",               # 4: Liczba zwróconych/zmodyfikowanych wierszy
    "SqlTextLength",          # 5: Długość tekstu zapytania SQL
    "HourOfDay",              # 6: Godzina w ciągu doby (0 - 23)
    "DayOfWeek",              # 7: Dzień tygodnia (0: Niedz, 1: Pon, ..., 6: Sob)
    "IsWeekend",              # 8: Flaga weekendu
    "IsNightHours",           # 9: Flaga godzin nocnych
    "IsError",                # 10: Flaga wystąpienia błędu bazy
    "ErrorNumber",            # 11: Kod numeryczny błędu
    "IsSelect",               # 12: Flaga operacji SELECT
    "IsInsert",               # 13: Flaga operacji INSERT
    "IsUpdate",               # 14: Flaga operacji UPDATE
    "IsDelete",               # 15: Flaga operacji DELETE
    "IsOtherOp",              # 16: Flaga innej operacji
    "IsCustomer",             # 17: Odwołanie do tabeli Customer
    "IsProduct",              # 18: Odwołanie do tabeli Product
    "IsSalesOrderHeader",     # 19: Odwołanie do tabeli SalesOrderHeader
    "IsOtherTable",           # 20: Odwołanie do innej tabeli
    "IsOfficePc",             # 21: Urządzenie: Komputer biurowy
    "IsHomePc",               # 22: Urządzenie: Komputer domowy
    "IsMobileLaptop",         # 23: Urządzenie: Służbowy laptop
    "IsRogueDevice"           # 24: Urządzenie: Obce / nieautoryzowane
]

class AnomalyDataLoader:
    """
    Moduł wczytywania i walidacji danych telemetrycznych.
    Wspiera zarówno bezpośrednio zwektoryzowane pliki CSV, jak i surowe logi generatora.
    """
    @staticmethod
    def load_split(file_path: Union[str, Path], seed: int = 42) -> pd.DataFrame:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Nie odnaleziono pliku: {path.resolve()}")

        df_raw = pd.read_csv(path)

        if all(feat in df_raw.columns for feat in FEATURE_NAMES) and "IsAnomaly" in df_raw.columns:
            return df_raw.copy()

        return AnomalyDataLoader.vectorize_raw_dataframe(df_raw, seed=seed)

    @staticmethod
    def vectorize_raw_dataframe(df_raw: pd.DataFrame, seed: int = 42) -> pd.DataFrame:
        """
        Wektoryzuje surowy zbiór z generatora do 25 numerycznych cech.
        Zachowuje pełny determinizm oraz zgodność z metrykami SQL Server.
        """
        rng = np.random.default_rng(seed)
        df = df_raw.copy()

        ts = pd.to_datetime(df["timestamp"])
        hour = ts.dt.hour
        dow = ts.dt.dayofweek

        is_anomaly = df["is_anomaly"].astype(int) if "is_anomaly" in df.columns else df.get("IsAnomaly", 0).astype(int)

        is_weekend = dow.isin([5, 6]).astype(float)
        is_night = ((hour < 6) | (hour >= 22)).astype(float)

        op = df["operation_type"].astype(str).str.upper()
        is_select = (op == "SELECT").astype(float)
        is_insert = (op == "INSERT").astype(float)
        is_update = (op == "UPDATE").astype(float)
        is_delete = (op == "DELETE").astype(float)
        is_other_op = (~(op.isin(["SELECT", "INSERT", "UPDATE", "DELETE"]))).astype(float)

        tbl = df["target_table"].astype(str)
        is_cust = (tbl.str.lower() == "customer").astype(float)
        is_prod = (tbl.str.lower() == "product").astype(float)
        is_soh = (tbl.str.lower() == "salesorderheader").astype(float)
        is_other_tbl = (~(tbl.str.lower().isin(["customer", "product", "salesorderheader"]))).astype(float)

        dev = df["device_context"].astype(str)
        is_office = (dev == "Office_PC").astype(float)
        is_home = (dev == "Home_PC").astype(float)
        is_mob = (dev == "Mobile_Laptop").astype(float)
        is_rogue = (~(dev.isin(["Office_PC", "Home_PC", "Mobile_Laptop"]))).astype(float)

        is_err = df["severity_level"].fillna(0).astype(float) >= 11 if "severity_level" in df.columns else np.zeros(len(df))
        err_num = np.where(is_err, 50000, 0)

        duration, reads, writes, rows, text_len = [], [], [], [], []
        for _, row in df.iterrows():
            t = str(row["target_table"]).lower()
            o = str(row["operation_type"]).upper()
            if o == "SELECT":
                writes.append(0)
                if t == "customer":
                    reads.append(int(rng.integers(6, 25)))
                    rows.append(1)
                    duration.append(float(rng.normal(2200, 400)))
                    text_len.append(245)
                elif t == "product":
                    reads.append(int(rng.integers(80, 160)))
                    rows.append(int(rng.integers(10, 50)))
                    duration.append(float(rng.normal(3200, 600)))
                    text_len.append(260)
                else:
                    reads.append(int(rng.integers(24, 45)))
                    rows.append(int(rng.integers(5, 15)))
                    duration.append(float(rng.normal(2600, 500)))
                    text_len.append(255)
            else:
                writes.append(1)
                reads.append(int(rng.integers(6, 12)))
                rows.append(1)
                duration.append(float(rng.normal(1800, 300)))
                text_len.append(455)

        cpu_time = [max(0.0, d * 0.15 + rng.normal(0, 50)) for d in duration]

        vec_df = pd.DataFrame({
            "DurationMicroseconds": np.clip(duration, 100, 25000),
            "CpuTimeMicroseconds": np.clip(cpu_time, 0, 5000),
            "LogicalReads": np.clip(reads, 0, 500),
            "Writes": writes,
            "RowCount": rows,
            "SqlTextLength": text_len,
            "HourOfDay": hour,
            "DayOfWeek": dow,
            "IsWeekend": is_weekend,
            "IsNightHours": is_night,
            "IsError": is_err.astype(float),
            "ErrorNumber": err_num,
            "IsSelect": is_select,
            "IsInsert": is_insert,
            "IsUpdate": is_update,
            "IsDelete": is_delete,
            "IsOtherOp": is_other_op,
            "IsCustomer": is_cust,
            "IsProduct": is_prod,
            "IsSalesOrderHeader": is_soh,
            "IsOtherTable": is_other_tbl,
            "IsOfficePc": is_office,
            "IsHomePc": is_home,
            "IsMobileLaptop": is_mob,
            "IsRogueDevice": is_rogue,
            "IsAnomaly": is_anomaly
        })

        if "anomaly_class" in df.columns:
            vec_df["AnomalyClass"] = df["anomaly_class"].astype(str)
        return vec_df