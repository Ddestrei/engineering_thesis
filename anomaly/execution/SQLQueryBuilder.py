import random
import string
import zlib

from anomaly.execution.gt_tag import tag_sql

"""
    Buduje syntaktycznie i semantycznie poprawne zapytania T-SQL dla bazy AdventureWorksLT 
    oraz dołącza do nich komentarze ze znacznikami symulacji.
"""

class SQLQueryBuilder:

    FIRST_NAMES = [
        "James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael",
        "Linda", "William", "Elizabeth", "David", "Barbara", "Richard", "Susan",
        "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen", "Christopher"
    ]
    LAST_NAMES = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
        "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin"
    ]
    COMPANIES = [
        "Apex Global Logistics", "Quantum Innovations", "Summit Financial",
        "Vanguard Retail Group", "Blue Horizon Tech", "Pinnacle Capital",
        "Cascade Media Solutions", "Starlight Industries", "Vertex Dynamics",
        "OmniCorp Systems", "AeroTech Components", "Titan Supply Chain"
    ]
    COLORS = ["Black", "Blue", "Red", "Silver", "Yellow", "Multi"]

    def __init__(self, schema: str = "SalesLT"):
        self.schema = schema

    # Składa docelowe zapytanie SQL i dokleja znacznik ground truth /* gt:<EventUid> */.
    # Znacznik niesie tylko identyfikator operacji, bez etykiet, hosta, IP i czasu (gt_tag.py).
    def build_query(
        self,
        operation_type: str,
        target_table: str,
        event_uid: str,
    ) -> str:
        # Treść zapytania zależy wyłącznie od event_uid (deterministyczna, niezależna od kolejności
        # wykonania i od --limit). zlib.crc32 zamiast hash(), bo hash() napisów jest losowany per proces.
        rng = random.Random(zlib.crc32(event_uid.encode("utf-8")))
        op = operation_type.upper().strip()
        table = target_table.strip()

        if op == "SELECT":
            sql_body = self._build_select(table, rng)
        elif op == "INSERT":
            sql_body = self._build_insert(table, rng)
        else:
            sql_body = f"SELECT 1 AS Heartbeat FROM {self.schema}.{table}"

        return tag_sql(sql_body, event_uid)

    #losuje i buduje jedno z realistycznych zapytań raportowych CEO dla wskazanej tabeli z użyciem agregacji, klauzul TOP, ORDER BY i WHERE.
    def _build_select(self, table: str, rng: random.Random) -> str:
        full_table = f"{self.schema}.{table}"

        if table.lower() == "customer":
            templates = [
                f"SELECT TOP 10 CustomerID, FirstName, LastName, CompanyName, EmailAddress, Phone FROM {full_table} ORDER BY CustomerID DESC",
                f"SELECT CustomerID, FirstName, LastName, CompanyName, SalesPerson FROM {full_table} WHERE CustomerID = {rng.randint(1, 30118)}",
                f"SELECT TOP 25 CustomerID, CompanyName, EmailAddress FROM {full_table} WHERE CompanyName LIKE '{rng.choice(string.ascii_uppercase)}%'",
                f"SELECT SalesPerson, COUNT(*) AS TotalClients FROM {full_table} WHERE SalesPerson IS NOT NULL GROUP BY SalesPerson",
                f"SELECT TOP 15 CustomerID, FirstName, LastName, ModifiedDate FROM {full_table} ORDER BY ModifiedDate DESC",
            ]
            return rng.choice(templates)

        elif table.lower() == "salesorderheader":
            templates = [
                f"SELECT TOP 10 SalesOrderID, OrderDate, DueDate, Status, TotalDue, CustomerID FROM {full_table} ORDER BY OrderDate DESC",
                f"SELECT COUNT(*) AS OrderCount, SUM(TotalDue) AS TotalRevenue, AVG(TotalDue) AS AvgOrderValue FROM {full_table}",
                f"SELECT TOP 15 SalesOrderID, SalesOrderNumber, SubTotal, TaxAmt, Freight, TotalDue FROM {full_table} WHERE TotalDue > {rng.randint(100, 5000)}",
                f"SELECT Status, COUNT(*) AS StatusCount, SUM(TotalDue) AS StatusRevenue FROM {full_table} GROUP BY Status",
                f"SELECT TOP 5 SalesOrderID, CustomerID, TotalDue FROM {full_table} WHERE CustomerID = {rng.randint(1, 30118)}",
            ]
            return rng.choice(templates)

        elif table.lower() == "product":
            templates = [
                f"SELECT TOP 20 ProductID, Name, ProductNumber, Color, StandardCost, ListPrice FROM {full_table} ORDER BY ListPrice DESC",
                f"SELECT ProductID, Name, ListPrice FROM {full_table} WHERE ListPrice > {rng.randint(50, 1500)} ORDER BY ListPrice DESC",
                f"SELECT ProductCategoryID, COUNT(*) AS ItemCount, AVG(ListPrice) AS AvgPrice FROM {full_table} GROUP BY ProductCategoryID",
                f"SELECT TOP 10 ProductID, Name, StandardCost, ListPrice, Color FROM {full_table} WHERE Color = '{rng.choice(self.COLORS)}'",
                f"SELECT ProductID, Name, ProductNumber, ModifiedDate FROM {full_table} WHERE ProductID = {rng.randint(680, 999)}",
            ]
            return rng.choice(templates)

        else:
            return f"SELECT TOP 10 * FROM {full_table}"

    #generuje poprawne zapytanie INSERT z syntetycznymi danymi osobowymi wypełniając wymagane kolumny PasswordHash i PasswordSalt dla tabeli Customer 
    # lub zapytanie próbne generujące błąd uprawnień/klucza obcego dla innych tabel.
    def _build_insert(self, table: str, rng: random.Random) -> str:
        full_table = f"{self.schema}.{table}"

        if table.lower() == "customer":
            first = rng.choice(self.FIRST_NAMES)
            last = rng.choice(self.LAST_NAMES)
            company = f"SIM_{rng.choice(self.COMPANIES)}"
            sim_id = "".join(rng.choices(string.ascii_lowercase + string.digits, k=6))
            email = f"ceo_sim_{sim_id}@simulation.corp"
            phone = f"{rng.randint(100,999)}-555-{rng.randint(1000,9999)}"
            dummy_hash = "c2hhMjU2X2R1bW15X3Bhc3N3b3JkX2hhc2hfZm9yX3NpbXVsYXRpb25f"
            dummy_salt = "c2FsdDEyMw"

            return (
                f"INSERT INTO {full_table} "
                f"(NameStyle, FirstName, LastName, CompanyName, EmailAddress, Phone, PasswordHash, PasswordSalt, ModifiedDate) "
                f"VALUES (0, '{first}', '{last}', '{company}', '{email}', '{phone}', '{dummy_hash}', '{dummy_salt}', GETDATE())"
            )

        elif table.lower() == "product":
            rand_code = rng.randint(1000, 9999)
            return (
                f"INSERT INTO {full_table} "
                f"(Name, ProductNumber, StandardCost, ListPrice, SellStartDate) "
                f"VALUES ('SimProduct_{rand_code}', 'SIM-{rand_code}', 10.00, 25.00, GETDATE())"
            )

        elif table.lower() == "salesorderheader":
            return (
                f"INSERT INTO {full_table} "
                f"(DueDate, CustomerID, ShipMethod, SubTotal, TaxAmt, Freight) "
                f"VALUES (GETDATE()+7, 1, 'CARGO DELUXE', 150.00, 12.00, 5.00)"
            )

        else:
            return f"INSERT INTO {full_table} DEFAULT VALUES"

