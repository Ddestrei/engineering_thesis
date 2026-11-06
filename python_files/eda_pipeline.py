"""
=============================================================================
Projekt Badawczy: Detekcja Anomalii Behawioralnych w Bazie Danych SQL Server
Moduł: Eksploracyjna Analiza Danych (EDA) & Ekstrakcja Wektorów Cech ML
Autor: Senior Data Scientist & ML Engineer
Modele docelowe: Isolation Forest, One-Class SVM, Local Outlier Factor (LOF)
=============================================================================
"""

import os
import json
import warnings
from pathlib import Path
from typing import Union, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.preprocessing import RobustScaler, StandardScaler
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE

# Wyciszenie ostrzeżeń bibliotecznych dla czystości logów i raportów
warnings.filterwarnings("ignore")

# Globalna estetyka wykresów zgodna ze standardami publikacji naukowych
sns.set_theme(style="whitegrid", palette="tab10")
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 10,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.labelsize": 11,
    "axes.labelweight": "semibold",
    "figure.titlesize": 15,
    "figure.titleweight": "bold",
    "figure.dpi": 150
})

# Dedykowana paleta kolorystyczna dla klas: Norma vs Anomalia
PALETTE_ANOMALY = ["#2b5c8f", "#d95f02"]  # Morski granat vs Intensywny pomarańcz

# Mapowanie 25 cech wektora ML zgodnie ze specyfikacją projektu
FEATURE_NAMES: List[str] = [
    "DurationMicroseconds",   # 0: Czas trwania wykonania zapytania w mikrosekundach
    "CpuTimeMicroseconds",    # 1: Czas pracy procesora CPU w mikrosekundach
    "LogicalReads",           # 2: Liczba logicznych odczytów stron pamięci
    "Writes",                 # 3: Liczba fizycznych zapisów do bazy
    "RowCount",               # 4: Liczba zwróconych lub zmodyfikowanych wierszy
    "SqlTextLength",          # 5: Długość tekstu zapytania SQL w znakach
    "HourOfDay",              # 6: Godzina wykonania zapytania (0 - 23)
    "DayOfWeek",              # 7: Dzień tygodnia (0: Niedz, 1: Pon, ..., 6: Sob)
    "IsWeekend",              # 8: Flaga binarna czy dzień jest weekendem (Sob/Niedz)
    "IsNightHours",           # 9: Flaga binarna godzin nocnych (poza 07:00-19:00)
    "IsError",                # 10: Flaga binarna wystąpienia błędu bazy danych
    "ErrorNumber",            # 11: Kod numeryczny błędu SQL Server (0 jeśli brak)
    "IsSelect",               # 12: Flaga operacji DML SELECT
    "IsInsert",               # 13: Flaga operacji DML INSERT
    "IsUpdate",               # 14: Flaga operacji DML UPDATE
    "IsDelete",               # 15: Flaga operacji DML DELETE
    "IsOtherOp",              # 16: Flaga innej operacji (np. DDL, EXEC, SET)
    "IsCustomer",             # 17: Flaga odwołania do tabeli SalesLT.Customer
    "IsProduct",              # 18: Flaga odwołania do tabeli SalesLT.Product
    "IsSalesOrderHeader",     # 19: Flaga odwołania do tabeli SalesLT.SalesOrderHeader
    "IsOtherTable",           # 20: Flaga odwołania do innej/nieautoryzowanej tabeli
    "IsOfficePc",             # 21: Flaga urządzenia: Komputer biurowy (CEO-DESKTOP-OFFICE)
    "IsHomePc",               # 22: Flaga urządzenia: Komputer domowy (CEO-HOME-PC)
    "IsMobileLaptop",         # 23: Flaga urządzenia: Służbowy laptop (CEO-LAPTOP-MOB)
    "IsRogueDevice"           # 24: Flaga urządzenia: Obce urządzenie / Rogue Host
]

