"""
Testy Fazy 4: zapytania SQL dla wszystkich operacji generatora, rekordy startowe i role bazodanowe.

Testy nie łączą się z bazą; sprawdzają, że każde zapytanie jest zgodne z uprawnieniami roli (D3),
trafia w tabelę docelową generatora i adresuje rekordy po deterministycznym rowguid (D20).
Pełny test na żywej bazie: python -m anomaly.run_experiment --seed 11
"""
import json
import re

import pandas as pd
import pytest

from anomaly.domain import DELETE, INSERT, OPERATIONS, PERMISSIONS, ROLES, SELECT, UPDATE
from anomaly.execution import db_admin
from anomaly.execution.DBConfig import DBConfig
from anomaly.execution.DBWorkloadExecutor import DBWorkloadExecutor
from anomaly.execution.ExecutionConfig import ExecutionConfig
from anomaly.execution.SQLQueryBuilder import (SELECT_TEMPLATES, UPDATE_SETS, primary_table, record_guid,
                                               referenced_tables)
from anomaly.execution.gt_tag import extract_event_uid, strip_tag
from anomaly.generator.dataset import users_table

GUID = re.compile(r"'([0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12})'")


@pytest.fixture(scope="module")
def users_df(dataset11):
    return users_table(dataset11[0])


@pytest.fixture(scope="module")
def built(dataset11, users_df):
    """Wszystkie operacje ziarna 11 z gotowym SQL (jak w wykonawcy, bez łączenia z bazą)."""
    _, splits = dataset11
    ev = pd.concat([d.events for d in splits.values()], ignore_index=True)
    executor = DBWorkloadExecutor(DBConfig(server="none", database="none"), ExecutionConfig(dry_run=True))
    return ev.assign(sql=executor.build_queries(ev, users_df))


def test_every_query_tagged_and_targets_generator_table(built):
    assert (built["sql"].map(extract_event_uid) == built["event_uid"]).all()
    body = built["sql"].map(strip_tag)
    assert (body.map(primary_table) == built["target_table"]).all()
    assert (body.str.split(n=1).str[0].str.upper() == built["operation_type"]).all()


def test_queries_respect_role_permissions(built):
    """Tabela docelowa w uprawnieniach operacji, pozostałe tabele (JOIN, podzapytania) w uprawnieniach SELECT."""
    for role, op, table, sql in zip(built["role"], built["operation_type"], built["target_table"], built["sql"]):
        assert table in PERMISSIONS[role][op]
        others = set(referenced_tables(sql)) - {table}
        assert others <= PERMISSIONS[role][SELECT], (role, sql)
        if op in (UPDATE, DELETE):                       # WHERE rowguid wymaga prawa SELECT na tabeli
            assert table in PERMISSIONS[role][SELECT]


def test_joins_skip_users_rare_tables(built, dataset11):
    """JOIN-y raportowe nie dołączają rzadkich tabel użytkownika (D15). Zapisy mogą odwoływać się do
    tabeli rodzica (podzapytanie po rowguid w kluczu obcym); cechy zasobów liczone są z TargetTable."""
    rare = {u.user_id: set(u.rare_tables) for u in dataset11[0]}
    selects = built[built["operation_type"] == SELECT]
    for user, table, sql in zip(selects["user"], selects["target_table"], selects["sql"]):
        assert not (set(referenced_tables(sql)) - {table}) & rare[user], sql
    writes = built[built["operation_type"] != SELECT]
    for table, parents, sql in zip(writes["target_table"], writes["parent_keys"], writes["sql"]):
        parent_tables = set(json.loads(parents)) - {"Culture"} if parents else set()
        assert set(referenced_tables(sql)) - {table} == parent_tables, sql


def test_joins_present_for_roles_described_with_joins(built):
    selects = built[built["operation_type"] == SELECT]
    share = selects.groupby("role")["sql"].apply(lambda s: s.str.contains(" JOIN ").mean())
    assert (share > 0.1).all(), share


def test_writes_address_generator_records(built):
    writes = built[built["operation_type"] != SELECT]
    for op, key, parents, sql in zip(writes["operation_type"], writes["record_key"], writes["parent_keys"], writes["sql"]):
        guids = GUID.findall(sql)
        assert record_guid(key) in guids
        if op == INSERT and parents:
            expected = {record_guid(k) for t, k in json.loads(parents).items() if t != "Culture"}
            assert expected <= set(guids)
        if op in (UPDATE, DELETE):
            assert sql.count("WHERE rowguid =") == 1


def test_query_text_deterministic(built, users_df):
    sample = built.sample(300, random_state=1)
    again = DBWorkloadExecutor(DBConfig(server="none", database="none"),
                               ExecutionConfig(dry_run=True)).build_queries(sample, users_df)
    assert (again == sample["sql"]).all()


def test_record_guid_deterministic_and_distinct():
    assert record_guid("train-s11-R000001") == record_guid("train-s11-R000001")
    keys = [f"train-s11-R{i:06d}" for i in range(5000)] + [f"train-s11-S{i:05d}" for i in range(5000)]
    assert len({record_guid(k) for k in keys}) == len(keys)


def test_templates_cover_all_tables():
    tables = set().union(*(PERMISSIONS[r][op] for r in ROLES for op in OPERATIONS))
    assert tables <= set(SELECT_TEMPLATES)
    updated = set().union(*(PERMISSIONS[r][UPDATE] for r in ROLES))
    assert updated <= set(UPDATE_SETS)


def test_seed_statements_parents_first(dataset11):
    _, splits = dataset11
    for data in splits.values():
        statements = db_admin.seed_statements(data.seeds)
        assert len(statements) == len(data.seeds)
        defined = set()
        for stmt, key in zip(statements, data.seeds["record_key"]):
            own = record_guid(key)
            assert extract_event_uid(stmt) is None                 # rekordy startowe bez znacznika gt
            assert set(GUID.findall(stmt)) - {own} <= defined      # rodzice wstawieni wcześniej
            defined.add(own)


def test_principals_sql_grants_exactly_permissions(users_df):
    sql = db_admin.principals_sql(users_df)
    grants = set(re.findall(r"GRANT (\w+) ON \[SalesLT\]\.\[(\w+)\] TO \[sim_(\w+)\]", sql))
    expected = {(op, t, r.lower()) for r in ROLES for op in OPERATIONS for t in PERMISSIONS[r][op]}
    assert grants == expected
    assert "DENY" not in sql                                       # tylko jawne nadania (least privilege)
    members = re.findall(r"ALTER ROLE \[sim_(\w+)\] ADD MEMBER \[(SIM_\w+)\]", sql)
    assert len(members) == len(users_df)
    assert all(login.startswith(f"SIM_{role.upper()}_") for role, login in members)


def test_logins_sql_drops_legacy_and_hides_nothing():
    sql = db_admin.logins_sql(["SIM_CEO_01"], "$(SIM_PASSWORD)", "AdventureWorksLT2025")
    assert "CREATE LOGIN [SIM_CEO_01]" in sql and "DROP LOGIN [CEO_User]" in sql
    with pytest.raises(ValueError):
        db_admin.logins_sql(["x]; DROP DATABASE y; --"], "p", "AdventureWorksLT2025")
