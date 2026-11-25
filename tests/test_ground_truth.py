"""
Testy Fazy 2: znacznik ground truth w SQL, nazwa aplikacji, identyfikatory generatora
i walidacja połączenia XE <-> generator.

Uruchomienie z katalogu głównego repozytorium:  python -m pytest
"""
import pandas as pd
import pytest

from anomaly.execution.gt_tag import extract_event_uid, make_tag, strip_tag, tag_sql
from anomaly.execution.SessionConnectionManager import SessionConnectionManager
from anomaly.execution.SQLQueryBuilder import SQLQueryBuilder
from anomaly.features.xe_join import run_of, validate_and_join
from anomaly.generator.dataset import incidents_table


# --- znacznik w SQL ---------------------------------------------------------------

def test_tag_roundtrip():
    sql = tag_sql("SELECT TOP 10 * FROM SalesLT.Customer;", "train-s42-000007")
    assert sql.endswith("/* gt:train-s42-000007 */;")
    assert extract_event_uid(sql) == "train-s42-000007"
    assert strip_tag(sql) == "SELECT TOP 10 * FROM SalesLT.Customer"


def test_tag_rejects_unsafe_uid():
    with pytest.raises(ValueError):
        make_tag("x */ DROP TABLE t; /*")


def test_query_contains_only_event_uid():
    """Zapytanie nie może nieść etykiety ani kontekstu, tylko EventUid."""
    sql = SQLQueryBuilder().build_query("INSERT", "Customer", "validation-s123-000042")
    assert extract_event_uid(sql) == "validation-s123-000042"
    for forbidden in ("ScenarioID", "IsAnomaly", "ANOM_", "SimTime", "Host:", "IP:"):
        assert forbidden not in sql
    # Długość SQL liczona po wycięciu znacznika nie zależy od EventUid.
    a = strip_tag(SQLQueryBuilder().build_query("SELECT", "Product", "train-s42-000001"))
    assert "gt:" not in a


def test_app_name_has_no_labels_and_no_separators():
    app = SessionConnectionManager.make_app_name("test-s7777", "Mobile_Laptop", "172.16.88.24")
    assert app == "SIM|run:test-s7777|dev:Mobile_Laptop|ip:172.16.88.24"
    assert ";" not in app and "=" not in app
    with pytest.raises(ValueError):
        SessionConnectionManager.make_app_name("x;y", "Office_PC", "10.0.0.1")


# --- identyfikatory generatora ------------------------------------------------------

@pytest.fixture(scope="module")
def generated(dataset11):
    _, splits = dataset11
    return {name: data.events for name, data in splits.items()}


def test_event_uids_unique_and_prefixed(generated):
    all_uids = pd.concat([df["event_uid"] for df in generated.values()])
    assert all_uids.is_unique
    for split, df in generated.items():
        assert (df["event_uid"].map(run_of) == df["run_id"]).all()
        assert df["run_id"].nunique() == 1 and df["run_id"].iloc[0] == f"{split}-s11"


def test_incidents_table_matches_events(generated):
    val = generated["validation"]
    inc = incidents_table(val)
    assert inc["incident_id"].is_unique
    assert inc["n_events"].sum() == int(val["is_anomaly"].sum())
    assert set(inc["incident_id"]) == set(val.loc[val["is_anomaly"] == 1, "incident_id"])
    assert len(incidents_table(generated["train"])) == 0


# --- walidacja połączenia XE <-> generator ------------------------------------------

def _fake_xe(gt: pd.DataFrame) -> pd.DataFrame:
    """Zdarzenia XE takie, jakie wyprodukowałby LOGGER dla poprawnie wykonanego przebiegu."""
    return pd.DataFrame({
        "EventUid": gt["event_uid"],
        "EventName": "sql_batch_completed",
        "PhysicalTimestamp": "2026-11-25T18:00:00+01:00",
        "EventSequence": range(len(gt)),
        "SessionId": 60,
        "ClientConnectionId": "00000000-0000-0000-0000-000000000000",
        "Username": gt["login_name"],
        "HostName": gt["client_hostname"],
        "ClientAppName": "SIM|...",
        "AppRun": gt["run_id"],
        "AppDevice": gt["device_context"],
        "AppIp": gt["simulated_ip"],
        "SqlText": "SELECT 1 FROM SalesLT.Customer",
        "SqlTextLength": 30,
        "OperationType": gt["operation_type"],
        "Tables": [["Customer"]] * len(gt),
        "Result": "OK",
        "DurationMicroseconds": 100, "CpuTimeMicroseconds": 50, "LogicalReads": 3,
        "PhysicalReads": 0, "Writes": 0, "RowCount": 1,
    })


NO_ERRORS = pd.DataFrame(columns=["EventUid", "ErrorNumber", "Severity", "Message"])


@pytest.fixture
def gt(generated):
    df = generated["validation"].head(40).copy()
    df.insert(0, "split", "validation")
    return df


def test_join_ok(gt):
    joined, report = validate_and_join(_fake_xe(gt), NO_ERRORS, gt)
    assert report.ok, report.summary()
    assert len(joined) == len(gt)
    assert [c for c in joined.columns if c.startswith("gt_")] == [
        "gt_is_anomaly", "gt_anomaly_class", "gt_incident_id", "gt_severity"]
    assert "sim_timestamp" in joined.columns and "device" in joined.columns


def test_join_ignores_other_runs(gt):
    xe = _fake_xe(gt)
    other = xe.head(3).copy()
    other["EventUid"] = ["old-s1-000001", "old-s1-000002", "old-s1-000003"]
    _, report = validate_and_join(pd.concat([xe, other]), NO_ERRORS, gt)
    assert report.ok and report.xe_events_other_runs == 3


@pytest.mark.parametrize("damage, failed_check", [
    (lambda xe: xe.iloc[1:], "każde zdarzenie generatora ma zdarzenie XE"),
    (lambda xe: pd.concat([xe, xe.head(1)]), "każde EventUid najwyżej raz"),
    (lambda xe: xe.assign(Result=["Error"] + ["OK"] * (len(xe) - 1)), "wynikiem OK"),
    (lambda xe: xe.assign(AppDevice="Rogue_Device"), "AppDevice"),
    (lambda xe: pd.concat([xe, xe.head(1).assign(EventUid=xe["EventUid"].iloc[0][:-6] + "999999")]), "brak sierot"),
])
def test_join_rejects_damaged_runs(gt, damage, failed_check):
    joined, report = validate_and_join(damage(_fake_xe(gt)), NO_ERRORS, gt)
    assert not report.ok and joined.empty
    failing = [name for name, items in report.problems.items() if items]
    assert any(failed_check in name for name in failing), failing


def test_join_rejects_sql_errors(gt):
    errors = pd.DataFrame([{"EventUid": gt["event_uid"].iloc[0], "ErrorNumber": 229,
                            "Severity": 14, "Message": "The INSERT permission was denied"}])
    _, report = validate_and_join(_fake_xe(gt), errors, gt)
    assert not report.ok
    assert report.problems["brak błędów SQL w operacjach symulatora (D3)"]


def test_query_text_depends_only_on_event_uid():
    """Treść zapytania jest powtarzalna (Faza 3: koniec z globalnym random w SQLQueryBuilder)."""
    a = SQLQueryBuilder().build_query("SELECT", "Customer", "test-s11-000001")
    b = SQLQueryBuilder().build_query("SELECT", "Customer", "test-s11-000001")
    assert a == b
