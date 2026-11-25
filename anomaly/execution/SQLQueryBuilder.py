"""
Budowa zapytań T-SQL dla bazy AdventureWorksLT (PLAN Faza 4).

Zasady:
- treść zapytania zależy wyłącznie od event_uid (parametry SELECT/UPDATE) oraz record_key
  (zawartość wstawianego rekordu); nie zależy od etykiety ani od kolejności wykonania,
- każde zapytanie kończy znacznik /* gt:<EventUid> */ (gt_tag.py) i nic poza nim nie wiąże go
  z ground truth,
- rekordy są adresowane po kolumnie rowguid (unikalna w każdej tabeli SalesLT); rowguid jest
  deterministycznie wyprowadzany z record_key generatora (record_guid), dzięki czemu UPDATE / DELETE
  i odwołania do rodziców trafiają w rekordy startowe albo wstawione wcześniej w przebiegu (D20),
- zapytanie dotyka tylko tabel dozwolonych dla roli (METODOLOGIA D3): SELECT z JOIN-ami łączy tabelę
  docelową wyłącznie z innymi tabelami, które rola może czytać, i pomija rzadkie tabele użytkownika
  (D15), żeby „rzadki zasób” pozostał rzadki także w liście tabel z logów XE,
- tabela docelowa jest zawsze pierwszą tabelą zapytania (po FROM / INTO / UPDATE / DELETE FROM);
  tak samo wyznacza ją LOGGER (TargetTable) i funkcja primary_table poniżej.

Długości zmiennych fragmentów (tokeny, rowguid) są stałe, więc długość SQL nie zależy od
identyfikatora przebiegu ani od etykiety.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import uuid
import zlib
from typing import Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple

from anomaly.domain import DELETE, INSERT, PERMISSIONS, SELECT, UPDATE
from anomaly.execution.gt_tag import tag_sql

# Przestrzeń nazw rowguid symulacji (stała: zmiana unieważnia powtarzalność przebiegów).
RECORD_NAMESPACE = uuid.UUID("6f1c2a9e-4b7d-5e3a-9c41-2d8f0b6a7e15")

# Tabela główna zapytania; ten sam wzorzec ma LOGGER (XeLogReader.PrimaryTableRegex).
PRIMARY_TABLE_REGEX = re.compile(
    r"^\s*(?:SELECT\b.*?\bFROM|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+\[?SalesLT\]?\s*\.\s*\[?(?P<table>\w+)\]?",
    re.IGNORECASE | re.DOTALL,
)
TABLE_REGEX = re.compile(r"\[?SalesLT\]?\s*\.\s*\[?(?P<table>\w+)\]?", re.IGNORECASE)

# --- słowniki wartości (dane z oryginalnej bazy AdventureWorksLT2025.bak) -----------------------
# Produkty w sprzedaży, modele i kategorie liściowe istnieją po każdym przywróceniu bazy
# i nigdy nie są usuwane (DELETE trafia wyłącznie w rekordy symulacji).
PRODUCT_IDS = (680, 707, 708, 711, 712, 715, 716, 717, 722, 738, 739, 742, 748, 779, 780, 782, 793, 797,
               808, 809, 822, 836, 858, 864, 870, 875, 880, 884, 900, 935, 937, 940, 954, 968, 976, 987)
PRODUCT_MODEL_IDS = tuple(range(1, 129))
LEAF_CATEGORY_IDS = tuple(range(5, 42))
TOP_CATEGORY_IDS = (1, 2, 3, 4)
CULTURES = ("en", "fr", "ar", "he", "th", "zh-cht")
SHIP_METHODS = ("CARGO TRANSPORT 5", "CARGO DELUXE", "OVERNIGHT J-FAST", "ZY - EXPRESS")
ADDRESS_TYPES = ("Main Office", "Shipping")
COLORS = ("Black", "Blue", "Red", "Silver", "Yellow", "Multi", "White", "Grey")
FIRST_NAMES = ("James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda", "William",
               "Elizabeth", "David", "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah")
LAST_NAMES = ("Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez",
              "Martinez", "Wilson", "Anderson", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Walker")
TITLES = ("Mr.", "Ms.", "Mrs.", "Dr.")
COMPANIES = ("Apex Global Logistics", "Quantum Innovations", "Summit Financial", "Vanguard Retail Group",
             "Blue Horizon Tech", "Pinnacle Capital", "Cascade Media", "Starlight Industries",
             "Vertex Dynamics", "OmniCorp Systems", "AeroTech Components", "Titan Supply Chain")
SALES_PEOPLE = ("adventure-works\\david8", "adventure-works\\garrett1", "adventure-works\\jae0",
                "adventure-works\\jillian0", "adventure-works\\josé1", "adventure-works\\linda3",
                "adventure-works\\michael9", "adventure-works\\pamela0", "adventure-works\\shu0")
CITIES = (("Calgary", "Alberta", "Canada", "T2P 2G8"), ("Toronto", "Ontario", "Canada", "M4B 1V7"),
          ("Dallas", "Texas", "United States", "75201"), ("Montreal", "Quebec", "Canada", "H1Y 2H7"),
          ("Richmond", "British Columbia", "Canada", "V6B 3P7"), ("San Antonio", "Texas", "United States", "78204"),
          ("Chicago", "Illinois", "United States", "60610"), ("Vancouver", "British Columbia", "Canada", "V7L 4J4"),
          ("Bothell", "Washington", "United States", "98011"), ("Houston", "Texas", "United States", "77003"),
          ("Seattle", "Washington", "United States", "98104"), ("Ottawa", "Ontario", "Canada", "K4B 1S2"))
STREETS = ("Oak Street", "Maple Avenue", "Production Way", "Lakeview Road", "Pine Drive", "Market Street",
           "Elm Court", "River Road", "Hillcrest Blvd", "Cedar Lane")
PRODUCT_WORDS = ("Road", "Touring", "Mountain", "Sport", "Classic", "Pro", "Urban", "Trail", "Racing", "Comfort")
PRODUCT_NOUNS = ("Frame", "Helmet", "Jersey", "Gloves", "Wheel", "Saddle", "Pedal", "Bottle", "Lights", "Rack")
DESCRIPTION_PHRASES = (
    "Lightweight aluminum design for everyday riding.", "Durable construction with a lifetime warranty.",
    "Ergonomic fit for long distance comfort.", "High visibility finish for safe commuting.",
    "Weather resistant materials for all seasons.", "Precision engineered for competitive racing.",
    "Ideal for beginners and casual riders.", "Updated geometry offers improved handling.",
)
SEARCH_WORDS = ("aluminum", "comfort", "durable", "light", "race", "frame", "wheel", "design")
COMMENTS = ("Corrected shipping date", "Status reviewed by finance", "Freight adjusted",
            "Tax recalculated", "Invoice verified", "Payment confirmed")

S = "SalesLT"


# --- pomocnicze -------------------------------------------------------------------------------------

def record_guid(record_key: str) -> str:
    """Deterministyczny rowguid rekordu symulacji (ten sam dla wykonawcy i rekordów startowych)."""
    return str(uuid.uuid5(RECORD_NAMESPACE, record_key)).upper()


def token(value: str) -> str:
    """Stałej długości (10 znaków) token do unikalnych nazw (Product.Name, ProductModel.Name, ...)."""
    return uuid.uuid5(RECORD_NAMESPACE, "tok:" + value).hex[:10].upper()


def rng_for(value: str) -> random.Random:
    # zlib.crc32 zamiast hash(), bo hash() napisów jest losowany per proces.
    return random.Random(zlib.crc32(value.encode("utf-8")))


def primary_table(sql: str) -> Optional[str]:
    m = PRIMARY_TABLE_REGEX.match(sql)
    return m.group("table") if m else None


def referenced_tables(sql: str) -> List[str]:
    seen: List[str] = []
    for m in TABLE_REGEX.finditer(sql):
        if m.group("table") not in seen:
            seen.append(m.group("table"))
    return seen


def _money(x: float) -> str:
    return f"{x:.2f}"


def _q(text: str) -> str:
    return text.replace("'", "''")


def _by_guid(table: str, id_col: str, key: str) -> str:
    return f"(SELECT {id_col} FROM {S}.{table} WHERE rowguid = '{record_guid(key)}')"


# --- SELECT ------------------------------------------------------------------------------------------
# (wymagane tabele dołączane, funkcja rng -> SQL). Tabela docelowa jest pierwsza po FROM.
Template = Tuple[FrozenSet[str], Callable[[random.Random], str]]


def _t(*needs: str):
    def wrap(fn):
        return (frozenset(needs), fn)
    return wrap


def _n(r: random.Random) -> int:
    return r.choice((5, 10, 15, 20, 25))


def _letter(r: random.Random) -> str:
    return r.choice("ABCDEFGHIJKLMNOPRSTW")


SELECT_TEMPLATES: Dict[str, List[Template]] = {
    "Customer": [
        _t()(lambda r: f"SELECT TOP {_n(r)} CustomerID, FirstName, LastName, CompanyName, EmailAddress, Phone "
                       f"FROM {S}.Customer ORDER BY ModifiedDate DESC"),
        _t()(lambda r: f"SELECT CustomerID, Title, FirstName, LastName, CompanyName, SalesPerson "
                       f"FROM {S}.Customer WHERE CustomerID = {r.randint(1, 31525)}"),
        _t()(lambda r: f"SELECT TOP {_n(r)} CustomerID, CompanyName, EmailAddress FROM {S}.Customer "
                       f"WHERE CompanyName LIKE N'{_letter(r)}%' ORDER BY CompanyName"),
        _t()(lambda r: f"SELECT SalesPerson, COUNT(*) AS Customers FROM {S}.Customer "
                       f"WHERE SalesPerson IS NOT NULL GROUP BY SalesPerson ORDER BY Customers DESC"),
        _t("SalesOrderHeader")(lambda r:
            f"SELECT TOP {_n(r)} c.CustomerID, c.CompanyName, COUNT(h.SalesOrderID) AS Orders, SUM(h.TotalDue) AS Revenue "
            f"FROM {S}.Customer c JOIN {S}.SalesOrderHeader h ON h.CustomerID = c.CustomerID "
            f"GROUP BY c.CustomerID, c.CompanyName ORDER BY Revenue DESC"),
        _t("CustomerAddress", "Address")(lambda r:
            f"SELECT TOP {_n(r)} c.CustomerID, c.FirstName, c.LastName, ca.AddressType, a.AddressLine1, a.City, a.PostalCode "
            f"FROM {S}.Customer c JOIN {S}.CustomerAddress ca ON ca.CustomerID = c.CustomerID "
            f"JOIN {S}.Address a ON a.AddressID = ca.AddressID WHERE c.LastName LIKE N'{_letter(r)}%'"),
    ],
    "Address": [
        _t()(lambda r: f"SELECT TOP {_n(r)} AddressID, AddressLine1, City, StateProvince, PostalCode "
                       f"FROM {S}.Address WHERE City = N'{r.choice(CITIES)[0]}'"),
        _t()(lambda r: f"SELECT CountryRegion, StateProvince, COUNT(*) AS Addresses FROM {S}.Address "
                       f"GROUP BY CountryRegion, StateProvince ORDER BY Addresses DESC"),
        _t()(lambda r: f"SELECT TOP {_n(r)} AddressID, AddressLine1, AddressLine2, City, PostalCode, ModifiedDate "
                       f"FROM {S}.Address ORDER BY ModifiedDate DESC"),
        _t("SalesOrderHeader")(lambda r:
            f"SELECT a.CountryRegion, a.StateProvince, COUNT(h.SalesOrderID) AS Orders, SUM(h.TotalDue) AS Revenue "
            f"FROM {S}.Address a JOIN {S}.SalesOrderHeader h ON h.ShipToAddressID = a.AddressID "
            f"GROUP BY a.CountryRegion, a.StateProvince ORDER BY Revenue DESC"),
        _t("CustomerAddress", "Customer")(lambda r:
            f"SELECT TOP {_n(r)} a.AddressID, a.City, a.PostalCode, ca.AddressType, c.CompanyName "
            f"FROM {S}.Address a JOIN {S}.CustomerAddress ca ON ca.AddressID = a.AddressID "
            f"JOIN {S}.Customer c ON c.CustomerID = ca.CustomerID WHERE a.StateProvince = N'{r.choice(CITIES)[1]}'"),
    ],
    "CustomerAddress": [
        _t()(lambda r: f"SELECT AddressType, COUNT(*) AS Links FROM {S}.CustomerAddress GROUP BY AddressType"),
        _t()(lambda r: f"SELECT TOP {_n(r)} CustomerID, AddressID, AddressType, ModifiedDate "
                       f"FROM {S}.CustomerAddress ORDER BY ModifiedDate DESC"),
        _t()(lambda r: f"SELECT CustomerID, AddressID, AddressType FROM {S}.CustomerAddress "
                       f"WHERE CustomerID = {r.randint(1, 31525)}"),
        _t("Customer", "Address")(lambda r:
            f"SELECT TOP {_n(r)} ca.CustomerID, c.FirstName, c.LastName, ca.AddressType, a.AddressLine1, a.City "
            f"FROM {S}.CustomerAddress ca JOIN {S}.Customer c ON c.CustomerID = ca.CustomerID "
            f"JOIN {S}.Address a ON a.AddressID = ca.AddressID ORDER BY ca.ModifiedDate DESC"),
        _t("Address")(lambda r:
            f"SELECT a.City, COUNT(*) AS Links FROM {S}.CustomerAddress ca "
            f"JOIN {S}.Address a ON a.AddressID = ca.AddressID WHERE ca.AddressType = N'{r.choice(ADDRESS_TYPES)}' "
            f"GROUP BY a.City ORDER BY Links DESC"),
    ],
    "SalesOrderHeader": [
        _t()(lambda r: f"SELECT TOP {_n(r)} SalesOrderID, OrderDate, DueDate, Status, TotalDue, CustomerID "
                       f"FROM {S}.SalesOrderHeader ORDER BY OrderDate DESC"),
        _t()(lambda r: f"SELECT COUNT(*) AS Orders, SUM(TotalDue) AS Revenue, AVG(TotalDue) AS AvgOrder "
                       f"FROM {S}.SalesOrderHeader"),
        _t()(lambda r: f"SELECT Status, COUNT(*) AS Orders, SUM(TotalDue) AS Revenue FROM {S}.SalesOrderHeader "
                       f"GROUP BY Status"),
        _t()(lambda r: f"SELECT TOP {_n(r)} SalesOrderID, SalesOrderNumber, SubTotal, TaxAmt, Freight, TotalDue "
                       f"FROM {S}.SalesOrderHeader WHERE TotalDue > {r.randint(100, 5000)} ORDER BY TotalDue DESC"),
        _t("SalesOrderDetail")(lambda r:
            f"SELECT TOP {_n(r)} h.SalesOrderID, h.OrderDate, COUNT(d.SalesOrderDetailID) AS Lines, SUM(d.LineTotal) AS LinesTotal "
            f"FROM {S}.SalesOrderHeader h JOIN {S}.SalesOrderDetail d ON d.SalesOrderID = h.SalesOrderID "
            f"GROUP BY h.SalesOrderID, h.OrderDate ORDER BY LinesTotal DESC"),
        _t("SalesOrderDetail", "Address")(lambda r:
            f"SELECT a.StateProvince, SUM(d.LineTotal) AS Revenue, SUM(d.OrderQty) AS Units "
            f"FROM {S}.SalesOrderHeader h JOIN {S}.SalesOrderDetail d ON d.SalesOrderID = h.SalesOrderID "
            f"JOIN {S}.Address a ON a.AddressID = h.ShipToAddressID GROUP BY a.StateProvince ORDER BY Revenue DESC"),
        _t("Customer")(lambda r:
            f"SELECT TOP {_n(r)} h.SalesOrderID, h.OrderDate, h.TotalDue, c.CompanyName "
            f"FROM {S}.SalesOrderHeader h JOIN {S}.Customer c ON c.CustomerID = h.CustomerID ORDER BY h.TotalDue DESC"),
    ],
    "SalesOrderDetail": [
        _t()(lambda r: f"SELECT TOP {_n(r)} SalesOrderID, ProductID, OrderQty, UnitPrice, LineTotal "
                       f"FROM {S}.SalesOrderDetail ORDER BY LineTotal DESC"),
        _t()(lambda r: f"SELECT ProductID, SUM(OrderQty) AS Units, SUM(LineTotal) AS Revenue FROM {S}.SalesOrderDetail "
                       f"GROUP BY ProductID ORDER BY Revenue DESC"),
        _t()(lambda r: f"SELECT SalesOrderID, SalesOrderDetailID, ProductID, OrderQty, UnitPrice, UnitPriceDiscount "
                       f"FROM {S}.SalesOrderDetail WHERE SalesOrderID = {r.randint(71774, 71963)}"),
        _t("SalesOrderHeader")(lambda r:
            f"SELECT TOP {_n(r)} d.SalesOrderID, d.ProductID, d.LineTotal, h.OrderDate, h.Status "
            f"FROM {S}.SalesOrderDetail d JOIN {S}.SalesOrderHeader h ON h.SalesOrderID = d.SalesOrderID "
            f"WHERE d.OrderQty >= {r.randint(1, 5)} ORDER BY d.LineTotal DESC"),
    ],
    "Product": [
        _t()(lambda r: f"SELECT TOP {_n(r)} ProductID, Name, ProductNumber, Color, StandardCost, ListPrice "
                       f"FROM {S}.Product ORDER BY ListPrice DESC"),
        _t()(lambda r: f"SELECT ProductID, Name, ListPrice FROM {S}.Product WHERE ListPrice > {r.randint(50, 1500)} "
                       f"ORDER BY ListPrice"),
        _t()(lambda r: f"SELECT ProductCategoryID, COUNT(*) AS Items, AVG(ListPrice) AS AvgPrice FROM {S}.Product "
                       f"GROUP BY ProductCategoryID"),
        _t()(lambda r: f"SELECT TOP {_n(r)} ProductID, Name, StandardCost, ListPrice FROM {S}.Product "
                       f"WHERE Color = N'{r.choice(COLORS)}'"),
        _t()(lambda r: f"SELECT ProductID, Name, ProductNumber, SellStartDate, SellEndDate FROM {S}.Product "
                       f"WHERE ProductID = {r.choice(PRODUCT_IDS)}"),
        _t("ProductCategory")(lambda r:
            f"SELECT pc.Name AS Category, COUNT(p.ProductID) AS Items, MIN(p.ListPrice) AS MinPrice, MAX(p.ListPrice) AS MaxPrice "
            f"FROM {S}.Product p JOIN {S}.ProductCategory pc ON pc.ProductCategoryID = p.ProductCategoryID "
            f"GROUP BY pc.Name ORDER BY Items DESC"),
        _t("ProductModel", "ProductModelProductDescription", "ProductDescription")(lambda r:
            f"SELECT TOP {_n(r)} p.ProductID, p.Name, pm.Name AS Model, pd.Description "
            f"FROM {S}.Product p JOIN {S}.ProductModel pm ON pm.ProductModelID = p.ProductModelID "
            f"JOIN {S}.ProductModelProductDescription pmpd ON pmpd.ProductModelID = pm.ProductModelID "
            f"JOIN {S}.ProductDescription pd ON pd.ProductDescriptionID = pmpd.ProductDescriptionID "
            f"WHERE pmpd.Culture = N'{r.choice(CULTURES)}'"),
        _t("ProductModel", "ProductModelProductDescription")(lambda r:
            f"SELECT p.ProductID, p.Name, pm.Name AS Model "
            f"FROM {S}.Product p JOIN {S}.ProductModel pm ON pm.ProductModelID = p.ProductModelID "
            f"LEFT JOIN {S}.ProductModelProductDescription pmpd ON pmpd.ProductModelID = pm.ProductModelID "
            f"AND pmpd.Culture = N'{r.choice(CULTURES)}' WHERE pmpd.ProductModelID IS NULL"),
    ],
    "ProductModel": [
        _t()(lambda r: f"SELECT TOP {_n(r)} ProductModelID, Name, ModifiedDate FROM {S}.ProductModel "
                       f"ORDER BY ModifiedDate DESC"),
        _t()(lambda r: f"SELECT ProductModelID, Name FROM {S}.ProductModel WHERE Name LIKE N'{_letter(r)}%'"),
        _t()(lambda r: f"SELECT ProductModelID, Name, ModifiedDate FROM {S}.ProductModel "
                       f"WHERE ProductModelID = {r.choice(PRODUCT_MODEL_IDS)}"),
        _t("Product")(lambda r:
            f"SELECT TOP {_n(r)} pm.ProductModelID, pm.Name, COUNT(p.ProductID) AS Products, AVG(p.ListPrice) AS AvgPrice "
            f"FROM {S}.ProductModel pm JOIN {S}.Product p ON p.ProductModelID = pm.ProductModelID "
            f"GROUP BY pm.ProductModelID, pm.Name ORDER BY Products DESC"),
        _t("ProductModelProductDescription", "ProductDescription")(lambda r:
            f"SELECT TOP {_n(r)} pm.Name, pmpd.Culture, pd.Description "
            f"FROM {S}.ProductModel pm JOIN {S}.ProductModelProductDescription pmpd ON pmpd.ProductModelID = pm.ProductModelID "
            f"JOIN {S}.ProductDescription pd ON pd.ProductDescriptionID = pmpd.ProductDescriptionID "
            f"WHERE pmpd.Culture = N'{r.choice(CULTURES)}'"),
    ],
    "ProductDescription": [
        _t()(lambda r: f"SELECT TOP {_n(r)} ProductDescriptionID, Description FROM {S}.ProductDescription "
                       f"WHERE Description LIKE N'%{r.choice(SEARCH_WORDS)}%'"),
        _t()(lambda r: f"SELECT TOP {_n(r)} ProductDescriptionID, Description, ModifiedDate FROM {S}.ProductDescription "
                       f"ORDER BY ModifiedDate DESC"),
        _t()(lambda r: f"SELECT COUNT(*) AS Descriptions, AVG(LEN(Description)) AS AvgLength FROM {S}.ProductDescription"),
        _t("ProductModelProductDescription")(lambda r:
            f"SELECT pmpd.Culture, COUNT(*) AS Descriptions FROM {S}.ProductDescription pd "
            f"JOIN {S}.ProductModelProductDescription pmpd ON pmpd.ProductDescriptionID = pd.ProductDescriptionID "
            f"GROUP BY pmpd.Culture"),
        _t("ProductModelProductDescription", "ProductModel")(lambda r:
            f"SELECT TOP {_n(r)} pd.ProductDescriptionID, pd.Description, pm.Name AS Model "
            f"FROM {S}.ProductDescription pd JOIN {S}.ProductModelProductDescription pmpd ON pmpd.ProductDescriptionID = pd.ProductDescriptionID "
            f"JOIN {S}.ProductModel pm ON pm.ProductModelID = pmpd.ProductModelID "
            f"WHERE pmpd.Culture = N'{r.choice(CULTURES)}' ORDER BY pd.ModifiedDate DESC"),
    ],
    "ProductCategory": [
        _t()(lambda r: f"SELECT ProductCategoryID, ParentProductCategoryID, Name FROM {S}.ProductCategory "
                       f"ORDER BY ParentProductCategoryID, Name"),
        _t()(lambda r: f"SELECT c.ProductCategoryID, c.Name, p.Name AS ParentName FROM {S}.ProductCategory c "
                       f"LEFT JOIN {S}.ProductCategory p ON p.ProductCategoryID = c.ParentProductCategoryID "
                       f"WHERE c.ParentProductCategoryID = {r.choice(TOP_CATEGORY_IDS)}"),
        _t()(lambda r: f"SELECT ParentProductCategoryID, COUNT(*) AS Subcategories FROM {S}.ProductCategory "
                       f"GROUP BY ParentProductCategoryID"),
        _t("Product")(lambda r:
            f"SELECT pc.ProductCategoryID, pc.Name, COUNT(p.ProductID) AS Products "
            f"FROM {S}.ProductCategory pc LEFT JOIN {S}.Product p ON p.ProductCategoryID = pc.ProductCategoryID "
            f"GROUP BY pc.ProductCategoryID, pc.Name ORDER BY Products"),
    ],
    "ProductModelProductDescription": [
        _t()(lambda r: f"SELECT Culture, COUNT(*) AS Links FROM {S}.ProductModelProductDescription GROUP BY Culture"),
        _t()(lambda r: f"SELECT TOP {_n(r)} ProductModelID, ProductDescriptionID, Culture, ModifiedDate "
                       f"FROM {S}.ProductModelProductDescription ORDER BY ModifiedDate DESC"),
        _t()(lambda r: f"SELECT ProductModelID, ProductDescriptionID, Culture FROM {S}.ProductModelProductDescription "
                       f"WHERE ProductModelID = {r.choice(PRODUCT_MODEL_IDS)}"),
        _t("ProductModel", "ProductDescription")(lambda r:
            f"SELECT TOP {_n(r)} pm.Name, pmpd.Culture, pd.Description "
            f"FROM {S}.ProductModelProductDescription pmpd JOIN {S}.ProductModel pm ON pm.ProductModelID = pmpd.ProductModelID "
            f"JOIN {S}.ProductDescription pd ON pd.ProductDescriptionID = pmpd.ProductDescriptionID "
            f"WHERE pmpd.Culture = N'{r.choice(CULTURES)}'"),
    ],
}


# --- INSERT (zawartość rekordu zależy od record_key) ---------------------------------------------------

def _insert_values(table: str, key: str, parents: Mapping[str, str]) -> Tuple[List[str], List[str]]:
    r = rng_for("rec:" + key)
    tok = token(key)
    if table == "Customer":
        first, last = r.choice(FIRST_NAMES), r.choice(LAST_NAMES)
        return (["NameStyle", "Title", "FirstName", "LastName", "CompanyName", "EmailAddress", "Phone",
                 "PasswordHash", "PasswordSalt"],
                ["0", f"N'{r.choice(TITLES)}'", f"N'{first}'", f"N'{last}'", f"N'{r.choice(COMPANIES)}'",
                 f"N'{first.lower()}.{tok.lower()}@example.com'",
                 f"N'{r.randint(100, 999)}-555-{r.randint(1000, 9999)}'",
                 f"'{hashlib.sha256(key.encode()).hexdigest()}'", f"'{tok[:8]}'"])
    if table == "Address":
        city, state, country, postal = r.choice(CITIES)
        return (["AddressLine1", "City", "StateProvince", "CountryRegion", "PostalCode"],
                [f"N'{r.randint(100, 9999)} {r.choice(STREETS)}'", f"N'{city}'", f"N'{state}'",
                 f"N'{country}'", f"N'{postal}'"])
    if table == "CustomerAddress":
        return (["CustomerID", "AddressID", "AddressType"],
                [_by_guid("Customer", "CustomerID", parents["Customer"]),
                 _by_guid("Address", "AddressID", parents["Address"]), f"N'{r.choice(ADDRESS_TYPES)}'"])
    if table == "SalesOrderHeader":
        sub = r.uniform(50, 5000)
        return (["DueDate", "CustomerID", "ShipMethod", "PurchaseOrderNumber", "SubTotal", "TaxAmt", "Freight"],
                [f"DATEADD(day, {r.randint(3, 14)}, GETDATE())", _by_guid("Customer", "CustomerID", parents["Customer"]),
                 f"N'{r.choice(SHIP_METHODS)}'", f"N'PO{r.randint(10 ** 9, 10 ** 10 - 1)}'",
                 _money(sub), _money(sub * 0.08), _money(sub * 0.025)])
    if table == "SalesOrderDetail":
        return (["SalesOrderID", "OrderQty", "ProductID", "UnitPrice"],
                [_by_guid("SalesOrderHeader", "SalesOrderID", parents["SalesOrderHeader"]),
                 str(r.randint(1, 12)), str(r.choice(PRODUCT_IDS)), _money(r.uniform(5, 1500))])
    if table == "Product":
        cost = r.uniform(5, 800)
        return (["Name", "ProductNumber", "Color", "StandardCost", "ListPrice", "ProductCategoryID",
                 "ProductModelID", "SellStartDate"],
                [f"N'{r.choice(PRODUCT_WORDS)} {r.choice(PRODUCT_NOUNS)} {tok}'", f"N'SM-{tok}'",
                 f"N'{r.choice(COLORS)}'", _money(cost), _money(cost * r.uniform(1.2, 2.0)),
                 str(r.choice(LEAF_CATEGORY_IDS)), str(r.choice(PRODUCT_MODEL_IDS)), "GETDATE()"])
    if table == "ProductModel":
        return (["Name"], [f"N'{r.choice(PRODUCT_WORDS)} Model {tok}'"])
    if table == "ProductDescription":
        return (["Description"], [f"N'{_q(r.choice(DESCRIPTION_PHRASES))} {_q(r.choice(DESCRIPTION_PHRASES))}'"])
    if table == "ProductCategory":
        return (["ParentProductCategoryID", "Name"],
                [str(r.choice(TOP_CATEGORY_IDS)), f"N'{r.choice(PRODUCT_NOUNS)} Category {tok}'"])
    if table == "ProductModelProductDescription":
        return (["ProductModelID", "ProductDescriptionID", "Culture"],
                [_by_guid("ProductModel", "ProductModelID", parents["ProductModel"]),
                 _by_guid("ProductDescription", "ProductDescriptionID", parents["ProductDescription"]),
                 f"N'{parents['Culture']}'"])
    raise ValueError(f"Brak szablonu INSERT dla tabeli {table}")


def insert_statement(table: str, record_key: str, parents: Mapping[str, str]) -> str:
    """INSERT rekordu symulacji (bez znacznika gt). Używany przez wykonawcę i do rekordów startowych."""
    cols, vals = _insert_values(table, record_key, parents)
    cols.append("rowguid")
    vals.append(f"'{record_guid(record_key)}'")
    return f"INSERT INTO {S}.{table} ({', '.join(cols)}) VALUES ({', '.join(vals)})"


# --- UPDATE (parametry zależą od event_uid) --------------------------------------------------------------

UPDATE_SETS: Dict[str, List[Callable[[random.Random, str], str]]] = {
    "SalesOrderHeader": [
        lambda r, t: f"Status = {r.randint(1, 5)}",
        lambda r, t: f"ShipDate = DATEADD(day, {r.randint(1, 10)}, OrderDate)",
        lambda r, t: f"DueDate = DATEADD(day, {r.randint(5, 20)}, OrderDate)",
        lambda r, t: f"Freight = {_money(r.uniform(5, 150))}",
        lambda r, t: f"TaxAmt = ROUND(SubTotal * {r.choice(('0.07', '0.08', '0.09'))}, 2)",
        lambda r, t: f"Comment = N'{r.choice(COMMENTS)}'",
    ],
    "Customer": [
        lambda r, t: f"Phone = N'{r.randint(100, 999)}-555-{r.randint(1000, 9999)}'",
        lambda r, t: f"EmailAddress = N'{r.choice(FIRST_NAMES).lower()}.{t.lower()}@example.com'",
        lambda r, t: f"CompanyName = N'{r.choice(COMPANIES)}'",
        lambda r, t: f"SalesPerson = N'{r.choice(SALES_PEOPLE)}'",
    ],
    "Address": [
        lambda r, t: f"AddressLine2 = N'Suite {r.randint(100, 999)}'",
        lambda r, t: f"AddressLine1 = N'{r.randint(100, 9999)} {r.choice(STREETS)}'",
        lambda r, t: "PostalCode = N'{}'".format(r.choice(CITIES)[3]),
    ],
    "CustomerAddress": [
        lambda r, t: f"AddressType = N'{r.choice(ADDRESS_TYPES)}'",
        lambda r, t: "ModifiedDate = GETDATE()",
    ],
    "Product": [
        lambda r, t: f"ListPrice = ROUND(ListPrice * {r.uniform(0.9, 1.15):.3f}, 2)",
        lambda r, t: f"StandardCost = {_money(r.uniform(5, 800))}",
        lambda r, t: f"Color = N'{r.choice(COLORS)}'",
        lambda r, t: f"SellEndDate = DATEADD(month, {r.randint(6, 36)}, SellStartDate)",
        lambda r, t: f"Weight = {r.uniform(100, 9000):.2f}",
    ],
    "ProductModel": [
        lambda r, t: f"Name = N'{r.choice(PRODUCT_WORDS)} Model {t}'",
        lambda r, t: "ModifiedDate = GETDATE()",
    ],
    "ProductDescription": [
        lambda r, t: f"Description = N'{_q(r.choice(DESCRIPTION_PHRASES))} {_q(r.choice(DESCRIPTION_PHRASES))}'",
    ],
    "ProductCategory": [
        lambda r, t: f"Name = N'{r.choice(PRODUCT_NOUNS)} Category {t}'",
        lambda r, t: f"ParentProductCategoryID = {r.choice(TOP_CATEGORY_IDS)}",
    ],
    "ProductModelProductDescription": [
        lambda r, t: "ModifiedDate = GETDATE()",
        lambda r, t: f"ModifiedDate = DATEADD(day, -{r.randint(1, 30)}, GETDATE())",
    ],
}


class SQLQueryBuilder:

    def __init__(self, schema: str = "SalesLT"):
        if schema != S:
            raise ValueError("Szablony są zdefiniowane dla schematu SalesLT.")
        self.schema = schema

    def build_query(
        self,
        operation_type: str,
        target_table: str,
        event_uid: str,
        record_key: str = "",
        parent_keys: str | Mapping[str, str] = "",
        role: Optional[str] = None,
        avoid_tables: Iterable[str] = (),
    ) -> str:
        """Zapytanie dla jednej operacji generatora, ze znacznikiem /* gt:<EventUid> */.

        role          rola użytkownika; ogranicza JOIN-y do tabel, które rola może czytać
                      (bez roli: tylko zapytania jednotabelowe),
        avoid_tables  tabele niedołączane JOIN-em (rzadkie tabele użytkownika, D15).
        """
        op = operation_type.upper().strip()
        table = target_table.strip()
        r = rng_for(event_uid)
        if op == SELECT:
            body = self._select(table, r, role, set(avoid_tables))
        elif op == INSERT:
            parents = json.loads(parent_keys) if isinstance(parent_keys, str) and parent_keys else (parent_keys or {})
            body = insert_statement(table, self._need_key(record_key, op, event_uid), parents)
        elif op == UPDATE:
            key = self._need_key(record_key, op, event_uid)
            set_clause = r.choice(UPDATE_SETS[table])(r, token(event_uid))
            body = f"UPDATE {S}.{table} SET {set_clause} WHERE rowguid = '{record_guid(key)}'"
        elif op == DELETE:
            key = self._need_key(record_key, op, event_uid)
            body = f"DELETE FROM {S}.{table} WHERE rowguid = '{record_guid(key)}'"
        else:
            raise ValueError(f"Nieznana operacja: {operation_type}")
        return tag_sql(body, event_uid)

    @staticmethod
    def _need_key(record_key, op: str, event_uid: str) -> str:
        if not isinstance(record_key, str) or not record_key:
            raise ValueError(f"{event_uid}: operacja {op} wymaga record_key (generator Fazy 3).")
        return record_key

    @staticmethod
    def _select(table: str, r: random.Random, role: Optional[str], avoid: set) -> str:
        if table not in SELECT_TEMPLATES:
            raise ValueError(f"Brak szablonów SELECT dla tabeli {table}")
        readable = (PERMISSIONS[role][SELECT] if role else frozenset()) - avoid - {table}
        options = [fn for needs, fn in SELECT_TEMPLATES[table] if needs <= readable]
        return r.choice(options)(r)
