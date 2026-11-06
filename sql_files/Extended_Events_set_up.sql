USE [master];
GO

-- 1. Usunięcie istniejącej sesji o tej samej nazwie (jeśli istnieje)
IF EXISTS (SELECT 1 FROM sys.server_event_sessions WHERE name = N'AW2025_Behavioral_Anomaly_Session')
BEGIN
    ALTER EVENT SESSION [AW2025_Behavioral_Anomaly_Session] ON SERVER STATE = STOP;
    DROP EVENT SESSION [AW2025_Behavioral_Anomaly_Session] ON SERVER;
END
GO

-- 2. Utworzenie sesji Extended Events z pełnym wektorem cech audytowych i behawioralnych
CREATE EVENT SESSION [AW2025_Behavioral_Anomaly_Session] ON SERVER

-- A. Zdarzenie: Zapytania ad-hoc i batche T-SQL
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
        [sqlserver].[is_system] = (0) -- Odrzucenie procesów wewnętrznych silnika
        AND [sqlserver].[database_name] = N'AdventureWorksLT2025'
        AND [sqlserver].[username] <> N'NT SERVICE\SQLTELEMETRY' -- Odrzucenie szumu telemetrycznego CEIP
    )
),

-- B. Zdarzenie: Wywołania procedur składowanych (RPC)
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

-- C. Zdarzenie: Błędy wykonania zapytań (np. Permission Denied 229, błędy składni)
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
        AND [severity] >= 11 -- Błędy realne (odrzuca komunikaty informacyjne INFO 5701 i 5703 o severity 10)
    )
)

-- Target: Asynchroniczny plik .xel
ADD TARGET package0.event_file(
    SET filename = N'C:\XELogs\AW2025_Behavioral_Anomaly.xel',
        max_file_size = (100),       -- Maksymalny rozmiar pliku w MB
        max_rollover_files = (10)    -- Liczba plików w pętli
)
WITH (
    MAX_MEMORY = 8192 KB,
    EVENT_RETENTION_MODE = ALLOW_SINGLE_EVENT_LOSS,
    MAX_DISPATCH_LATENCY = 3 SECONDS, -- Maksymalne opóźnienie zapisu na dysk
    MAX_EVENT_SIZE = 0 KB,
    MEMORY_PARTITION_MODE = NONE,
    TRACK_CAUSALITY = ON,            -- Ułatwia śledzenie sekwencji zdarzeń i ActivityId
    STARTUP_STATE = OFF
);
GO

-- 3. Uruchomienie sesji XE
ALTER EVENT SESSION [AW2025_Behavioral_Anomaly_Session] ON SERVER STATE = START;
GO

PRINT 'Sesja XE [AW2025_Behavioral_Anomaly_Session] została pomyślnie uruchomiona (bez szumów telemetrycznych).';
GO
