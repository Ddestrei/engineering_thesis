"""
=============================================================================
Projekt Badawczy: Detekcja Anomalii Behawioralnych w Bazie Danych SQL Server
Moduł: Pipeline Trenowania, Strojenia Progów i Ewaluacji Modeli ML
Rola: Lead Data Scientist & Cybersecurity ML Architect
=============================================================================
Architektura Modułu:
1. Reżim Uczenia Półnadzorowanego (Semi-Supervised / Novelty Detection):
   - X_train: Wyłącznie 100% normalnego ruchu (ceo_train.csv, 0 anomalii).
   - X_val: Norma + kontrolowane anomalie (ceo_validation.csv) do strojenia progów.
   - X_test: Niezależne ziarno (seed) + perturbacja parametrów (ceo_test.csv).

2. Zaimplementowane Modele (5 Detektorów):
   a) Baseline Regułowy (Rule-Based Expert Heuristic)
   b) Statystyka Odporna (Robust Z-Score z Medianą i MAD - Iglewicz & Hoaglin)
   c) Isolation Forest (Liu, Ting, Zhou)
   d) One-Class SVM (Schölkopf, Platt, Shawe-Taylor)
   e) Local Outlier Factor w trybie Novelty Detection (Breunig, Kriegel, Ng, Sander)

3. Preprocessing i Ochrona Przed Wyciekiem Danych (No Data Leakage):
   - Skalowanie RobustScaler dopasowywane (fit) WYŁĄCZNIE na X_train.
   - Cechy binarne / One-Hot przekazywane bez skalowania (passthrough).

4. Strojenie Progów (Threshold Tuning):
   - Optymalizacja progu tau* na zbiorze walidacyjnym pod kątem MCC
     (Matthews Correlation Coefficient) lub z twardym ograniczeniem na FPR <= 5%.

5. Badanie Ablacyjne (Ablation Study):
   - Systematyczne usuwanie grup cech (czasowe, urządzenia, zasoby, metryki wykonania)
     w celu empirycznego wykazania wagi informacyjnej poszczególnych atrybutów.
=============================================================================
"""

from __future__ import annotations

import os
import json
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import RobustScaler
from sklearn.svm import OneClassSVM
from sklearn.metrics import (
    precision_score,
    recall_score,
    matthews_corrcoef,
    average_precision_score,
    confusion_matrix,
    roc_auc_score
)

# Wyciszenie ostrzeżeń bibliotecznych dla czytelności raportów
warnings.filterwarnings("ignore")

# =============================================================================
# DEFINICJA STRUKTURY I GRUP CECH WEKTORA 25-WYMIAROWEGO
# =============================================================================

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

CONTINUOUS_FEATURES: List[str] = [
    "DurationMicroseconds",
    "CpuTimeMicroseconds",
    "LogicalReads",
    "Writes",
    "RowCount",
    "SqlTextLength",
    "HourOfDay",
    "DayOfWeek"
]

FEATURE_GROUPS: Dict[str, List[str]] = {
    "temporal": ["HourOfDay", "DayOfWeek", "IsWeekend", "IsNightHours"],
    "devices": ["IsOfficePc", "IsHomePc", "IsMobileLaptop", "IsRogueDevice"],
    "resources": ["IsCustomer", "IsProduct", "IsSalesOrderHeader", "IsOtherTable"],
    "execution_metrics": ["DurationMicroseconds", "CpuTimeMicroseconds", "LogicalReads", "Writes", "RowCount", "SqlTextLength"],
    "errors": ["IsError", "ErrorNumber"],
    "operations": ["IsSelect", "IsInsert", "IsUpdate", "IsDelete", "IsOtherOp"]
}


