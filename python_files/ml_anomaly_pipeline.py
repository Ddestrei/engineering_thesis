"""
Główny skrypt uruchamiający pełen eksperyment uczenia maszynowego, weryfikację modeli na zbiorze testowym oraz systematyczne Badanie Ablacyjne (Ablation Study) grup cech.
"""

from __future__ import annotations
from pathlib import Path
from typing import Dict, List
import pandas as pd
from AnomalyDataLoader import AnomalyDataLoader, FEATURE_NAMES
from AnomalyDetectionPipeline import AnomalyDetectionPipeline


FEATURE_GROUPS: Dict[str, List[str]] = {
    "temporal": ["HourOfDay", "DayOfWeek", "IsWeekend", "IsNightHours"],
    "devices": ["IsOfficePc", "IsHomePc", "IsMobileLaptop", "IsRogueDevice"],
    "resources": ["IsCustomer", "IsProduct", "IsSalesOrderHeader", "IsOtherTable"],
    "execution_metrics": ["DurationMicroseconds", "CpuTimeMicroseconds", "LogicalReads", "Writes", "RowCount", "SqlTextLength"],
    "errors": ["IsError", "ErrorNumber"],
    "operations": ["IsSelect", "IsInsert", "IsUpdate", "IsDelete", "IsOtherOp"]
}


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


def main():
    """
    Orkiestrator wykonawczy: wczytuje dane, trenuje modele bazowe,
    generuje raport testowy oraz uruchamia pełne badanie ablacyjne.
    """
    print("=" * 80)
    print("CYBERSECURITY ML PIPELINE - DETEKCJA ANOMALII W BAZIE SQL SERVER")
    print("=" * 80)

    base_dirs = [Path("generated_data"), Path(".")]
    train_path, val_path, test_path = None, None, None

    for d in base_dirs:
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

    loader = AnomalyDataLoader()
    df_train = loader.load_split(train_path, seed=42)
    df_val = loader.load_split(val_path, seed=123)
    df_test = loader.load_split(test_path, seed=7777)

    print(f"\n[+] Rozmiary danych:")
    print(f"    - Train: {df_train.shape[0]} próbek (Anomalie: {df_train['IsAnomaly'].sum()}) [Czysta norma]")
    print(f"    - Val:   {df_val.shape[0]} próbek (Anomalie: {df_val['IsAnomaly'].sum()} = {100*df_val['IsAnomaly'].mean():.1f}%)")
    print(f"    - Test:  {df_test.shape[0]} próbek (Anomalie: {df_test['IsAnomaly'].sum()} = {100*df_test['IsAnomaly'].mean():.1f}%)")

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

    out_dir = Path("results_ml")
    out_dir.mkdir(parents=True, exist_ok=True)
    baseline_results.to_csv(out_dir / "baseline_test_results.csv", index=False)
    ablation_results.to_csv(out_dir / "ablation_study_results.csv", index=False)
    print(f"\n[OK] Wyniki eksperymentów zapisano do folderu: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
