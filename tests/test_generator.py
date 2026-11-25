"""
Testy Fazy 3: generator wielu ról i użytkowników (METODOLOGIA D3–D6, D15–D19).

Uruchomienie z katalogu głównego repozytorium:  python -m pytest
"""
import json
from collections import Counter

import numpy as np
import pandas as pd
import pytest

from anomaly.domain import (CEO, CFO, DELETE, INSERT, MKT_MGR, PERMISSIONS, ROLES, SELECT, UPDATE,
                            is_permitted, select_only_tables)
from anomaly.generator.dataset import build_dataset
from anomaly.generator.incidents import CLASSES, RARE_RESOURCE, UNUSUAL_RATIO, ratio_target, severity
from anomaly.generator.profiles import ROLE_PROFILES
from anomaly.generator.sampling import weeks
from anomaly.generator.users import USERS_PER_ROLE


@pytest.fixture(scope="module")
def users(dataset11):
    return dataset11[0]


@pytest.fixture(scope="module")
def splits(dataset11):
    return dataset11[1]


def _all_events(splits):
    return pd.concat([d.events.assign(split=s) for s, d in splits.items()], ignore_index=True)


# --- populacja (D4) --------------------------------------------------------------------

def test_population(users):
    assert len(users) == len(ROLES) * USERS_PER_ROLE
    assert Counter(u.role for u in users) == {r: USERS_PER_ROLE for r in ROLES}
    assert len({u.login_name for u in users}) == len(users)
    hosts = [m.hostname for u in users for m in u.device_meta.values()]
    ips = [m.ip for u in users for m in u.device_meta.values()]
    assert len(set(hosts)) == len(hosts) and len(set(ips)) == len(ips)


def test_rare_tables_are_read_only_and_rare(users):
    for u in users:
        assert u.rare_tables, u.user_id
        for t in u.rare_tables:
            assert t in select_only_tables(u.role)
            assert 0.01 <= u.select_weights[t] <= 0.03 + 1e-9
        assert abs(sum(u.select_weights.values()) - 1) < 1e-9


# --- D3: uprawnienia --------------------------------------------------------------------

def test_all_operations_permitted(splits):
    ev = _all_events(splits)
    ok = [is_permitted(r, o, t) for r, o, t in zip(ev["role"], ev["operation_type"], ev["target_table"])]
    assert all(ok)


# --- D6: powtarzalność ----------------------------------------------------------------

def test_deterministic_and_seed_sensitive(splits):
    _, again = build_dataset(11)
    pd.testing.assert_frame_equal(splits["validation"].events, again["validation"].events)
    _, other = build_dataset(22)
    assert not splits["train"].events["timestamp"].equals(other["train"].events["timestamp"])


def test_periods(splits):
    """D5: TRAIN 2 miesiące, VAL i TEST po 1 miesiącu, chronologicznie i bez nakładania."""
    months = {s: sorted(pd.to_datetime(d.events["timestamp"]).dt.to_period("M").unique()) for s, d in splits.items()}
    assert [str(m) for m in months["train"]] == ["2026-01", "2026-02"]
    assert [str(m) for m in months["validation"]] == ["2026-03"]
    assert [str(m) for m in months["test"]] == ["2026-04"]
    assert int(splits["train"].events["is_anomaly"].sum()) == 0


# --- norma i trudne przypadki (D15) ----------------------------------------------------

def test_no_weekend_without_tag(splits):
    ev = _all_events(splits)
    normal = ev[ev["is_anomaly"] == 0]
    weekend = pd.to_datetime(normal["timestamp"]).dt.weekday >= 5
    assert not (weekend & ~normal["hard_normal"].fillna("").str.contains("WEEKEND")).any()
    for role in ROLES:
        if ROLE_PROFILES[role].p_weekend == 0:
            r = normal[normal["role"] == role]
            assert not (pd.to_datetime(r["timestamp"]).dt.weekday >= 5).any(), role


def test_ceo_office_days_and_hours(splits):
    e = splits["train"].events
    office = e[(e["role"] == CEO) & (e["device_context"] == "Office_PC")]
    starts = pd.to_datetime(office.groupby("session_id")["timestamp"].min())
    assert set(starts.dt.weekday) <= {1, 2, 3}
    hours = starts.dt.hour + starts.dt.minute / 60 + starts.dt.second / 3600
    assert hours.between(10.0, 15.0 + 1 / 60).all()     # okno z dokumentu 10–15 (+ kilka sekund przesunięcia)


def test_cfo_after_hours_only_at_month_end(splits):
    e = splits["train"].events
    cfo = e[e["role"] == CFO].assign(me=lambda d: d["hard_normal"].fillna("").str.contains("MONTH_END"))
    sessions = cfo.groupby("session_id").agg(start=("timestamp", "min"), me=("me", "any"),
                                             device=("device_context", "first"))
    late = pd.to_datetime(sessions["start"]).dt.hour >= 17
    assert not (late & ~sessions["me"]).any()
    assert (sessions.loc[sessions["device"] == "Private_PC", "me"]).all()