# =============================================================================
# 1. MODUŁ PREPROCESSINGU I WCZYTYWANIA DANYCH
# =============================================================================

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

        # Sprawdzenie czy plik zawiera już 25 gotowych kolumn cech
        if all(feat in df_raw.columns for feat in FEATURE_NAMES) and "IsAnomaly" in df_raw.columns:
            return df_raw.copy()

        # Jeśli plik zawiera kolumny surowe generatora, wykonujemy bezpieczną wektoryzację
        return AnomalyDataLoader.vectorize_raw_dataframe(df_raw, seed=seed)

    @staticmethod
    def vectorize_raw_dataframe(df_raw: pd.DataFrame, seed: int = 42) -> pd.DataFrame:
        """
        Wektoryzuje surowy zbiór z generatora do 25 numerycznych cech.
        Zachowuje pełny determinizm (seed) oraz zgodność z metrykami SQL Server.
        """
        rng = np.random.default_rng(seed)
        df = df_raw.copy()

        ts = pd.to_datetime(df["timestamp"])
        hour = ts.dt.hour
        dow = ts.dt.dayofweek

        # Etykieta celu
        is_anomaly = df["is_anomaly"].astype(int) if "is_anomaly" in df.columns else df.get("IsAnomaly", 0).astype(int)

        # Cechy czasowe
        is_weekend = dow.isin([5, 6]).astype(float)
        # Noc: przed 06:00 lub po 22:00 (zgodnie z profilem CEO i XeLogReader)
        is_night = ((hour < 6) | (hour >= 22)).astype(float)

        # Operacje
        op = df["operation_type"].astype(str).str.upper()
        is_select = (op == "SELECT").astype(float)
        is_insert = (op == "INSERT").astype(float)
        is_update = (op == "UPDATE").astype(float)
        is_delete = (op == "DELETE").astype(float)
        is_other_op = (~(op.isin(["SELECT", "INSERT", "UPDATE", "DELETE"]))).astype(float)

        # Tabele
        tbl = df["target_table"].astype(str)
        is_cust = (tbl.str.lower() == "customer").astype(float)
        is_prod = (tbl.str.lower() == "product").astype(float)
        is_soh = (tbl.str.lower() == "salesorderheader").astype(float)
        is_other_tbl = (~(tbl.str.lower().isin(["customer", "product", "salesorderheader"]))).astype(float)

        # Urządzenia
        dev = df["device_context"].astype(str)
        is_office = (dev == "Office_PC").astype(float)
        is_home = (dev == "Home_PC").astype(float)
        is_mob = (dev == "Mobile_Laptop").astype(float)
        is_rogue = (~(dev.isin(["Office_PC", "Home_PC", "Mobile_Laptop"]))).astype(float)

        # Błędy
        is_err = df["severity_level"].fillna(0).astype(float) >= 11 if "severity_level" in df.columns else np.zeros(len(df))
        err_num = np.where(is_err, 50000, 0)

        # Metryki wykonania z bazy danych zgodne z rozkładami empirycznymi AdventureWorks
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
                else:  # SalesOrderHeader
                    reads.append(int(rng.integers(24, 45)))
                    rows.append(int(rng.integers(5, 15)))
                    duration.append(float(rng.normal(2600, 500)))
                    text_len.append(255)
            else:  # INSERT
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


# =============================================================================
# 2. DEFINICJE MODELI (5 METOD ANOMALY DETECTION)
# =============================================================================

class RuleBasedDetector(BaseEstimator, ClassifierMixin):
    """
    1. Baseline Regułowy (Rule-Based Expert Heuristic).
    Generuje alarm na podstawie twardych naruszeń polityki bezpieczeństwa:
    - Logowanie w godzinach nocnych (IsNightHours == 1)
    - Praca w weekend (IsWeekend == 1)
    - Nieznane urządzenie (IsRogueDevice == 1)
    - Dostęp do nieautoryzowanej tabeli (IsOtherTable == 1)
    - Wystąpienie błędu uprawnień/składni (IsError == 1)
    - Niedozwolone operacje modyfikacji/usuwania (IsUpdate == 1 lub IsDelete == 1)
    """
    def __init__(self, rules: Optional[List[str]] = None):
        self.rules = rules or [
            "IsNightHours", "IsWeekend", "IsRogueDevice",
            "IsOtherTable", "IsError", "IsUpdate", "IsDelete"
        ]
        self.threshold_: float = 0.5

    def fit(self, X: Union[pd.DataFrame, np.ndarray], y=None):
        return self

    def decision_function(self, X: Union[pd.DataFrame, np.ndarray]) -> np.ndarray:
        if isinstance(X, pd.DataFrame):
            active_rules = [r for r in self.rules if r in X.columns]
            if not active_rules:
                return np.zeros(len(X))
            scores = (X[active_rules] > 0).sum(axis=1).values.astype(float)
        else:
            # Tablica numpy - sumowanie naruszeń
            scores = np.sum(X > 0, axis=1).astype(float)
        return scores

    def predict(self, X: Union[pd.DataFrame, np.ndarray]) -> np.ndarray:
        scores = self.decision_function(X)
        return (scores >= self.threshold_).astype(int)


