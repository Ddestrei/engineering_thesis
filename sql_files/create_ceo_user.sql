USE [master];
GO

--Utworzenie lub aktualizacja Loginu serwerowego dla profilu CEO
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

--Utworzenie użytkownika w bazie
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

--Konfiguracja uprawnień zgodnie ze specyfikacją profilu CEO
GRANT CONNECT TO [CEO_User];

GRANT SELECT ON [SalesLT].[Customer] TO [CEO_User];
GRANT SELECT ON [SalesLT].[Product] TO [CEO_User];
GRANT SELECT ON [SalesLT].[SalesOrderHeader] TO [CEO_User];

GRANT INSERT ON [SalesLT].[Customer] TO [CEO_User];

DENY UPDATE, DELETE ON SCHEMA::[SalesLT] TO [CEO_User];

DENY INSERT ON [SalesLT].[Product] TO [CEO_User];
DENY INSERT ON [SalesLT].[SalesOrderHeader] TO [CEO_User];

GO
