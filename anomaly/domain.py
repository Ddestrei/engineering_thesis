"""
Stałe domenowe wspólne dla generatora, wykonawcy i oceny.

- uprawnienia ról: przepisane z dokumentu „Poprawiony schemat użycia” (sekcja „Role oraz uprawnienia”),
- wrażliwość tabel: METODOLOGIA D10 (RiskScore) i D19 (severity incydentu).
"""
from typing import Dict, FrozenSet

SELECT, INSERT, UPDATE, DELETE = "SELECT", "INSERT", "UPDATE", "DELETE"
OPERATIONS = (SELECT, INSERT, UPDATE, DELETE)

# Kody ról używane w identyfikatorach użytkowników i loginach.
CEO, CFO, MKT_MGR, MKT, SALES_MGR, SALES = "CEO", "CFO", "MKT_MGR", "MKT", "SALES_MGR", "SALES"
ROLES = (CEO, CFO, MKT_MGR, MKT, SALES_MGR, SALES)

ROLE_NAMES: Dict[str, str] = {
    CEO: "Dyrektor Generalny (CEO)",
    CFO: "Dyrektor Finansowy (CFO)",
    MKT_MGR: "Kierownik Marketingu (Marketing Manager)",
    MKT: "Pracownik ds. Marketingu (Marketing)",
    SALES_MGR: "Kierownik Sprzedaży (Sales Manager)",
    SALES: "Pracownik ds. Sprzedaży (Sales)",
}

_PRODUCT_READ = frozenset({"Product", "ProductModel", "ProductDescription",
                           "ProductModelProductDescription", "ProductCategory"})

PERMISSIONS: Dict[str, Dict[str, FrozenSet[str]]] = {
    CEO: {
        SELECT: frozenset({"SalesOrderHeader", "Customer", "Product"}),
        INSERT: frozenset({"Customer"}),
        UPDATE: frozenset(),
        DELETE: frozenset(),
    },
    CFO: {
        SELECT: frozenset({"SalesOrderHeader", "SalesOrderDetail", "Address"}),
        INSERT: frozenset(),
        UPDATE: frozenset({"SalesOrderHeader"}),
        DELETE: frozenset({"SalesOrderHeader", "SalesOrderDetail"}),
    },
    MKT_MGR: {
        SELECT: _PRODUCT_READ,
        INSERT: frozenset({"Product", "ProductModel"}),
        UPDATE: frozenset({"Product", "ProductModel"}),
        DELETE: frozenset({"Product", "ProductModel"}),
    },
    MKT: {
        SELECT: _PRODUCT_READ,
        INSERT: frozenset({"ProductDescription", "ProductCategory", "ProductModelProductDescription"}),
        UPDATE: frozenset({"ProductDescription", "ProductCategory", "ProductModelProductDescription"}),
        DELETE: frozenset(),
    },
    SALES_MGR: {
        SELECT: frozenset({"SalesOrderHeader", "SalesOrderDetail", "Customer", "Address", "CustomerAddress"}),
        INSERT: frozenset({"SalesOrderHeader", "SalesOrderDetail", "Customer"}),
        UPDATE: frozenset({"Customer"}),
        DELETE: frozenset({"Customer"}),
    },
    SALES: {
        SELECT: frozenset({"Customer", "Address", "CustomerAddress", "Product"}),
        INSERT: frozenset({"Customer", "Address", "CustomerAddress"}),
        UPDATE: frozenset({"Address", "CustomerAddress"}),
        DELETE: frozenset({"CustomerAddress"}),
    },
}

# METODOLOGIA D10: wrażliwość danych w tabelach.
TABLE_SENSITIVITY: Dict[str, float] = {
    "Customer": 1.0, "Address": 1.0, "CustomerAddress": 1.0,               # dane osobowe
    "SalesOrderHeader": 0.8, "SalesOrderDetail": 0.8,                      # dane finansowe
    "Product": 0.4, "ProductModel": 0.4,                                   # dane katalogowe
    "ProductDescription": 0.2, "ProductCategory": 0.2,                     # treści marketingowe
    "ProductModelProductDescription": 0.2,
}


def is_permitted(role: str, operation: str, table: str) -> bool:
    return table in PERMISSIONS[role].get(operation, frozenset())


def permitted_operations(role: str):
    return [op for op in OPERATIONS if PERMISSIONS[role][op]]


def select_only_tables(role: str) -> FrozenSet[str]:
    """Tabele, które rola wyłącznie odczytuje (kandydaci na „rzadko używany zasób”, D15)."""
    written = PERMISSIONS[role][INSERT] | PERMISSIONS[role][UPDATE] | PERMISSIONS[role][DELETE]
    return PERMISSIONS[role][SELECT] - written