class RobustZScoreDetector(BaseEstimator, ClassifierMixin):
    """
    2. Statystyka Odporna (Robust Statistics / Modified Z-Score - Iglewicz & Hoaglin 1993).
    Wykorzystuje medianę oraz MAD (Median Absolute Deviation) do estymacji odchylenia:
        M_i = 0.6745 * |X_i - Median| / MAD
    Odporny na asymetrię rozkładu i obecność wartości skrajnych w logach bazy danych.
    """
    def __init__(self, continuous_cols: Optional[List[str]] = None):
        self.continuous_cols = continuous_cols or CONTINUOUS_FEATURES
        self.medians_: Dict[str, float] = {}
        self.mads_: Dict[str, float] = {}
        self.threshold_: float = 2.5

    def fit(self, X: Union[pd.DataFrame, np.ndarray], y=None):
        self.medians_.clear()
        self.mads_.clear()
        if isinstance(X, pd.DataFrame):
            for col in self.continuous_cols:
                if col in X.columns:
                    med = float(np.median(X[col]))
                    mad = float(np.median(np.abs(X[col] - med)))
                    self.medians_[col] = med
                    self.mads_[col] = max(mad, 1e-4)
        else:
            for i in range(X.shape[1]):
                med = float(np.median(X[:, i]))
                mad = float(np.median(np.abs(X[:, i] - med)))
                self.medians_[str(i)] = med
                self.mads_[str(i)] = max(mad, 1e-4)
        return self

    def decision_function(self, X: Union[pd.DataFrame, np.ndarray]) -> np.ndarray:
        z_scores = []
        if isinstance(X, pd.DataFrame):
            for col, med in self.medians_.items():
                if col in X.columns:
                    mad = self.mads_[col]
                    z = 0.6745 * np.abs(X[col].values - med) / mad
                    z_scores.append(z)
        else:
            for i_str, med in self.medians_.items():
                i = int(i_str)
                if i < X.shape[1]:
                    mad = self.mads_[i_str]
                    z = 0.6745 * np.abs(X[:, i] - med) / mad
                    z_scores.append(z)

        if not z_scores:
            return np.zeros(len(X))
        z_matrix = np.column_stack(z_scores)
        # Zwracamy maksymalny modified Z-score spośród cech ilościowych
        return np.max(z_matrix, axis=1)

    def predict(self, X: Union[pd.DataFrame, np.ndarray]) -> np.ndarray:
        scores = self.decision_function(X)
        return (scores >= self.threshold_).astype(int)


# =============================================================================
# 3. MODUŁ STROJENIA PROGÓW (THRESHOLD TUNING)
# =============================================================================

class ThresholdTuner:
    """
    Kryterium doboru optymalnego progu odcięcia (tau*) na zbiorze walidacyjnym.
    """
    @staticmethod
    def tune(
        y_val: np.ndarray,
        anomaly_scores: np.ndarray,
        mode: str = "mcc",
        max_fpr: float = 0.05,
        n_quantiles: int = 1000
    ) -> Tuple[float, Dict[str, float]]:
        """
        Przeszukuje przestrzeń score'ów w poszukiwaniu progu tau*, który:
        - Dla mode='mcc': maksymalizuje Matthews Correlation Coefficient.
        - Dla mode='fpr_constrained': gwarantuje FPR <= max_fpr (np. 5%),
          a w ramach tego ograniczenia maksymalizuje MCC.
        """
        thresholds = np.unique(anomaly_scores)
        if len(thresholds) > n_quantiles:
            thresholds = np.percentile(anomaly_scores, np.linspace(0, 100, n_quantiles))

        best_thresh = float(thresholds[0])
        best_mcc = -2.0
        best_metrics: Dict[str, float] = {}

        for th in thresholds:
            y_pred = (anomaly_scores >= th).astype(int)
            tn, fp, fn, tp = confusion_matrix(y_val, y_pred, labels=[0, 1]).ravel()
            fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
            mcc = matthews_corrcoef(y_val, y_pred)

            if mode == "fpr_constrained" and fpr > max_fpr:
                continue

            if mcc > best_mcc:
                best_mcc = mcc
                best_thresh = float(th)
                prec = precision_score(y_val, y_pred, zero_division=0)
                rec = recall_score(y_val, y_pred, zero_division=0)
                f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0
                best_metrics = {
                    "threshold": float(th),
                    "mcc": float(mcc),
                    "fpr": float(fpr),
                    "precision": float(prec),
                    "recall": float(rec),
                    "f1": float(f1)
                }

        # Fallback jeśli warunek FPR odciął wszystkie progi
        if not best_metrics:
            best_thresh = float(np.median(thresholds))
            y_pred = (anomaly_scores >= best_thresh).astype(int)
            tn, fp, fn, tp = confusion_matrix(y_val, y_pred, labels=[0, 1]).ravel()
            fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
            best_metrics = {
                "threshold": best_thresh,
                "mcc": float(matthews_corrcoef(y_val, y_pred)),
                "fpr": float(fpr),
                "precision": float(precision_score(y_val, y_pred, zero_division=0)),
                "recall": float(recall_score(y_val, y_pred, zero_division=0)),
                "f1": 0.0
            }

        return best_thresh, best_metrics