def test_no_volume_jump_in_partial_weeks(splits):
    """Naprawa błędu: niepełny tydzień nie dostaje pełnego wolumenu (operacje na dzień roboczy podobne)."""
    e = splits["train"].events
    e = e[(e["role"] != CFO) & ~e["hard_normal"].fillna("").str.contains("SELECT_SPIKE")]
    day = pd.to_datetime(e["timestamp"]).dt.normalize()
    per_day = e.groupby(day).size()
    workdays = per_day[per_day.index.weekday < 5]
    partial = [d for w in weeks(pd.Timestamp("2026-01-01").date(), pd.Timestamp("2026-03-01").date())
               if 0 < len([x for x in w if x.weekday() < 5]) < 5 for d in w if d.weekday() < 5]
    partial_mean = workdays[workdays.index.isin(pd.to_datetime(partial))].mean()
    assert 0.6 < partial_mean / workdays.mean() < 1.4


def test_hard_normal_cases_present(splits):
    tags = splits["train"].events["hard_normal"].fillna("")
    for tag in ("MONTH_END", "AFTER_HOURS", "WEEKEND", "ALT_IP", "RARE_TABLE", "SELECT_SPIKE"):
        assert tags.str.contains(tag).any(), tag


# --- incydenty (D16, D18, D19) -------------------------------------------------------------

def test_incident_counts(splits):
    val = splits["validation"].incidents
    assert len(val) == len(ROLES) * len(CLASSES) * 3
    assert (val.groupby(["role", "anomaly_class"]).size() == 3).all()
    test = splits["test"].incidents.groupby(["role", "anomaly_class"]).size()
    assert test.between(2, 4).all()
    for inc in (val, splits["test"].incidents):
        counts = inc["intensity"].value_counts()
        assert abs(counts["strong"] - counts["weak"]) <= len(ROLES) * len(CLASSES)


def test_severity_matches_formula(splits):
    for split in ("validation", "test"):
        e = splits[split].events
        for inc_id, g in e[e["is_anomaly"] == 1].groupby("incident_id"):
            expected = severity(g["anomaly_class"].iloc[0], g["intensity"].iloc[0],
                                list(zip(g["operation_type"], g["target_table"])))
            assert (g["severity_level"] == expected).all(), inc_id
            assert 1 <= expected <= 5


def test_rare_resource_uses_rare_tables(splits, users):
    by_id = {u.user_id: u for u in users}
    for split, idx in (("validation", 0), ("test", 1)):
        e = splits[split].events
        rare = e[(e["anomaly_class"] == RARE_RESOURCE)]
        for _, g in rare.groupby("incident_id"):
            u = by_id[g["user"].iloc[0]]
            expected = u.rare_tables[min(idx, len(u.rare_tables) - 1)]
            assert set(g["target_table"]) == {expected} and set(g["operation_type"]) == {SELECT}


def test_ratio_target_dominates(splits, users):
    by_id = {u.user_id: u for u in users}
    for split in ("validation", "test"):
        e = splits[split].events
        for _, g in e[e["anomaly_class"] == UNUSUAL_RATIO].groupby("incident_id"):
            u = by_id[g["user"].iloc[0]]
            target = ratio_target(u, split)
            assert u.expected_op_shares()[target] < 0.5
            assert (g["operation_type"] == target).mean() >= 0.45


# --- rekordy (pula startowa, D3) -----------------------------------------------------------

def test_record_keys_consistent(splits):
    for split, data in splits.items():
        e, seeds = data.events, data.seeds
        seed_keys = set(seeds["record_key"])
        assert seeds["record_key"].is_unique
        writes = e[e["operation_type"] != SELECT]
        assert (writes["record_key"] != "").all()
        assert (e.loc[e["operation_type"] == SELECT, "record_key"] == "").all()

        inserts = writes[writes["operation_type"] == INSERT]
        assert inserts["record_key"].is_unique
        assert not set(inserts["record_key"]) & seed_keys

        pool = set(seeds.loc[seeds["purpose"] == "update_pool", "record_key"])
        assert set(writes.loc[writes["operation_type"] == UPDATE, "record_key"]) <= pool

        deletes = writes[writes["operation_type"] == DELETE]
        assert deletes["record_key"].is_unique                      # każdy rekord usuwany najwyżej raz
        assert not set(deletes["record_key"]) & pool                 # pula aktualizacji nigdy nie jest usuwana
        delete_seeds = set(seeds.loc[seeds["purpose"] == "delete_target", "record_key"])
        drafts = set(deletes["record_key"]) - delete_seeds
        assert drafts <= set(inserts["record_key"])                  # pozostałe to szkice wstawione wcześniej
        assert set(deletes.loc[deletes["record_key"].isin(drafts), "role"]) <= {MKT_MGR}

        # rodzice istnieją (rekord startowy albo wcześniejszy INSERT) i klucze tabel łączących się nie powtarzają
        known = seed_keys | set(inserts["record_key"])
        links = []
        for table, pk in list(zip(inserts["target_table"], inserts["parent_keys"])) + \
                         list(zip(seeds["table"], seeds["parent_keys"])):
            if not pk:
                continue
            parents = json.loads(pk)
            assert {k for t, k in parents.items() if t != "Culture"} <= known
            if table in ("CustomerAddress", "ProductModelProductDescription"):
                links.append((table,) + tuple(sorted(parents.items())))
        assert len(links) == len(set(links))
