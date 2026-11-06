USE [master];
GO

-- ===============================================================================
-- 1. Utworzenie lub aktualizacja Loginu serwerowego dla profilu CEO
-- ===============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'CEO_User')
BEGIN
    CREATE LOGIN [CEO_User] 
    WITH PASSWORD = N'CeoPass2026!Secure#', 
         DEFAULT_DATABASE = [AdventureWorksLT2025],
         CHECK_EXPIRATION = OFF, 
         CHECK_POLICY = OFF;
    PRINT '-> Pomyślnie utworzono login serwerowy [CEO_User].';
END
ELSE
BEGIN
    ALTER LOGIN [CEO_User] 
    WITH PASSWORD = N'CeoPass2026!Secure#', 
         DEFAULT_DATABASE = [AdventureWorksLT2025],
         CHECK_EXPIRATION = OFF, 
         CHECK_POLICY = OFF;
    PRINT '-> Zaktualizowano hasło dla istniejącego loginu [CEO_User].';
END
GO

-- ===============================================================================
-- 2. Utworzenie użytkownika w bazie AdventureWorksLT2025
-- ===============================================================================
USE [AdventureWorksLT2025];
GO

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'CEO_User')
BEGIN
    CREATE USER [CEO_User] FOR LOGIN [CEO_User];
    PRINT '-> Utworzono użytkownika bazy danych [CEO_User] w [AdventureWorksLT2025].';
END
ELSE
BEGIN
    PRINT '-> Użytkownik bazy danych [CEO_User] już istnieje.';
END
GO

-- ===============================================================================
-- 3. Konfiguracja uprawnień zgodnie ze specyfikacją profilu CEO
-- ===============================================================================
-- Zezwolenie na połączenie z bazą
GRANT CONNECT TO [CEO_User];

-- A. Uprawnienia ODCZYTU (SELECT): SalesOrderHeader, Customer, Product
GRANT SELECT ON [SalesLT].[Customer] TO [CEO_User];
GRANT SELECT ON [SalesLT].[Product] TO [CEO_User];
GRANT SELECT ON [SalesLT].[SalesOrderHeader] TO [CEO_User];

-- B. Uprawnienia ZAPISU (INSERT): Wyłącznie tabela Customer (dodawanie kluczowych klientów)
GRANT INSERT ON [SalesLT].[Customer] TO [CEO_User];

-- C. Blokady polityki bezpieczeństwa:
-- Operacje UPDATE i DELETE są dla CEO zakazane
DENY UPDATE, DELETE ON SCHEMA::[SalesLT] TO [CEO_User];

-- Próba zapisu do innych tabel wywoła błąd 229 (Permission Denied) w Extended Events
DENY INSERT ON [SalesLT].[Product] TO [CEO_User];
DENY INSERT ON [SalesLT].[SalesOrderHeader] TO [CEO_User];

PRINT '===============================================================================';
PRINT 'Uprawnienia dla [CEO_User] zostały pomyślnie skonfigurowane:';
PRINT '  [GRANT SELECT] -> SalesLT.Customer, SalesLT.Product, SalesLT.SalesOrderHeader';
PRINT '  [GRANT INSERT] -> SalesLT.Customer (wyłącznie)';
PRINT '  [DENY]         -> UPDATE, DELETE na schemacie SalesLT';
PRINT '  [DENY]         -> INSERT na SalesLT.Product, SalesLT.SalesOrderHeader';
PRINT '===============================================================================';
GO