# =============================================================================
# 4. GŁÓWNY PIPELINE UCZENIA I EWALUACJI
# =============================================================================

class AnomalyDetectionPipeline:
    """
    Zintegrowany zarządca procesu uczenia, strojenia progów i testowania modeli.
    """
    def __init__(self, random_state: int = 42):
        self.random_state = random_state
        self.preprocessor: Optional[ColumnTransformer] = None
        self.models: Dict[str, BaseEstimator] = {}
        self.tuned_thresholds: Dict[str, float] = {}
        self.val_metrics: Dict[str, Dict[str, float]] = {}

    def _init_models(self, active_continuous: List[str]) -> Dict[str, BaseEstimator]:
        return {
            "Rule_Baseline": RuleBasedDetector(),
            "Robust_ZScore": RobustZScoreDetector(continuous_cols=active_continuous),
            "Isolation_Forest": IsolationForest(
                n_estimators=200,
                max_samples="auto",
                contamination="auto",
                random_state=self.random_state
            ),
            "OneClass_SVM": OneClassSVM(
                kernel="rbf",
                gamma="scale",
                nu=0.05
            ),
            "Local_Outlier_Factor": LocalOutlierFactor(
                n_neighbors=20,
                novelty=True,
                contamination="auto"
            )
        }

    def train_and_tune(
        self,
        df_train: pd.DataFrame,
        df_val: pd.DataFrame,
        features: Optional[List[str]] = None,
        tune_mode: str = "mcc",
        max_fpr: float = 0.05
    ):
        """
        KROK 1 & 2: Trening modeli na X_train i dobór progów na X_val.
        Gwarancja: Scaler dopasowywany jest WYŁĄCZNIE na danych treningowych.
        """
        active_features = features or FEATURE_NAMES
        cont_cols = [c for c in CONTINUOUS_FEATURES if c in active_features]
        bin_cols = [c for c in active_features if c not in cont_cols]

        X_train = df_train[active_features]
        X_val = df_val[active_features]
        y_val = df_val["IsAnomaly"].values

        # Budowa dedykowanego transformera kolumn
        transformers = []
        if cont_cols:
            transformers.append(("scaler", RobustScaler(), cont_cols))
        if bin_cols:
            transformers.append(("passthrough", "passthrough", bin_cols))

        self.preprocessor = ColumnTransformer(transformers=transformers, remainder="drop")
        X_train_proc = self.preprocessor.fit_transform(X_train)
        X_val_proc = self.preprocessor.transform(X_val)

        self.models = self._init_models(cont_cols)
        self.tuned_thresholds.clear()
        self.val_metrics.clear()

        for name, model in self.models.items():
            # Modele statystyczno-regułowe otrzymują czysty DataFrame, a modele ML dane przeskalowane
            if name in ["Rule_Baseline", "Robust_ZScore"]:
                model.fit(X_train)
                val_scores = model.decision_function(X_val)
            else:
                model.fit(X_train_proc)
                # W scikit-learn decision_function jest ujemna dla anomalii, odwracamy znak
                val_scores = -model.decision_function(X_val_proc)

            th, v_met = ThresholdTuner.tune(
                y_val=y_val,
                anomaly_scores=val_scores,
                mode=tune_mode,
                max_fpr=max_fpr
            )
            self.tuned_thresholds[name] = th
            self.val_metrics[name] = v_met

    def evaluate_test(
        self,
        df_test: pd.DataFrame,
        features: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """
        KROK 3: Ostateczna ewaluacja na zbiorze testowym ceo_test.csv.
        """
        active_features = features or FEATURE_NAMES
        X_test = df_test[active_features]
        y_test = df_test["IsAnomaly"].values

        X_test_proc = self.preprocessor.transform(X_test) if self.preprocessor else X_test

        results = []
        for name, model in self.models.items():
            if name in ["Rule_Baseline", "Robust_ZScore"]:
                test_scores = model.decision_function(X_test)
            else:
                test_scores = -model.decision_function(X_test_proc)

            th = self.tuned_thresholds[name]
            y_pred = (test_scores >= th).astype(int)

            tn, fp, fn, tp = confusion_matrix(y_test, y_pred, labels=[0, 1]).ravel()
            fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0

            results.append({
                "Model": name,
                "Tuned_Threshold": round(th, 4),
                "Val_MCC": round(self.val_metrics[name]["mcc"], 4),
                "Val_FPR": round(self.val_metrics[name]["fpr"], 4),
                "Test_Precision": round(precision_score(y_test, y_pred, zero_division=0), 4),
                "Test_Recall": round(recall_score(y_test, y_pred, zero_division=0), 4),
                "Test_PR_AUC": round(average_precision_score(y_test, test_scores), 4),
                "Test_MCC": round(matthews_corrcoef(y_test, y_pred), 4),
                "Test_FPR": round(fpr, 4),
                "TP": tp,
                "FP": fp,
                "TN": tn,
                "FN": fn
            })

        return pd.DataFrame(results)


# =============================================================================
# 5. BADANIE ABLACYJNE (ABLATION STUDY)
# =============================================================================

def run_ablation_study(
    df_train: pd.DataFrame,
    df_val: pd.DataFrame,
    df_test: pd.DataFrame,
    tune_mode: str = "mcc",
    max_fpr: float = 0.05
) -> pd.DataFrame:
    """
    Przeprowadza systematyczne badanie ablacyjne w celu identyfikacji
    wpływu poszczególnych grup cech na skuteczność detekcji anomalii.
    """
    ablation_experiments = {
        "Full_Features (25)": FEATURE_NAMES,
        "w/o_Temporal": [c for c in FEATURE_NAMES if c not in FEATURE_GROUPS["temporal"]],
        "w/o_Devices": [c for c in FEATURE_NAMES if c not in FEATURE_GROUPS["devices"]],
        "w/o_Resources": [c for c in FEATURE_NAMES if c not in FEATURE_GROUPS["resources"]],
        "w/o_ExecutionMetrics": [c for c in FEATURE_NAMES if c not in FEATURE_GROUPS["execution_metrics"]],
        "w/o_ErrorFlags": [c for c in FEATURE_NAMES if c not in FEATURE_GROUPS["errors"]]
    }

    all_frames = []
    print("\n" + "=" * 80)
    print("ROZPOCZĘCIE BADANIA ABLACYJNEGO (ABLATION STUDY)")
    print("=" * 80)

    pipeline = AnomalyDetectionPipeline(random_state=42)

    for exp_name, feat_subset in ablation_experiments.items():
        print(f"[*] Uruchamianie konfiguracji: {exp_name:<25} | Liczba cech: {len(feat_subset)}")
        pipeline.train_and_tune(
            df_train=df_train,
            df_val=df_val,
            features=feat_subset,
            tune_mode=tune_mode,
            max_fpr=max_fpr
        )
        res_df = pipeline.evaluate_test(df_test=df_test, features=feat_subset)
        res_df.insert(0, "Experiment", exp_name)
        res_df.insert(2, "Feature_Count", len(feat_subset))
        all_frames.append(res_df)

    final_df = pd.concat(all_frames, ignore_index=True)
    return final_df


# =============================================================================
# 6. PUNKT WEJŚCIA I EGZEKUCJA GŁÓWNA
# =============================================================================

def main():
    """
    Orkiestrator wykonawczy: wczytuje dane, trenuje modele bazowe,
    generuje raport testowy oraz uruchamia pełne badanie ablacyjne.
    """
    print("=" * 80)
    print("CYBERSECURITY ML PIPELINE - DETEKCJA ANOMALII W BAZIE SQL SERVER")
    print("=" * 80)

    # Ścieżki do danych (wspiera zarówno katalog generated_data jak i katalog główny)
    base_dirs = [Path("generated_data"), Path(".")]
    train_path, val_path, test_path = None, None, None

    for d in base_dirs:
        # Preferencja dla plików zwektoryzowanych, następnie standardowych
        candidate_train = d / "vectorized_ceo_train.csv" if (d / "vectorized_ceo_train.csv").exists() else d / "ceo_train.csv"
        candidate_val = d / "vectorized_ceo_validation.csv" if (d / "vectorized_ceo_validation.csv").exists() else d / "ceo_validation.csv"
        candidate_test = d / "vectorized_ceo_test.csv" if (d / "vectorized_ceo_test.csv").exists() else d / "ceo_test.csv"

        if candidate_train.exists() and candidate_val.exists() and candidate_test.exists():
            train_path, val_path, test_path = candidate_train, candidate_val, candidate_test
            break

    if not train_path:
        raise FileNotFoundError("Nie odnaleziono zbiorów ceo_train.csv, ceo_validation.csv, ceo_test.csv.")

    print(f"[+] Zbiór Treningowy:  {train_path}")
    print(f"[+] Zbiór Walidacyjny: {val_path}")
    print(f"[+] Zbiór Testowy:     {test_path}")

    # Wczytanie zbiorów
    loader = AnomalyDataLoader()
    df_train = loader.load_split(train_path, seed=42)
    df_val = loader.load_split(val_path, seed=123)
    df_test = loader.load_split(test_path, seed=7777)

    print(f"\n[+] Rozmiary danych:")
    print(f"    - Train: {df_train.shape[0]} próbek (Anomalie: {df_train['IsAnomaly'].sum()}) [Czysta norma]")
    print(f"    - Val:   {df_val.shape[0]} próbek (Anomalie: {df_val['IsAnomaly'].sum()} = {100*df_val['IsAnomaly'].mean():.1f}%)")
    print(f"    - Test:  {df_test.shape[0]} próbek (Anomalie: {df_test['IsAnomaly'].sum()} = {100*df_test['IsAnomaly'].mean():.1f}%)")

    # 1. Główny eksperyment (Full Baseline)
    print("\n" + "=" * 80)
    print("ETAP 1: TRENING I TESTY MODELI BAZOWYCH (FULL 25 FEATURES)")
    print("=" * 80)

    pipeline = AnomalyDetectionPipeline(random_state=42)
    pipeline.train_and_tune(
        df_train=df_train,
        df_val=df_val,
        features=FEATURE_NAMES,
        tune_mode="mcc",
        max_fpr=0.05
    )

    baseline_results = pipeline.evaluate_test(df_test=df_test, features=FEATURE_NAMES)
    print("\n--- WYNIKI EWALUACJI NA ZBIORZE TESTOWYM (ceo_test.csv) ---")
    display_cols = ["Model", "Tuned_Threshold", "Test_Precision", "Test_Recall", "Test_PR_AUC", "Test_MCC", "Test_FPR"]
    print(baseline_results[display_cols].to_string(index=False))

    # 2. Badanie ablacyjne (Ablation Study)
    ablation_results = run_ablation_study(
        df_train=df_train,
        df_val=df_val,
        df_test=df_test,
        tune_mode="mcc",
        max_fpr=0.05
    )

    print("\n" + "=" * 80)
    print("PODSUMOWANIE BADANIA ABLACYJNEGO (WPŁYW GRUP CECH NA TEST_MCC I PR_AUC)")
    print("=" * 80)
    abl_summary = ablation_results[["Experiment", "Model", "Feature_Count", "Test_MCC", "Test_PR_AUC", "Test_FPR"]]
    print(abl_summary.to_string(index=False))

    # Zapis wyników do CSV dla potrzeb pracy dyplomowej
    out_dir = Path("results_ml")
    out_dir.mkdir(parents=True, exist_ok=True)
    baseline_results.to_csv(out_dir / "baseline_test_results.csv", index=False)
    ablation_results.to_csv(out_dir / "ablation_study_results.csv", index=False)
    print(f"\n[OK] Wyniki eksperymentów zapisano do folderu: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
