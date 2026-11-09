from typing import Dict, Optional, List
from sklearn.metrics import (
    precision_score,
    recall_score,
    matthews_corrcoef,
    confusion_matrix,
    average_precision_score
)
import pandas as pd
from sklearn.base import BaseEstimator
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import RobustScaler
from sklearn.svm import OneClassSVM
from RobustZScoreDetector import RobustZScoreDetector, CONTINUOUS_FEATURES
from RuleBasedDetector import RuleBasedDetector
from ThresholdTuner import ThresholdTuner
from AnomalyDataLoader import FEATURE_NAMES

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
        Trening modeli na X_train i dobór progów na X_val.
        Scaler dopasowywany jest wyłącznie na danych treningowych.
        """
        active_features = features or FEATURE_NAMES
        cont_cols = [c for c in CONTINUOUS_FEATURES if c in active_features]
        bin_cols = [c for c in active_features if c not in cont_cols]

        X_train = df_train[active_features]
        X_val = df_val[active_features]
        y_val = df_val["IsAnomaly"].values

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
            if name in ["Rule_Baseline", "Robust_ZScore"]:
                model.fit(X_train)
                val_scores = model.decision_function(X_val)
            else:
                model.fit(X_train_proc)
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
        Ostateczna ewaluacja na zbiorze testowym ceo_test.csv.
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