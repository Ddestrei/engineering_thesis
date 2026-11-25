"""
Przypisanie rekordów bazy do operacji zapisu (decyzja z 25.11.2026: pula rekordów startowych).

Każda operacja INSERT / UPDATE / DELETE dostaje klucz rekordu (record_key), a INSERT rekordu
zależnego także klucze rodziców (parent_keys). Klucz jest zamieniany na rowguid w Fazie 4
(wszystkie tabele SalesLT mają unikalną kolumnę rowguid).

Zasady (gwarantują brak błędów kluczy obcych, METODOLOGIA D3):
- INSERT tworzy nowy rekord symulacyjny (klucz przebiegu, prefiks R),
- UPDATE trafia w rekord z puli aktualizacji danej tabeli (rekordy startowe, prefiks S, nigdy nieusuwane),
- DELETE trafia w świeży rekord startowy bez rekordów zależnych, użyty tylko raz;
  wyjątek: Marketing Manager usuwa szkic wstawiony wcześniej w tej samej sesji (sekwencja z profilu),
- rodzic rekordu zależnego: rekord wstawiony wcześniej w tej samej sesji, jeśli istnieje
  (zamówienie -> pozycje, nowy adres -> powiązanie z klientem, nowy opis -> powiązanie z modelem),
  w przeciwnym razie rekord z puli aktualizacji.

Rekordy startowe (seed_records_<split>.csv) wstawia wykonawca jako administrator przed przebiegiem;
nie niosą znacznika gt, więc nie trafiają do danych eksperymentu.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from anomaly.domain import DELETE, INSERT, MKT_MGR, SELECT, UPDATE

UPDATE_POOL_SIZE = 20          # rekordów startowych na tabelę do aktualizacji (w ramach przebiegu)
CULTURES = ("en", "fr", "ar", "he", "th", "zh-cht")   # kultury opisów w AdventureWorksLT

# Rodzice rekordów zależnych: tabela -> lista (tabela rodzica)
PARENTS: Dict[str, Tuple[str, ...]] = {
    "SalesOrderHeader": ("Customer",),
    "SalesOrderDetail": ("SalesOrderHeader",),
    "CustomerAddress": ("Customer", "Address"),
    "ProductModelProductDescription": ("ProductModel", "ProductDescription"),
}
# Rodzic, którego szukamy najpierw w bieżącej sesji (pozostali zawsze z puli).
SESSION_PARENT = {
    "SalesOrderDetail": "SalesOrderHeader",
    "CustomerAddress": "Address",
    "ProductModelProductDescription": "ProductDescription",
}


class RecordAllocator:
    def __init__(self, rng: np.random.Generator, run_id: str):
        self.rng = rng
        self.run_id = run_id
        self.seeds: List[dict] = []
        self.update_pool: Dict[str, List[str]] = {}
        self._n_run = 0
        self._n_seed = 0
        self._link_keys: Set[Tuple] = set()   # unikalność kluczy głównych tabel łączących

    # --- klucze ---------------------------------------------------------------------
    def _run_key(self) -> str:
        self._n_run += 1
        return f"{self.run_id}-R{self._n_run:06d}"

    def _new_seed(self, table: str, purpose: str) -> str:
        self._n_seed += 1
        key = f"{self.run_id}-S{self._n_seed:05d}"
        parents = self._parents_for_seed(table, key)
        self.seeds.append({"record_key": key, "table": table, "purpose": purpose,
                           "parent_keys": json.dumps(parents) if parents else ""})
        return key

    def pool(self, table: str) -> str:
        """Rekord z puli aktualizacji (tworzony na żądanie do UPDATE_POOL_SIZE, potem losowany)."""
        keys = self.update_pool.setdefault(table, [])
        if len(keys) < UPDATE_POOL_SIZE:
            keys.append(self._new_seed(table, "update_pool"))
            return keys[-1]
        return keys[int(self.rng.integers(len(keys)))]

    # --- rodzice --------------------------------------------------------------------
    def _link(self, table: str, parents: Dict[str, str]) -> Dict[str, str]:
        """Tabele łączące mają klucz główny złożony z rodziców: pilnujemy, by się nie powtórzył."""
        if table == "CustomerAddress":
            for _ in range(50):
                if ("CA", parents["Customer"], parents["Address"]) not in self._link_keys:
                    break
                parents["Customer"] = self.pool("Customer")
            self._link_keys.add(("CA", parents["Customer"], parents["Address"]))
        if table == "ProductModelProductDescription":
            for _ in range(50):
                parents["Culture"] = CULTURES[int(self.rng.integers(len(CULTURES)))]
                k = ("PMPD", parents["ProductModel"], parents["ProductDescription"], parents["Culture"])
                if k not in self._link_keys:
                    break
                parents["ProductModel"] = self.pool("ProductModel")
            self._link_keys.add(("PMPD", parents["ProductModel"], parents["ProductDescription"], parents["Culture"]))
        return parents

    def _parents_for_seed(self, table: str, key: str) -> Dict[str, str]:
        parents: Dict[str, str] = {}
        for parent in PARENTS.get(table, ()):
            # Rekord startowy łączący dostaje własnego, świeżego rodzica „po stronie łączonej”,
            # co gwarantuje unikalny klucz złożony.
            if parent == SESSION_PARENT.get(table):
                parents[parent] = self._new_seed(parent, "parent")
            else:
                parents[parent] = self.pool(parent)
        return self._link(table, parents) if parents else parents

    def _parents_for_insert(self, table: str, session_inserted: Dict[str, List[str]]) -> Dict[str, str]:
        parents: Dict[str, str] = {}
        for parent in PARENTS.get(table, ()):
            in_session = session_inserted.get(parent) if parent == SESSION_PARENT.get(table) else None
            parents[parent] = in_session[-1] if in_session else self.pool(parent)
        return self._link(table, parents) if parents else parents

    # --- przydział dla sesji ------------------------------------------------------------
    def assign_session(self, role: str, ops: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """Dla operacji jednej sesji (w kolejności czasu) zwraca listę (record_key, parent_keys_json)."""
        session_inserted: Dict[str, List[str]] = {}
        out: List[Tuple[str, str]] = []
        for op, table in ops:
            parents: Dict[str, str] = {}
            if op == SELECT:
                key = ""
            elif op == INSERT:
                key = self._run_key()
                parents = self._parents_for_insert(table, session_inserted)
                session_inserted.setdefault(table, []).append(key)
            elif op == UPDATE:
                key = self.pool(table)
            elif op == DELETE:
                drafts = session_inserted.get(table)
                if role == MKT_MGR and drafts:
                    key = drafts.pop()
                else:
                    key = self._new_seed(table, "delete_target")
            else:
                raise ValueError(f"Nieznana operacja: {op}")
            out.append((key, json.dumps(parents) if parents else ""))
        return out


def assign_records(events: pd.DataFrame, rng: np.random.Generator, run_id: str
                   ) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Dodaje kolumny record_key i parent_keys; zwraca (zdarzenia, rekordy startowe).
    Zdarzenia muszą być posortowane chronologicznie."""
    allocator = RecordAllocator(rng, run_id)
    ev = events.reset_index(drop=True)
    keys = [""] * len(ev)
    parents = [""] * len(ev)
    # Sesje przetwarzane w kolejności ich rozpoczęcia, operacje w sesji w kolejności czasu.
    first_ts = ev.groupby("session_id", sort=False)["timestamp"].transform("min")
    ordered = ev.assign(_first=first_ts, _pos=np.arange(len(ev))).sort_values(
        ["_first", "session_id", "timestamp", "_pos"], kind="stable")
    for _, grp in ordered.groupby("session_id", sort=False):
        assigned = allocator.assign_session(grp["role"].iloc[0],
                                            list(zip(grp["operation_type"], grp["target_table"])))
        for pos, (key, parent_json) in zip(grp["_pos"], assigned):
            keys[pos], parents[pos] = key, parent_json
    ev["record_key"] = keys
    ev["parent_keys"] = parents
    seeds = pd.DataFrame(allocator.seeds, columns=["record_key", "table", "purpose", "parent_keys"])
    return ev, seeds
