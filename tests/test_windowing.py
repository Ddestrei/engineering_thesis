"""
Testy Fazy 5: okna UserId × 30 min i cechy D14 (anomaly/features/windowing.py).

Dane syntetyczne w formacie tabeli po joinie XE, żeby każdą definicję cechy sprawdzić na
wartościach policzalnych ręcznie.
"""
import itertools

import numpy as np
import pandas as pd
import pytest

from anomaly.features.windowing import (FEATURE_GROUPS, ID_COLUMNS, LABEL_COLUMNS, MIN_USER_WINDOWS,
                                        ROLE_COLUMNS, base_windows, feature_columns, fit_reference,
                                        prepare_events, relative_features)

USERS = pd.DataFrame({
    "user_id": ["SALES_01", "SALES_02", "CFO_01"],
    "role": ["SALES", "SALES", "CFO"],
    "login_name": ["SIM_SALES_01", "SIM_SALES_02", "SIM_CFO_01"],
})
_uid = itertools.count()


def op(user, ts, operation="SELECT", table="Customer", conn="c1", device="Office_PC", ip="10.0.0.1",
       sql=None, reads=10, rows=1, duration=100, anomaly=0, incident="", cls="", severity=None,
       intensity="", hard_normal=""):
    login = USERS.set_index("user_id").loc[user, "login_name"]
    sql = sql or f"{operation} x FROM SalesLT.{table}"
    return {"event_uid": f"t-s1-{next(_uid):06d}", "sim_timestamp": ts, "username": login,
            "user": user, "login_name": login, "xe_connection_id": f"{user}-{conn}", "device": device,
            "ip": ip, "sql_text": sql, "sql_text_length": len(sql), "operation_type": operation,
            "target_table": table, "logical_reads": reads, "row_count": rows, "duration_us": duration,
            "gt_is_anomaly": anomaly, "gt_anomaly_class": cls or np.nan, "gt_incident_id": incident or np.nan,
            "gt_severity": severity if severity is not None else np.nan,
            "intensity": intensity, "hard_normal": hard_normal}


def events(rows):
    df = pd.DataFrame(rows)
    gt = df[["event_uid", "intensity", "hard_normal"]]
    return prepare_events(df.drop(columns=["intensity", "hard_normal"]), gt, USERS)


def workday_train(user, days=40, start="2026-01-05", table_of=lambda d, k: "Customer", **kw):
    """Po 2 operacje dziennie o 10:05 i 10:10 oraz 1 o 14:00 (dni robocze)."""
    rows = []
    for d in pd.bdate_range(start, periods=days):
        for k, t in enumerate(("10:05", "10:10", "14:00")):
            rows.append(op(user, f"{d.date()} {t}:00", table=table_of(d, k), conn=f"{d.date()}-{k // 2}", **kw))
    return rows


def windows_for(train_rows, eval_rows, variant):
    tr_ev, ev = events(train_rows), events(eval_rows)
    tr_w, w = base_windows(tr_ev), base_windows(ev)
    refs = fit_reference(tr_ev, tr_w, variant)
    return relative_features(ev, w, refs, variant), refs


# --- okna i agregaty --------------------------------------------------------------------

def test_windows_aligned_to_half_hours_left_closed():
    ev = events([op("SALES_01", "2026-03-02 08:00:00"), op("SALES_01", "2026-03-02 08:29:59.9"),
                 op("SALES_01", "2026-03-02 08:30:00"), op("SALES_02", "2026-03-02 08:10:00")])
    w = base_windows(ev).set_index(["UserId", "WindowStart"])
    assert w.loc[("SALES_01", pd.Timestamp("2026-03-02 08:00")), "operation_count"] == 2
    assert w.loc[("SALES_01", pd.Timestamp("2026-03-02 08:30")), "operation_count"] == 1
    assert w.loc[("SALES_02", pd.Timestamp("2026-03-02 08:00")), "operation_count"] == 1
    assert len(w) == 3


