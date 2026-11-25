from sklearn.base import BaseEstimator, ClassifierMixin
from typing import List, Optional, Union
import pandas as pd
import numpy as np

class RuleBasedDetector(BaseEstimator, ClassifierMixin):
    """
    Baseline Regułowy.
    Generuje alarm na podstawie twardych naruszeń polityki bezpieczeństwa:
    Logowanie w godzinach nocnych (IsNightHours == 1)
    Praca w weekend (IsWeekend == 1)
    Nieznane urządzenie (IsRogueDevice == 1)
    Dostęp do nieautoryzowanej tabeli (IsOtherTable == 1)
    Wystąpienie błędu uprawnień/składni (IsError == 1)
    Niedozwolone operacje modyfikacji/usuwania (IsUpdate == 1 lub IsDelete == 1)
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
            scores = np.sum(X > 0, axis=1).astype(float)
        return scores

    def predict(self, X: Union[pd.DataFrame, np.ndarray]) -> np.ndarray:
        scores = self.decision_function(X)
        return (scores >= self.threshold_).astype(int)