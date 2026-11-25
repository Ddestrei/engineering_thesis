"""
Znacznik ground truth w treści zapytania SQL (METODOLOGIA D11, PLAN Faza 2).

Do każdego zapytania wysyłanego przez symulator doklejany jest komentarz:

    /* gt:<EventUid> */

Komentarz niesie WYŁĄCZNIE identyfikator operacji. Nie zawiera etykiety (anomalia / norma),
klasy anomalii, hosta, IP ani czasu. Etykiety i czas symulowany dołączane są dopiero
po odczycie logów, przez połączenie z plikiem generatora po EventUid
(anomaly/features/xe_join.py).

Ten sam format odczytuje program LOGGER (LOGGER/XeLogReader.cs, GtTagRegex),
który wycina komentarz przed liczeniem długości tekstu SQL.
Zmiana formatu wymaga zmiany w obu miejscach.
"""
import re

# EventUid: litery, cyfry, '-' i '_' (np. "train-s42-000123").
EVENT_UID_PATTERN = r"[A-Za-z0-9_-]+"
GT_TAG_REGEX = re.compile(r"/\*\s*gt:(" + EVENT_UID_PATTERN + r")\s*\*/")


def make_tag(event_uid: str) -> str:
    if not re.fullmatch(EVENT_UID_PATTERN, event_uid):
        raise ValueError(f"Niepoprawny EventUid: {event_uid!r}")
    return f"/* gt:{event_uid} */"


def tag_sql(sql_body: str, event_uid: str) -> str:
    """Dokleja znacznik na końcu zapytania (przed średnikiem)."""
    return f"{sql_body.rstrip().rstrip(';')} {make_tag(event_uid)};"


def extract_event_uid(sql_text: str):
    """EventUid ze znacznika albo None, jeśli zapytanie nie pochodzi z symulatora."""
    match = GT_TAG_REGEX.search(sql_text or "")
    return match.group(1) if match else None


def strip_tag(sql_text: str) -> str:
    """Tekst zapytania bez znacznika (do cech, np. długości SQL)."""
    return GT_TAG_REGEX.sub("", sql_text or "").rstrip().rstrip(";").rstrip()
