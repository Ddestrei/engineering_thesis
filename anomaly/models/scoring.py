"""
Faza 6: normalizacja score'u (METODOLOGIA D8) i dobór progu (D7).

Normalizacja (wygładzona ECDF z przedłużonym ogonem), dopasowana na score'ach NORMALNYCH okien VAL
jednostki modelowania:
    s <= s_max:  norm(s) = rank(s) / (n + 1),   rank(s) = liczba score'ów referencyjnych <= s
    s >  s_max:  norm(s) = 1 − (1 / (n + 1)) · exp(−(s − s_max) / σ)
σ = 1,4826 · MAD score'ów referencyjnych. Gdy MAD = 0 (np. reguły: w normie prawie zawsze 0),
kolejno IQR / 1,349, odchylenie standardowe, 1. D8 nie określa tego przypadku; bez zastępczej skali
ogon byłby nieskończenie stromy i najwyżej oceniane alerty remisowałyby na wartości 1,0.

Precyzja: dla s − s_max > ok. 33 σ wartość 1 − e^(−x)/(n+1) zaokrągla się w float64 do 1,0 (zdarza się
dla robust i LOF przy bardzo dużych odchyleniach). Kolejność z D8 odtwarza się wtedy, sortując remisy
według nadwyżki ogona x = (s − s_max) / σ, od której zależy ogon (porównywalnej między jednostkami,
w przeciwieństwie do score'u surowego): zob. ScoreNormalizer.excess() i rank_key().

Próg (D7): spośród progów o FPR <= 5% na VAL wybierany jest próg o maksymalnym MCC (przy remisie
wyższy próg). Drugi punkt pracy (H1): najwyższy próg, przy którym recall na VAL >= 0,80.
Alarm: norm_score >= próg.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np

MAX_FPR = 0.05
TARGET_RECALL = 0.80
NO_ALARM = 1.0 + 1e-9            # próg powyżej każdego score'u znormalizowanego (brak alarmów)


@dataclass
class ScoreNormalizer:
    reference: List[float] = field(default_factory=list)     # posortowane score'y normalnych okien VAL
    s_max: float = 0.0
    sigma: float = 1.0
    sigma_level: str = "mad"

    @classmethod
    def fit(cls, ref_scores: np.ndarray) -> "ScoreNormalizer":
        ref = np.sort(np.asarray(ref_scores, dtype=float))
        if len(ref) == 0:
            raise ValueError("Brak normalnych okien VAL do normalizacji score'u (D8).")
        mad = float(np.median(np.abs(ref - np.median(ref))))
        q1, q3 = np.percentile(ref, [25, 75])
        std = float(ref.std())
        for sigma, level in ((1.4826 * mad, "mad"), (float(q3 - q1) / 1.349, "iqr"), (std, "std"), (1.0, "one")):
            if sigma > 1e-12:
                break
        return cls(reference=ref.tolist(), s_max=float(ref[-1]), sigma=float(sigma), sigma_level=level)

    def transform(self, scores: np.ndarray) -> np.ndarray:
        s = np.asarray(scores, dtype=float)
        ref = np.asarray(self.reference)
        n = len(ref)
        body = np.searchsorted(ref, s, side="right") / (n + 1)
        tail = 1.0 - np.exp(-self.excess(s)) / (n + 1)           # excess >= 0: bez przepełnienia exp
        return np.where(s <= self.s_max, body, tail)

    def excess(self, scores: np.ndarray) -> np.ndarray:
        """Nadwyżka ogona (s − s_max) / σ, 0 dla s <= s_max; rozstrzyga remisy na 1,0 (rank_key)."""
        return np.maximum(0.0, (np.asarray(scores, dtype=float) - self.s_max) / self.sigma)

    def to_dict(self) -> dict:
        return {"n": len(self.reference), "s_max": self.s_max, "sigma": self.sigma,
                # Pełna precyzja: zaokrąglenie przesuwałoby rank okien leżących na granicy, więc
                # scoring z pliku (Faza 7) nie odtwarzałby alarmów VAL z Fazy 6.
                "sigma_level": self.sigma_level, "reference": list(self.reference)}


def rank_key(norm: np.ndarray, excess: np.ndarray) -> np.ndarray:
    """Indeksy okien od największego ryzyka według D8: norm, remisy (też zaokrąglone 1,0) według nadwyżki ogona."""
    return np.lexsort((-np.asarray(excess, dtype=float), -np.asarray(norm, dtype=float)))


# --- progi --------------------------------------------------------------------------

def _curve(y: np.ndarray, s: np.ndarray):
    """Progi (malejąco: unikalne score'y + NO_ALARM na początku) i liczności TP/FP dla alarmu s >= próg."""
    y = np.asarray(y, dtype=int)
    s = np.asarray(s, dtype=float)
    order = np.argsort(-s, kind="mergesort")
    s_sorted, y_sorted = s[order], y[order]
    tp_cum = np.cumsum(y_sorted)
    fp_cum = np.cumsum(1 - y_sorted)
    last = np.r_[np.nonzero(np.diff(s_sorted))[0], len(s_sorted) - 1]      # ostatni indeks każdej wartości
    thresholds = np.r_[NO_ALARM, s_sorted[last]]
    tp = np.r_[0, tp_cum[last]]
    fp = np.r_[0, fp_cum[last]]
    return thresholds, tp, fp, int(y.sum()), int(len(y) - y.sum())


def _mcc(tp, fp, fn, tn):
    num = tp * tn - fp * fn
    den = np.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    return np.divide(num, den, out=np.zeros_like(num, dtype=float), where=den > 0)


def metrics_at(y: np.ndarray, s: np.ndarray, threshold: float) -> Dict[str, float]:
    y = np.asarray(y, dtype=int)
    pred = np.asarray(s) >= threshold
    tp = int((pred & (y == 1)).sum()); fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum()); tn = int((~pred & (y == 0)).sum())
    return {"threshold": float(threshold), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "fpr": fp / (fp + tn) if fp + tn else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "mcc": float(_mcc(np.array([tp], float), np.array([fp], float),
                              np.array([fn], float), np.array([tn], float))[0])}


def select_main_threshold(y: np.ndarray, s: np.ndarray, max_fpr: float = MAX_FPR) -> Dict[str, float]:
    """D7: FPR <= max_fpr, w ramach tego maksymalne MCC; przy remisie wyższy próg."""
    thr, tp, fp, pos, neg = _curve(y, s)
    fpr = fp / neg if neg else np.zeros_like(fp, dtype=float)
    mcc = _mcc(tp.astype(float), fp.astype(float), (pos - tp).astype(float), (neg - fp).astype(float))
    ok = np.nonzero(fpr <= max_fpr + 1e-12)[0]                 # NO_ALARM (indeks 0) zawsze spełnia
    best = ok[np.argmax(mcc[ok])]                                # argmax zwraca pierwszy = najwyższy próg
    return metrics_at(y, s, thr[best])


def select_recall_threshold(y: np.ndarray, s: np.ndarray, target: float = TARGET_RECALL) -> Dict[str, float]:
    """D7, drugi punkt pracy (H1): najwyższy próg, przy którym recall na VAL >= target."""
    thr, tp, fp, pos, neg = _curve(y, s)
    if pos == 0:
        return metrics_at(y, s, NO_ALARM)
    idx = np.nonzero(tp / pos >= target - 1e-12)[0][0]
    return metrics_at(y, s, thr[idx])
