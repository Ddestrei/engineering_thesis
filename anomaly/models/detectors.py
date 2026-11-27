"""
Faza 6: detektory na oknach UserId × 30 min. Wspólny interfejs:

    detector.fit(X_train, features) -> detector     X: macierz okien TRAIN jednostki modelowania
    detector.score(X) -> np.ndarray                  większa wartość = większe ryzyko (D8)

Detektor widzi wyłącznie macierz cech (kolumny z windowing.feature_columns); etykiety nie
przechodzą przez fit ani score. Jeden obiekt = jedna jednostka modelowania (populacja, rola
albo użytkownik, D17); orkiestracja wariantów jest w anomaly/models/training.py.

- RuleBasedDetector:     D13, 4 reguły z percentyli TRAIN, score = liczba naruszonych reguł
- RobustZScoreDetector:  D12, mediana + skala z kaskadą zastępczą, score = średnia 3 największych |z|
- IsolationForestDetector, OneClassSVMDetector, LOFDetector: sklearn, skalowanie dopasowane na TRAIN,
  score = −decision_function
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM

from anomaly.domain import OPERATIONS
from anomaly.features.windowing import BINARY_FEATURES

RATIO_FEATURES = [f"{op.lower()}_ratio" for op in OPERATIONS]
NUMERIC_TOL = 1e-9          # skala poniżej tej wartości (względnie do mediany) to szum zmiennoprzecinkowy = 0


class Detector:
    name = "base"

    def fit(self, X: np.ndarray, features: Sequence[str]) -> "Detector":
        raise NotImplementedError

    def score(self, X: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def params(self) -> dict:
        """Parametry dopasowania zapisywane do zamrożonej konfiguracji (tylko liczby i listy)."""
        return {}


# --- D13: reguły --------------------------------------------------------------------------

class RuleBasedDetector(Detector):
    """
    Progi z percentyli okien TRAIN jednostki (nie z parametrów generatora):
      1. operation_count > P99
      2. hour_rarity > P99
      3. rare_resource_score > P99
      4. dowolny z udziałów select/insert/update/delete_ratio poza [P1, P99]  (jedna reguła)
    Score = liczba naruszonych reguł (0–4). Reguły, których cech nie ma w zestawie (ablacja), są pomijane.
    """
    name = "rules"
    UPPER = ("operation_count", "hour_rarity", "rare_resource_score")

    def fit(self, X, features):
        self.features_ = list(features)
        col = {f: i for i, f in enumerate(self.features_)}
        self.upper_: Dict[str, float] = {f: float(np.percentile(X[:, col[f]], 99)) for f in self.UPPER if f in col}
        self.ratio_bounds_: Dict[str, List[float]] = {
            f: [float(v) for v in np.percentile(X[:, col[f]], [1, 99])] for f in RATIO_FEATURES if f in col}
        self._col = col
        return self

    def violations(self, X: np.ndarray) -> np.ndarray:
        """Macierz 0/1: okno × reguła (kolejność: UPPER, potem reguła proporcji)."""
        cols = [(X[:, self._col[f]] > p99) for f, p99 in self.upper_.items()]
        if self.ratio_bounds_:
            out = np.zeros(len(X), dtype=bool)
            for f, (p1, p99) in self.ratio_bounds_.items():
                x = X[:, self._col[f]]
                out |= (x < p1) | (x > p99)
            cols.append(out)
        return np.column_stack(cols).astype(int) if cols else np.zeros((len(X), 0), dtype=int)

    def score(self, X):
        return self.violations(X).sum(axis=1).astype(float)

    def params(self):
        return {"p99": self.upper_, "ratio_p1_p99": self.ratio_bounds_}


# --- D12: robust z-score ----------------------------------------------------------------

def robust_scale(x: np.ndarray) -> tuple:
    """
    Mediana i skala według kaskady D12. Zwraca (mediana, skala, poziom kaskady).
      1,4826 · MAD  ->  IQR / 1,349  ->  max(1e-3, 0,01 · (P99 − P1))  ->  1 (cecha w TRAIN stała)
    """
    x = np.asarray(x, dtype=float)
    med = float(np.median(x))
    tol = NUMERIC_TOL * max(1.0, abs(med))
    mad = float(np.median(np.abs(x - med)))
    if mad > tol:
        return med, 1.4826 * mad, "mad"
    q1, q3 = np.percentile(x, [25, 75])
    if q3 - q1 > tol:
        return med, float(q3 - q1) / 1.349, "iqr"
    if float(np.ptp(x)) <= tol:
        return med, 1.0, "constant"
    p1, p99 = np.percentile(x, [1, 99])
    return med, max(1e-3, 0.01 * float(p99 - p1)), "range"


class RobustZScoreDetector(Detector):
    """D12: z_j = |x_j − m_j| / s_j dla cech ciągłych; score = średnia z 3 największych z_j."""
    name = "robust"
    TOP_K = 3

    def fit(self, X, features):
        self.features_ = list(features)
        self.idx_ = [i for i, f in enumerate(self.features_) if f not in BINARY_FEATURES]
        stats = [robust_scale(X[:, i]) for i in self.idx_]
        self.median_ = np.array([s[0] for s in stats])
        self.scale_ = np.array([s[1] for s in stats])
        self.levels_ = [s[2] for s in stats]
        return self

    def z(self, X: np.ndarray) -> np.ndarray:
        return np.abs(X[:, self.idx_] - self.median_) / self.scale_

    def score(self, X):
        if not self.idx_:
            return np.zeros(len(X))
        z = np.sort(self.z(X), axis=1)
        return z[:, -min(self.TOP_K, z.shape[1]):].mean(axis=1)

    def params(self):
        names = [self.features_[i] for i in self.idx_]
        return {"median": dict(zip(names, self.median_.round(6).tolist())),
                "scale": dict(zip(names, self.scale_.round(6).tolist())),
                "level": dict(zip(names, self.levels_))}


# --- modele ML -------------------------------------------------------------------------

class _SklearnDetector(Detector):
    """Skalowanie StandardScaler dopasowane na TRAIN jednostki; score = −decision_function (D8)."""

    def __init__(self, **hp):
        self.hp = hp

    def _model(self, n_train: int, n_features: int, Xs: np.ndarray):
        raise NotImplementedError

    def fit(self, X, features):
        self.features_ = list(features)
        self.scaler_ = StandardScaler().fit(X)
        Xs = self.scaler_.transform(X)
        self.model_ = self._model(len(X), X.shape[1], Xs).fit(Xs)
        return self

    def score(self, X):
        return -self.model_.decision_function(self.scaler_.transform(X))

    def params(self):
        return {"hyperparameters": self.effective_hp_}


class IsolationForestDetector(_SklearnDetector):
    name = "iforest"

    def __init__(self, n_estimators: int = 100, max_samples: int = 256, random_state: int = 0):
        super().__init__(n_estimators=n_estimators, max_samples=max_samples, random_state=random_state)

    def _model(self, n_train, n_features, Xs):
        self.effective_hp_ = {**self.hp, "max_samples": min(self.hp["max_samples"], n_train)}
        return IsolationForest(**self.effective_hp_)


class OneClassSVMDetector(_SklearnDetector):
    """gamma = gamma_mult · 'scale', gdzie 'scale' = 1 / (n_cech · var(X po skalowaniu)), jak w sklearn."""
    name = "ocsvm"

    def __init__(self, nu: float = 0.05, gamma_mult: float = 1.0):
        super().__init__(nu=nu, gamma_mult=gamma_mult)

    def _model(self, n_train, n_features, Xs):
        var = float(Xs.var())
        gamma = self.hp["gamma_mult"] / (n_features * var) if var > 0 else self.hp["gamma_mult"]
        self.effective_hp_ = {"nu": self.hp["nu"], "gamma_mult": self.hp["gamma_mult"], "gamma": gamma}
        return OneClassSVM(kernel="rbf", nu=self.hp["nu"], gamma=gamma)


class LOFDetector(_SklearnDetector):
    name = "lof"

    def __init__(self, n_neighbors: int = 20):
        super().__init__(n_neighbors=n_neighbors)

    def _model(self, n_train, n_features, Xs):
        self.effective_hp_ = {"n_neighbors": max(1, min(self.hp["n_neighbors"], n_train - 1))}
        return LocalOutlierFactor(novelty=True, **self.effective_hp_)


DETECTORS = ("rules", "robust", "iforest", "ocsvm", "lof")


def hyperparameter_grid(detector: str, seed: int) -> List[dict]:
    """Siatka hiperparametrów strojonych na VAL (Faza 6). Reguły i robust nie mają hiperparametrów."""
    if detector == "iforest":
        return [{"n_estimators": n, "max_samples": m, "random_state": seed}
                for n in (100, 300) for m in (64, 128, 256)]
    if detector == "ocsvm":
        return [{"nu": nu, "gamma_mult": g} for nu in (0.01, 0.05, 0.1) for g in (0.3, 1.0, 3.0)]
    if detector == "lof":
        return [{"n_neighbors": k} for k in (5, 10, 20, 35)]
    if detector in ("rules", "robust"):
        return [{}]
    raise ValueError(f"Nieznany detektor: {detector}")


def make_detector(detector: str, hp: Optional[dict] = None) -> Detector:
    hp = hp or {}
    return {"rules": RuleBasedDetector, "robust": RobustZScoreDetector, "iforest": IsolationForestDetector,
            "ocsvm": OneClassSVMDetector, "lof": LOFDetector}[detector](**hp)