CONTINUOUS_METRICS: List[str] = [
    "DurationMicroseconds",
    "CpuTimeMicroseconds",
    "LogicalReads",
    "Writes",
    "RowCount",
    "SqlTextLength"
]


def load_dataset(file_path: Union[str, Path]) -> pd.DataFrame:
    """
    Wczytuje zbiór danych z pliku JSON (struktura zagnieżdżona z Extended Events)
    lub pliku CSV (zbiory z generatora). Spłaszcza obiekty GroundTruth i Context
    oraz mapuje 25 elementów tablicy Features na dedykowane, nazwane kolumny.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Nie odnaleziono pliku źródłowego: {path.resolve()}")

    if path.suffix.lower() == ".json":
        with open(path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        records = []
        for item in raw_data:
            gt = item.get("GroundTruth", {})
            ctx = item.get("Context", {})
            feats = item.get("Features", [])

            rec = {
                "ScenarioId": gt.get("ScenarioId", "UNKNOWN"),
                "IsAnomaly": int(gt.get("IsAnomaly", 0)),
                "AnomalyClass": str(gt.get("AnomalyClass", "None")),
                "Timestamp": ctx.get("Timestamp"),
                "Username": ctx.get("Username"),
                "HostName": ctx.get("HostName"),
                "SimulatedIp": ctx.get("SimulatedIp"),
                "DeviceContext": ctx.get("DeviceContext"),
                "OperationType": ctx.get("OperationType"),
                "TargetTable": ctx.get("TargetTable"),
                "EventName": ctx.get("EventName"),
                "SessionId": ctx.get("SessionId"),
                "IsErrorContext": bool(ctx.get("IsError", False)),
                "ErrorMessage": ctx.get("ErrorMessage", "")
            }

            # Mapowanie 25 cech wektora Features
            for idx, col_name in enumerate(FEATURE_NAMES):
                rec[col_name] = feats[idx] if idx < len(feats) else 0

            records.append(rec)

        df = pd.DataFrame(records)

    elif path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
        if "Features" in df.columns:
            feat_matrix = df["Features"].apply(
                lambda x: json.loads(x) if isinstance(x, str) else x
            ).tolist()
            feat_df = pd.DataFrame(feat_matrix, columns=FEATURE_NAMES)
            df = pd.concat([df.drop(columns=["Features"]), feat_df], axis=1)
        elif "is_anomaly" in df.columns:
            rename_map = {
                "is_anomaly": "IsAnomaly",
                "anomaly_class": "AnomalyClass",
                "scenario_id": "ScenarioId",
                "timestamp": "Timestamp",
                "user": "Username",
                "client_hostname": "HostName",
                "simulated_ip": "SimulatedIp",
                "device_context": "DeviceContext",
                "operation_type": "OperationType",
                "target_table": "TargetTable"
            }
            df = df.rename(columns=rename_map)

    # Standaryzacja etykiet czytelnych dla wykresów
    if "IsAnomaly" in df.columns:
        df["IsAnomaly"] = df["IsAnomaly"].astype(int)
        df["AnomalyLabel"] = df["IsAnomaly"].map({0: "Norma", 1: "Anomalia"})

    if "AnomalyClass" in df.columns:
        df["AnomalyClass"] = df["AnomalyClass"].fillna("None").astype(str)

    # Parsowanie daty i wzbogacenie o transformacje nieliniowe log1p
    if "Timestamp" in df.columns:
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")

    for col in CONTINUOUS_METRICS:
        if col in df.columns:
            # log1p(x) = ln(1 + x) jest odporne na wartości zerowe i kompresuje heavy-tail
            df[f"log_{col}"] = np.log1p(df[col].clip(lower=0))

    return df


def plot_univariate_continuous(df: pd.DataFrame, output_dir: Optional[Path] = None):
    """
    Krok 2.1: Rozkłady zmiennych ciągłych (KDE + Boxploty).
    Prezentuje rozkłady na skali logarytmicznej w celu uwypuklenia grubych ogonów (heavy-tail).
    """
    metrics = ["DurationMicroseconds", "LogicalReads", "RowCount", "SqlTextLength"]
    fig, axes = plt.subplots(len(metrics), 2, figsize=(14, 3.5 * len(metrics)))

    for i, metric in enumerate(metrics):
        if metric not in df.columns:
            continue

        log_metric = f"log_{metric}"

        # Histogram z estymacją gęstości jądrowej (KDE)
        ax_kde = axes[i, 0]
        sns.histplot(
            data=df,
            x=log_metric,
            hue="AnomalyLabel",
            kde=True,
            stat="density",
            common_norm=False,
            palette=PALETTE_ANOMALY,
            ax=ax_kde,
            bins=25
        )
        ax_kde.set_title(f"Rozkład log1p({metric}) [Gęstość]")
        ax_kde.set_xlabel(f"log1p({metric})")
        ax_kde.set_ylabel("Gęstość prawdopodobieństwa")

        # Boxplot porównawczy z oznaczeniem średniej (biały punkt)
        ax_box = axes[i, 1]
        sns.boxplot(
            data=df,
            x="AnomalyLabel",
            y=log_metric,
            hue="AnomalyLabel",
            palette=PALETTE_ANOMALY,
            legend=False,
            ax=ax_box,
            showmeans=True,
            meanprops={"marker": "o", "markerfacecolor": "white", "markeredgecolor": "black"}
        )
        ax_box.set_title(f"Porównanie rozstępu log1p({metric}) (Norma vs Anomalia)")
        ax_box.set_xlabel("Klasa Ground Truth")
        ax_box.set_ylabel(f"log1p({metric})")

    plt.tight_layout()
    if output_dir:
        plt.savefig(output_dir / "01_univariate_continuous.png", bbox_inches="tight")
    plt.close()


def plot_univariate_temporal(df: pd.DataFrame, output_dir: Optional[Path] = None):
    """
    Krok 2.2: Analiza periodyczności i cech czasowych (HourOfDay, DayOfWeek).
    Pozwala zidentyfikować anomalie behawioralne typu 'unusual_time'.
    """
    fig, axes = plt.subplots(1, 2, figsize=(16, 5))

    # Rozkład zapytań w ujęciu godzinowym
    ax_hour = axes[0]
    sns.countplot(
        data=df,
        x="HourOfDay",
        hue="AnomalyLabel",
        palette=PALETTE_ANOMALY,
        ax=ax_hour
    )
    ax_hour.set_title("Rozkład aktywności w ujęciu godzinowym (HourOfDay)")
    ax_hour.set_xlabel("Godzina w ciągu doby (0 - 23)")
    ax_hour.set_ylabel("Liczba operacji")

    # Rozkład zapytań w ujęciu dni tygodnia
    ax_day = axes[1]
    days_labels = ["Niedz (0)", "Pon (1)", "Wt (2)", "Śr (3)", "Czw (4)", "Pt (5)", "Sob (6)"]
    sns.countplot(
        data=df,
        x="DayOfWeek",
        hue="AnomalyLabel",
        palette=PALETTE_ANOMALY,
        ax=ax_day
    )
    ax_day.set_title("Rozkład aktywności wg dni tygodnia (DayOfWeek)")
    ax_day.set_xlabel("Dzień tygodnia")
    ax_day.set_xticks(range(7))
    ax_day.set_xticklabels(days_labels)
    ax_day.set_ylabel("Liczba operacji")

    plt.tight_layout()
    if output_dir:
        plt.savefig(output_dir / "02_univariate_temporal.png", bbox_inches="tight")
    plt.close()


def plot_multivariate_correlation(df: pd.DataFrame, output_dir: Optional[Path] = None):
    """
    Krok 3.1: Macierz korelacji rangowej Spearmana.
    Wybór Spearmana zamiast Pearsona wynika z faktu, że metryki wykonania zapytań
    charakteryzują się nieliniowymi zależnościami monotonicznymi i dużą asymetrią.
    """
    selected_cols = [
        "DurationMicroseconds", "LogicalReads", "RowCount", "SqlTextLength",
        "HourOfDay", "DayOfWeek", "IsWeekend", "IsNightHours",
        "IsSelect", "IsInsert", "IsCustomer", "IsProduct", "IsSalesOrderHeader",
        "IsOfficePc", "IsHomePc", "IsMobileLaptop", "IsRogueDevice",
        "IsAnomaly"
    ]
    cols_to_corr = [c for c in selected_cols if c in df.columns]
    corr = df[cols_to_corr].corr(method="spearman")

    fig, ax = plt.subplots(figsize=(14, 12))
    sns.heatmap(
        corr,
        annot=True,
        fmt=".2f",
        cmap="coolwarm",
        center=0,
        square=True,
        linewidths=0.5,
        cbar_kws={"shrink": 0.8},
        ax=ax
    )
    ax.set_title("Macierz korelacji rang Spearmana kluczowych cech z celem IsAnomaly")
    plt.tight_layout()
    if output_dir:
        plt.savefig(output_dir / "03_correlation_heatmap.png", bbox_inches="tight")
    plt.close()


def plot_multivariate_scatter(df: pd.DataFrame, output_dir: Optional[Path] = None):
    """
    Krok 3.2: Relacje wielowymiarowe z kolorowaniem według podklas anomalii (AnomalyClass).
    Umożliwia ocenę rozłączności klastrów nietypowych obciążeń w przestrzeni cech.
    """
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    # Wykres 1: Godzina doby vs logarytmiczne odczyty logiczne
    ax1 = axes[0]
    sns.scatterplot(
        data=df,
        x="HourOfDay",
        y="log_LogicalReads" if "log_LogicalReads" in df.columns else "LogicalReads",
        hue="AnomalyClass",
        style="AnomalyLabel",
        s=75,
        alpha=0.85,
        ax=ax1
    )
    ax1.set_title("Godzina doby vs log1p(LogicalReads) [Wykrywanie unusual_time]")
    ax1.set_xlabel("Godzina w ciągu doby (HourOfDay)")
    ax1.set_ylabel("log1p(LogicalReads)")

    # Wykres 2: Zależność zasobożerności: Odczyty logiczne vs Czas trwania
    ax2 = axes[1]
    sns.scatterplot(
        data=df,
        x="log_LogicalReads" if "log_LogicalReads" in df.columns else "LogicalReads",
        y="log_DurationMicroseconds" if "log_DurationMicroseconds" in df.columns else "DurationMicroseconds",
        hue="AnomalyClass",
        style="AnomalyLabel",
        s=75,
        alpha=0.85,
        ax=ax2
    )
    ax2.set_title("log1p(LogicalReads) vs log1p(DurationMicroseconds)")
    ax2.set_xlabel("log1p(LogicalReads)")
    ax2.set_ylabel("log1p(DurationMicroseconds)")

    plt.tight_layout()
    if output_dir:
        plt.savefig(output_dir / "04_multivariate_scatter.png", bbox_inches="tight")
    plt.close()


def plot_temporal_volume_analysis(df: pd.DataFrame, output_dir: Optional[Path] = None):
    """
    Krok 4: Analiza wolumenu w czasie oraz struktury sesji.
    Wizualizuje skoki częstotliwości zapytań (volume_spike) w oknach czasowych 1h
    oraz rozkład liczby zapytań przypadających na pojedynczą sesję bazy danych.
    """
    fig, axes = plt.subplots(2, 1, figsize=(15, 10))

    # Analiza szeregu czasowego w oknach godzinowych
    ax_time = axes[0]
    if "Timestamp" in df.columns and df["Timestamp"].notna().any():
        df_sorted = df.dropna(subset=["Timestamp"]).sort_values("Timestamp").copy()
        df_sorted.set_index("Timestamp", inplace=True)

        norm_ts = df_sorted[df_sorted["IsAnomaly"] == 0].resample("1h").size()
        anom_ts = df_sorted[df_sorted["IsAnomaly"] == 1].resample("1h").size()

        resampled_df = pd.DataFrame({"Norma": norm_ts, "Anomalia": anom_ts}).fillna(0)
        resampled_df.plot(
            kind="bar",
            stacked=True,
            color=PALETTE_ANOMALY,
            ax=ax_time,
            width=0.8
        )
        ax_time.set_title("Wolumen operacji w oknach 1-godzinnych (Identyfikacja anomalii volume_spike)")
        ax_time.set_ylabel("Liczba wykonanych zapytań")
        ax_time.set_xlabel("Oś czasu (Miesiąc-Dzień Godzina)")

        # Redukcja zagęszczenia etykiet osi czasu
        ticks = [i for i in range(0, len(resampled_df), max(1, len(resampled_df) // 12))]
        ax_time.set_xticks(ticks)
        ax_time.set_xticklabels([resampled_df.index[i].strftime("%m-%d %H:%M") for i in ticks], rotation=30)
    else:
        ax_time.text(0.5, 0.5, "Brak znaczników Timestamp do analizy szeregu", ha="center")

    # Rozkład zapytań w ramach pojedynczego SessionId
    ax_sess = axes[1]
    if "SessionId" in df.columns:
        sess_counts = df.groupby(["SessionId", "AnomalyLabel"]).size().reset_index(name="QueryCount")
        sns.boxplot(
            data=sess_counts,
            x="AnomalyLabel",
            y="QueryCount",
            hue="AnomalyLabel",
            palette=PALETTE_ANOMALY,
            legend=False,
            ax=ax_sess,
            showmeans=True,
            meanprops={"marker": "o", "markerfacecolor": "white", "markeredgecolor": "black"}
        )
        ax_sess.set_title("Rozkład liczby operacji przypadających na pojedynczą sesję bazy danych")
        ax_sess.set_xlabel("Typ sesji")
        ax_sess.set_ylabel("Liczba zapytań w sesji")
    else:
        ax_sess.text(0.5, 0.5, "Brak kolumny SessionId do grupowania sesyjnego", ha="center")

    plt.tight_layout()
    if output_dir:
        plt.savefig(output_dir / "05_temporal_volume.png", bbox_inches="tight")
    plt.close()


def plot_dimensionality_reduction(df: pd.DataFrame, output_dir: Optional[Path] = None):
    """
    Krok 5: Redukcja wymiarowości (RobustScaler + PCA).
    Badanie separowalności podklas anomalii w rzucie na przestrzeń 2D oraz wykres Scree Plot.
    """
    feat_cols = [c for c in FEATURE_NAMES if c in df.columns]
    X = df[feat_cols].values

    # Używamy RobustScaler (odpornego na wartości odstające), opierającego się na medianie i IQR
    scaler = RobustScaler()
    X_scaled = scaler.fit_transform(X)

    pca = PCA(n_components=min(10, len(feat_cols)))
    X_pca = pca.fit_transform(X_scaled)

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    # Wykres 1: Scree Plot (udział wariancji wyjaśnionej)
    ax_scree = axes[0]
    exp_var = pca.explained_variance_ratio_
    cum_var = np.cumsum(exp_var)
    ax_scree.bar(range(1, len(exp_var) + 1), exp_var, alpha=0.6, color="#2b5c8f", label="Wariancja pojedynczej składowej")
    ax_scree.step(range(1, len(cum_var) + 1), cum_var, where="mid", color="#d95f02", linewidth=2, label="Skumulowana wariancja")
    ax_scree.set_title("Analiza wariancji wyjaśnionej przez PCA (Scree Plot)")
    ax_scree.set_xlabel("Numer składowej głównej (Principal Component)")
    ax_scree.set_ylabel("Udział w wariancji całkowitej")
    ax_scree.set_ylim(0, 1.05)
    ax_scree.axhline(0.9, color="grey", linestyle="--", alpha=0.8, label="Próg informacyjny 90%")
    ax_scree.legend(loc="best")

    # Wykres 2: Projekcja 2D (PC1 vs PC2) z etykietami AnomalyClass
    ax_pca = axes[1]
    plot_df = pd.DataFrame({
        "PC1": X_pca[:, 0],
        "PC2": X_pca[:, 1],
        "AnomalyClass": df["AnomalyClass"],
        "AnomalyLabel": df["AnomalyLabel"]
    })

    sns.scatterplot(
        data=plot_df,
        x="PC1",
        y="PC2",
        hue="AnomalyClass",
        style="AnomalyLabel",
        s=75,
        alpha=0.85,
        ax=ax_pca
    )
    ax_pca.set_title(f"Rzutowanie 2D (PC1: {exp_var[0]*100:.1f}%, PC2: {exp_var[1]*100:.1f}% wariancji)")
    ax_pca.set_xlabel(f"PC1 ({exp_var[0]*100:.1f}%)")
    ax_pca.set_ylabel(f"PC2 ({exp_var[1]*100:.1f}%)")

    plt.tight_layout()
    if output_dir:
        plt.savefig(output_dir / "06_pca_reduction.png", bbox_inches="tight")
    plt.close()


def run_eda(file_path: Union[str, Path], output_dir: str = "eda_plots"):
    """
    Główna funkcja uruchamiająca pełen rygorystyczny proces EDA.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("ROZPOCZĘCIE PEŁNEJ EKSPLORACYJNEJ ANALIZY DANYCH (EDA) LOGÓW XEVENTS")
    print("=" * 70)

    df = load_dataset(file_path)
    print(f"[+] Pomyślnie załadowano zbiór danych: {file_path}")
    print(f"[+] Wymiary tabeli: {df.shape[0]} rekordów, {df.shape[1]} kolumn.")
    print("\n--- Rozkład klas Ground Truth ---")
    print(df["AnomalyLabel"].value_counts(normalize=True).round(4) * 100)
    print("\n--- Rozkład podklas anomalii ---")
    print(df["AnomalyClass"].value_counts())

    print("\n[*] 1/5: Generowanie rozkładów zmiennych ciągłych (KDE / Boxploty)...")
    plot_univariate_continuous(df, out_dir)

    print("[*] 2/5: Analiza rozkładów czasowych (Godziny / Dni tygodnia)...")
    plot_univariate_temporal(df, out_dir)

    print("[*] 3/5: Obliczanie macierzy korelacji Spearmana i relacji wielowymiarowych...")
    plot_multivariate_correlation(df, out_dir)
    plot_multivariate_scatter(df, out_dir)

    print("[*] 4/5: Analiza dynamiki wolumenu w oknach czasowych i zapytań w sesji...")
    plot_temporal_volume_analysis(df, out_dir)

    print("[*] 5/5: Skalowanie cech (RobustScaler) i rzutowanie przestrzenne PCA...")
    plot_dimensionality_reduction(df, out_dir)

    print("=" * 70)
    print(f"[SUKCES] Wszystkie wykresy analityczne zostały wygenerowane w folderze: {out_dir.resolve()}")
    print("=" * 70)


if __name__ == "__main__":
    # Domyślne uruchomienie dla najnowszego pliku JSON z loggera XEvents
    default_log = Path("LOGI/ml_feature_vectors_20261106_104658.json")
    if default_log.exists():
        run_eda(default_log)
    else:
        print(f"Nie odnaleziono pliku {default_log}. Podaj właściwą ścieżkę do run_eda(path).")
