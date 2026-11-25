from sklearn.base import BaseEstimator, ClassifierMixin
from typing import List, Optional, Union, Dict
import pandas as pd
import numpy as np

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

class RobustZScoreDetector(BaseEstimator, ClassifierMixin):
    """
    Statystyka Odporna Robust Statistics / Modified Z-Score - Iglewicz & Hoaglin 1993.
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
        return np.max(z_matrix, axis=1)

    def predict(self, X: Union[pd.DataFrame, np.ndarray]) -> np.ndarray:
        scores = self.decision_function(X)
        return (scores >= self.threshold_).astype(int)