-- Sesja Extended Events eksperymentu (PLAN Faza 4).
-- Sesja NIE jest uruchamiana przez ten skrypt: run_experiment.py przed każdym przebiegiem
-- przekierowuje cel event_file na osobny plik C:\XELogs\<run_id>__<znacznik czasu>.xel,
-- uruchamia sesję, a po wykonaniu operacji ją zatrzymuje (zatrzymanie zapisuje bufory do pliku).
--
-- Zdarzenia: sql_batch_completed wystarcza, bo każda operacja symulatora to jeden batch z jedną
-- instrukcją; sql_statement_completed dublowałby zdarzenia i dodawał instrukcje wyzwalaczy
-- (np. SalesLT.iduSalesOrderDetail), które nie są operacjami użytkownika.
-- EVENT_RETENTION_MODE = ALLOW_SINGLE_EVENT_LOSS: SQL Server nie dopuszcza NO_EVENT_LOSS dla zdarzenia
-- error_reported (błąd 25643). Utrata zdarzenia nie przejdzie niezauważona: join wymaga pokrycia 100%
-- i odrzuca przebieg z brakującym zdarzeniem. Bufor zwiększony do 32 MB, by utrata była mało prawdopodobna.
USE [master];
GO

--Usunięcie istniejącej sesji o tej samej nazwie jeśli istnieje
IF EXISTS (SELECT 1 FROM sys.dm_xe_sessions WHERE name = N'AW2025_Behavioral_Anomaly_Session')
    ALTER EVENT SESSION [AW2025_Behavioral_Anomaly_Session] ON SERVER STATE = STOP;
IF EXISTS (SELECT 1 FROM sys.server_event_sessions WHERE name = N'AW2025_Behavioral_Anomaly_Session')
    DROP EVENT SESSION [AW2025_Behavioral_Anomaly_Session] ON SERVER;
GO

--Utworzenie sesji Extended Events z pełnym wektorem cech audytowych i behawioralnych
CREATE EVENT SESSION [AW2025_Behavioral_Anomaly_Session] ON SERVER

-- Zdarzenie: Zapytania ad-hoc i batche T-SQL
ADD EVENT sqlserver.sql_batch_completed(
    ACTION(
        sqlserver.session_id,
        sqlserver.database_name,
        sqlserver.server_principal_name,
        sqlserver.username,
        sqlserver.client_hostname,
        sqlserver.client_app_name,
        sqlserver.client_connection_id,
        sqlserver.sql_text,
        package0.event_sequence
    )
    WHERE (
        [sqlserver].[is_system] = (0)
        AND [sqlserver].[database_name] = N'AdventureWorksLT2025'
        AND [sqlserver].[username] <> N'NT SERVICE\SQLTELEMETRY'
    )
),

--Wywołania procedur składowanych
ADD EVENT sqlserver.rpc_completed(
    ACTION(
        sqlserver.session_id,
        sqlserver.database_name,
        sqlserver.server_principal_name,
        sqlserver.username,
        sqlserver.client_hostname,
        sqlserver.client_app_name,
        sqlserver.client_connection_id,
        sqlserver.sql_text,
        package0.event_sequence
    )
    WHERE (
        [sqlserver].[is_system] = (0)
        AND [sqlserver].[database_name] = N'AdventureWorksLT2025'
        AND [sqlserver].[username] <> N'NT SERVICE\SQLTELEMETRY'
    )
),

--Błędy wykonania zapytań (np. Permission Denied 229, błędy składni)
ADD EVENT sqlserver.error_reported(
    ACTION(
        sqlserver.session_id,
        sqlserver.database_name,
        sqlserver.server_principal_name,
        sqlserver.username,
        sqlserver.client_hostname,
        sqlserver.client_app_name,
        sqlserver.client_connection_id,
        sqlserver.sql_text,
        package0.event_sequence
    )
    WHERE (
        [sqlserver].[is_system] = (0)
        AND [sqlserver].[database_name] = N'AdventureWorksLT2025'
        AND [sqlserver].[username] <> N'NT SERVICE\SQLTELEMETRY'
        AND [severity] >= 11 
    )
)

--Stworzenie asynchronicznego plik .xel
ADD TARGET package0.event_file(
    SET filename = N'C:\XELogs\AW2025_Behavioral_Anomaly.xel',
        max_file_size = (100),       
        max_rollover_files = (10)    
)
WITH (
    MAX_MEMORY = 32768 KB,
    EVENT_RETENTION_MODE = ALLOW_SINGLE_EVENT_LOSS,
    MAX_DISPATCH_LATENCY = 3 SECONDS, 
    MAX_EVENT_SIZE = 0 KB,
    MEMORY_PARTITION_MODE = NONE,
    TRACK_CAUSALITY = ON,            
    STARTUP_STATE = OFF
);
GO

-- Ręczne uruchomienie (poza run_experiment.py):
-- ALTER EVENT SESSION [AW2025_Behavioral_Anomaly_Session] ON SERVER STATE = START;