def test_base_aggregates_and_labels():
    t = "2026-03-02 10:0"
    ev = events([
        op("SALES_01", t + "1:00", "SELECT", "Customer", conn="a", reads=10, rows=5, duration=100,
           sql="SELECT * FROM SalesLT.Customer c JOIN SalesLT.Address a ON 1=1 JOIN SalesLT.CustomerAddress x ON 1=1"),
        op("SALES_01", t + "2:00", "INSERT", "Address", conn="a", reads=4, rows=1, duration=300, device="Mobile_Laptop",
           anomaly=1, incident="INC-1", cls="volume_spike", severity=3, intensity="strong"),
        op("SALES_01", t + "3:00", "UPDATE", "Address", conn="b", reads=6, rows=1, duration=200,
           anomaly=1, incident="INC-2", cls="unusual_ratio", severity=2, intensity="weak",
           hard_normal="HARD_NORMAL_ALT_IP"),
        op("SALES_01", t + "4:00", "DELETE", "CustomerAddress", conn="b", reads=0, rows=1, duration=400),
    ])
    w = base_windows(ev).iloc[0]
    assert w["operation_count"] == 4 and w["session_count"] == 2 and w["ops_per_session"] == 2
    assert w[["select_ratio", "insert_ratio", "update_ratio", "delete_ratio"]].tolist() == [0.25] * 4
    assert w["unique_tables"] == 3
    assert w["sum_logical_reads"] == 20 and w["sum_rows"] == 8 and w["mean_duration"] == 250
    assert w["avg_join_count"] == pytest.approx(0.5)            # 2 JOIN-y w 1 z 4 zapytań
    assert w["device_Office_PC"] == 0.75 and w["device_Mobile_Laptop"] == 0.25
    assert w["RoleId"] == "SALES" and w["role_SALES"] == 1 and w["role_CFO"] == 0
    assert w["IsAnomaly"] == 1 and w["IncidentIds"] == "INC-1;INC-2" and w["Severity"] == 3
    assert w["AnomalyClasses"] == "unusual_ratio;volume_spike" and w["Intensities"] == "strong;weak"
    assert w["HardNormal"] == "HARD_NORMAL_ALT_IP"


def test_cyclic_time_features():
    ev = events([op("SALES_01", "2026-03-07 18:40:00")])        # sobota, okno 18:30
    w = base_windows(ev).iloc[0]
    assert w["is_weekend"] == 1
    assert w["hour_sin"] == pytest.approx(np.sin(2 * np.pi * 18.5 / 24))
    assert w["dow_cos"] == pytest.approx(np.cos(2 * np.pi * 5 / 7))


def test_unknown_login_is_rejected():
    df = pd.DataFrame([op("SALES_01", "2026-03-02 10:00:00")]).assign(username="SIM_ROGUE")
    with pytest.raises(ValueError, match="spoza katalogu"):
        prepare_events(df.drop(columns=["intensity", "hard_normal"]), df[["event_uid", "intensity", "hard_normal"]],
                       USERS)


# --- cechy względem normy TRAIN ------------------------------------------------------------

def test_relative_features_use_only_train():
    train = workday_train("SALES_01")
    probe = op("SALES_01", "2026-03-02 10:05:00", table="CustomerAddress", ip="10.9.9.9")
    w1, _ = windows_for(train, [probe], "user")
    # Dodanie innych okien VAL (także z tą samą tabelą) nie zmienia cech okna-próbki.
    others = [op("SALES_01", f"2026-03-0{d} 10:05:00", table="CustomerAddress") for d in (3, 4, 5)]
    w2, _ = windows_for(train, [probe] + others, "user")
    cols = feature_columns("user")
    assert np.allclose(w1.iloc[0][cols].astype(float), w2.iloc[0][cols].astype(float))
    row = w1.iloc[0]
    assert row["new_table_flag"] == 1 and row["rare_resource_score"] > 0
    assert row["ip_known_flag"] == 0


