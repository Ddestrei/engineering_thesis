"""
Plik ten odpowiada za generowanie operacji CEO. 
Został on stworzony na podstawie opisu behawioralnego użytkownika CEO
"""

from __future__ import annotations

from datetime import datetime, timedelta, date

from CEOLogGenerator import CEOLogGenerator

import pandas as pd


def main():
    """
    Generuje trzy zbiory (Train, Val, Test) i zapisuje je do plików CSV
    w katalogu wyjściowym.
    """
    import os

    OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "generated_data")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=" * 70)
    print("  CEO Log Generator – Stochastic Synthetic Data for ML")
    print("=" * 70)

    gen = CEOLogGenerator(
        train_seed=42,
        val_seed=123,
        test_seed=7777,
        start_date=date(2026, 1, 1),
    )

    print("\n[1/3] Generowanie zbioru treningowego (Train)...")
    df_train = gen.generate_train()
    train_path = os.path.join(OUTPUT_DIR, "ceo_train.csv")
    df_train.to_csv(train_path, index=False)
    print(f"  -> Zapisano {len(df_train)} rekordów -> {train_path}")
    print(f"  -> Anomalie: {df_train['is_anomaly'].sum()} (oczekiwane: 0)")
    _print_summary(df_train, "Train")

    print("\n[2/3] Generowanie zbioru walidacyjnego (Validation)...")
    df_val = gen.generate_val()
    val_path = os.path.join(OUTPUT_DIR, "ceo_validation.csv")
    df_val.to_csv(val_path, index=False)
    n_anom = df_val["is_anomaly"].sum()
    print(f"  -> Zapisano {len(df_val)} rekordów -> {val_path}")
    print(f"  -> Anomalie: {n_anom} ({100*n_anom/len(df_val):.1f}%)")
    _print_summary(df_val, "Validation")

    print("\n[3/3] Generowanie zbioru testowego (Test)...")
    df_test = gen.generate_test()
    test_path = os.path.join(OUTPUT_DIR, "ceo_test.csv")
    df_test.to_csv(test_path, index=False)
    n_anom = df_test["is_anomaly"].sum()
    print(f"  -> Zapisano {len(df_test)} rekordów -> {test_path}")
    print(f"  -> Anomalie: {n_anom} ({100*n_anom/len(df_test):.1f}%)")
    _print_summary(df_test, "Test")

    print("\n" + "=" * 70)
    print("  Generowanie zakończone pomyślnie.")
    print("=" * 70)


def _print_summary(df: pd.DataFrame, name: str):
    """Drukuje podstawowe statystyki zbioru."""
    print(f"\n  --- {name} Summary ---")
    print(f"  Zakres dat:       {df['timestamp'].min()} -> {df['timestamp'].max()}")
    print(f"  Operacje (SELECT/INSERT):")
    for op, cnt in df["operation_type"].value_counts().items():
        print(f"    {op}: {cnt} ({100*cnt/len(df):.1f}%)")
    print(f"  Tabele:")
    for tbl, cnt in df["target_table"].value_counts().items():
        print(f"    {tbl}: {cnt} ({100*cnt/len(df):.1f}%)")
    print(f"  Urządzenia:")
    for dev, cnt in df["device_context"].value_counts().items():
        print(f"    {dev}: {cnt} ({100*cnt/len(df):.1f}%)")
    if df["is_anomaly"].sum() > 0:
        print(f"  Klasy anomalii:")
        for cls, cnt in df.loc[df["is_anomaly"] == 1, "anomaly_class"] \
                .value_counts().items():
            print(f"    {cls}: {cnt}")


if __name__ == "__main__":
    main()
