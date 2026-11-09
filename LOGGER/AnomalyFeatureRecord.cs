using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace LOGGER
{
    public record AnomalyFeatureRecord
    {
        // Metadane identyfikacyjne i korelacyjne
        public string EventName { get; init; } = string.Empty;
        public DateTimeOffset Timestamp { get; init; }
        public DateTimeOffset PhysicalTimestamp { get; init; }
        public int SessionId { get; init; }
        public string Username { get; init; } = string.Empty;
        public string HostName { get; init; } = string.Empty;
        public string ClientAppName { get; init; } = string.Empty;
        public string SimulatedIp { get; init; } = string.Empty;
        public string DeviceContext { get; init; } = string.Empty;
        public string ScenarioId { get; init; } = string.Empty;
        public int IsAnomaly { get; init; }
        public string AnomalyClass { get; init; } = "None";

        // Cechy zapytania / operacji
        public string SqlText { get; init; } = string.Empty;
        public string OperationType { get; init; } = string.Empty;
        public string TargetTable { get; init; } = string.Empty;
        public int SqlTextLength { get; init; }

        // Mierniki wydajnościowe
        public long DurationMicroseconds { get; init; }
        public long CpuTimeMicroseconds { get; init; }
        public long LogicalReads { get; init; }
        public long Writes { get; init; }
        public long RowCount { get; init; }

        // Sygnały błędów
        public bool IsError { get; init; }
        public int ErrorNumber { get; init; }
        public int Severity { get; init; }
        public string ErrorMessage { get; init; } = string.Empty;
    }
}
