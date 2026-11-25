from typing import Dict, Tuple
import numpy as np
from sklearn.metrics import (
    precision_score,
    recall_score,
    matthews_corrcoef,
    confusion_matrix,
)

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
        Dla mode='mcc': maksymalizuje Matthews Correlation Coefficient.
        Dla mode='fpr_constrained': gwarantuje FPR <= max_fpr (np. 5%),
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