using System;
using System.Collections.Generic;

namespace LOGGER
{
    /// <summary>
    /// Jedno zakończone zapytanie zarejestrowane przez Extended Events (sql_batch_completed / rpc_completed).
    /// Zawiera WYŁĄCZNIE fakty z logu i identyfikator EventUid. Etykiety ground truth (anomalia, klasa,
    /// incydent) oraz czas symulowany dołącza dopiero Python po EventUid (anomaly/features/xe_join.py).
    /// </summary>
    public record XeEventRecord
    {
        // Identyfikacja
        public string EventUid { get; init; } = string.Empty;          // ze znacznika /* gt:<EventUid> */
        public string EventName { get; init; } = string.Empty;
        public DateTimeOffset PhysicalTimestamp { get; init; }          // czas fizyczny wykonania na serwerze
        public long EventSequence { get; init; }
        public int SessionId { get; init; }
        public string ClientConnectionId { get; init; } = string.Empty;

        // Kontekst obserwowalny po stronie serwera
        public string Username { get; init; } = string.Empty;
        public string HostName { get; init; } = string.Empty;          // WSID klienta
        public string ClientAppName { get; init; } = string.Empty;
        public string AppRun { get; init; } = string.Empty;            // z nazwy aplikacji SIM|run:..|dev:..|ip:..
        public string AppDevice { get; init; } = string.Empty;
        public string AppIp { get; init; } = string.Empty;

        // Zapytanie (bez znacznika gt)
        public string SqlText { get; init; } = string.Empty;
        public int SqlTextLength { get; init; }
        public string OperationType { get; init; } = string.Empty;
        public IReadOnlyList<string> Tables { get; init; } = Array.Empty<string>();

        // Metryki wykonania z Extended Events
        public string Result { get; init; } = string.Empty;            // OK / Error / Abort
        public long DurationMicroseconds { get; init; }
        public long CpuTimeMicroseconds { get; init; }
        public long LogicalReads { get; init; }
        public long PhysicalReads { get; init; }
        public long Writes { get; init; }
        public long RowCount { get; init; }
    }

    /// <summary>
    /// Zdarzenie error_reported. Generator tworzy wyłącznie operacje zgodne z uprawnieniami
    /// (METODOLOGIA D3), więc każdy błąd ze znacznikiem gt oznacza błąd symulacji.
    /// Błędy NIE są eksportowane jako dane do modeli; trafiają do osobnej listy w JSON,
    /// na podstawie której walidacja przerywa przebieg.
    /// </summary>
    public record XeErrorRecord
    {
        public string EventUid { get; init; } = string.Empty;
        public DateTimeOffset PhysicalTimestamp { get; init; }
        public int SessionId { get; init; }
        public string Username { get; init; } = string.Empty;
        public int ErrorNumber { get; init; }
        public int Severity { get; init; }
        public string Message { get; init; } = string.Empty;
        public string SqlText { get; init; } = string.Empty;
    }

    public class XeReadStats
    {
        public int TotalEventsRead { get; set; }
        public int ExportedEvents { get; set; }
        public int ExportedErrors { get; set; }
        public int SkippedSystemNoise { get; set; }        // telemetria, sp_reset_connection, komunikaty informacyjne
        public int SkippedWithoutGtTag { get; set; }       // zapytania spoza symulatora (np. health-check SELECT 1)
        public int SkippedByUserFilter { get; set; }
    }

    public class XeReadResult
    {
        public List<XeEventRecord> Events { get; } = new();
        public List<XeErrorRecord> Errors { get; } = new();
        public XeReadStats Stats { get; } = new();
        public List<string> SourceFiles { get; } = new();
    }
}