def test_after_hours_and_hour_rarity():
    train = workday_train("SALES_01")
    w, refs = windows_for(train, [op("SALES_01", "2026-03-02 10:05:00"),
                                  op("SALES_01", "2026-03-02 20:10:00")], "user")
    day, evening = w.iloc[0], w.iloc[1]
    assert refs["SALES_01"].op_hour_low >= 10 and refs["SALES_01"].op_hour_high <= 14.01
    assert day["after_hours_flag"] == 0 and evening["after_hours_flag"] == 1
    assert evening["hour_rarity"] > day["hour_rarity"]
    assert evening["hour_rarity"] == pytest.approx(-np.log(1 / (refs["SALES_01"].n_windows + 24)))


def test_volume_ratio_to_median():
    train = workday_train("SALES_01")                           # okna 10:00 (2 op.) i 14:00 (1 op.)
    spike = [op("SALES_01", f"2026-03-02 10:{m:02d}:00") for m in range(0, 24, 2)]
    w, refs = windows_for(train, spike, "user")
    assert w.iloc[0]["volume_ratio_to_median"] == pytest.approx(12 / refs["SALES_01"].median_operation_count)


def test_rare_resource_score_counts_operations_and_depends_on_variant():
    # SALES_01 czyta Address rzadko (1 na 20 dni), SALES_02 często: w roli Address nie jest rzadka.
    train = (workday_train("SALES_01", table_of=lambda d, k: "Address" if (k == 2 and d.day == 15) else "Customer")
             + workday_train("SALES_02", table_of=lambda d, k: "Address"))
    one = [op("SALES_01", "2026-03-02 10:05:00", table="Address")]
    many = [op("SALES_01", f"2026-03-02 10:{m:02d}:00", table="Address") for m in range(5, 25, 2)]
    w_user_one, _ = windows_for(train, one, "user")
    w_user_many, _ = windows_for(train, many, "user")
    w_role_many, _ = windows_for(train, many, "role")
    assert w_user_many.iloc[0]["rare_resource_score"] == pytest.approx(10 * w_user_one.iloc[0]["rare_resource_score"])
    assert w_user_one.iloc[0]["rare_resource_score"] > 0
    assert w_role_many.iloc[0]["rare_resource_score"] == 0      # w roli tabela częsta (> 5%)
    assert w_user_many.iloc[0]["new_table_flag"] == 0           # była w TRAIN użytkownika


def test_train_with_anomalies_is_rejected():
    tr = events(workday_train("SALES_01", days=3) + [op("SALES_01", "2026-01-05 03:00:00", anomaly=1, incident="X")])
    with pytest.raises(ValueError, match="TRAIN zawiera okna anomalne"):
        fit_reference(tr, base_windows(tr), "user")


def test_user_with_few_train_windows_uses_role_statistics():
    few = MIN_USER_WINDOWS // 2 - 1                             # 2 okna dziennie
    train = workday_train("SALES_01", days=40) + workday_train("SALES_02", days=few)
    tr = events(train)
    refs = fit_reference(tr, base_windows(tr), "user")
    assert refs["SALES_01"].source == "own"
    assert refs["SALES_02"].source == "role:SALES"
    assert refs["SALES_02"].n_windows == refs["SALES_01"].n_windows + 2 * few


# --- kolumny cech ---------------------------------------------------------------------

def test_feature_columns_follow_d14():
    assert set(FEATURE_GROUPS) == {"time", "volume", "context", "ratios", "resources", "execution"}
    for variant in ("global", "role", "user"):
        cols = feature_columns(variant)
        assert len(cols) == len(set(cols))
        assert not set(cols) & (set(LABEL_COLUMNS) | set(ID_COLUMNS))
        assert not any("error" in c.lower() for c in cols)          # D3
        assert set(ROLE_COLUMNS) <= set(cols) if variant == "global" else not set(ROLE_COLUMNS) & set(cols)
    assert feature_columns("role", ["time", "volume"]) == FEATURE_GROUPS["time"] + FEATURE_GROUPS["volume"]
    with pytest.raises(ValueError):
        feature_columns("team")
