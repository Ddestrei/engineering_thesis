using Dapper;
using Microsoft.Data.SqlClient;
using AnomalyPanel.Core.Models;

namespace AnomalyPanel.Core.Data;

/// <summary>Repozytorium SQL Server (Dapper). Czas mierzony od otwarcia połączenia do zmaterializowania wyników.</summary>
public sealed class SqlAnomalyRepository(string connectionString, PerformanceLog log) : IAnomalyRepository
{
    private const int CommandTimeoutSec = 30;

    private async Task<IReadOnlyList<T>> QueryAsync<T>(string operation, string sql, object? args, CancellationToken ct) =>
        await log.MeasureAsync(operation, async () =>
        {
            await using var conn = new SqlConnection(connectionString);
            await conn.OpenAsync(ct);
            var cmd = new CommandDefinition(sql, args, commandTimeout: CommandTimeoutSec, cancellationToken: ct);
            return (IReadOnlyList<T>)(await conn.QueryAsync<T>(cmd)).AsList();
        });

    public Task<IReadOnlyList<RunInfo>> GetRunsAsync(CancellationToken ct = default) =>
        QueryAsync<RunInfo>("SQL: przebiegi", """
            SELECT RunId, Seed, Split, Config, CodeCommit, CodeDirty, FrozenSha256, EvaluationCommit, ExportedAt,
                   WindowCount, AnomalousWindows, EventCount, XeJsonPath
            FROM dbo.Runs ORDER BY Split, Config, Seed
            """, null, ct);

    public Task<IReadOnlyList<ModelInfo>> GetModelsAsync(int runId, CancellationToken ct = default) =>
        QueryAsync<ModelInfo>("SQL: modele", """
            SELECT ModelId, RunId, Variant, Detector, Hyperparameters, FeatureCount, ValPrAuc, ValMainFpr,
                   ValMainRecall, ValMainMcc, AlertCount
            FROM dbo.Models WHERE RunId = @runId
            """, new { runId }, ct);

    public Task<IReadOnlyList<ThresholdInfo>> GetThresholdsAsync(int modelId, CancellationToken ct = default) =>
        QueryAsync<ThresholdInfo>("SQL: progi", """
            SELECT GroupKey, MainThreshold, Recall80Threshold, ValFpr, ValRecall
            FROM dbo.Thresholds WHERE ModelId = @modelId ORDER BY GroupKey
            """, new { modelId }, ct);

    public Task<IReadOnlyList<AlertRow>> GetAlertsAsync(int modelId, CancellationToken ct = default) =>
        QueryAsync<AlertRow>("SQL: alerty", """
            SELECT a.AlertId, a.WindowId, w.UserId, w.RoleId, w.WindowStart, w.OperationCount,
                   a.RawScore, a.NormScore, a.TailExcess, a.RiskScore, a.ThresholdGroup, a.MainThreshold, a.AlarmRecall80,
                   w.SeverityWeight, w.ContextWeight, w.CtxNonPrimaryDevice, w.CtxAfterHours, w.CtxUnknownIp,
                   w.IsAnomaly, w.IncidentIds, w.AnomalyClasses, w.Intensities, w.Severity, w.HardNormal
            FROM dbo.Alerts a JOIN dbo.Windows w ON w.WindowId = a.WindowId
            WHERE a.ModelId = @modelId
            """, new { modelId }, ct);

    public Task<IReadOnlyList<FeatureDeviation>> GetFeaturesAsync(long windowId, string variant, CancellationToken ct = default) =>
        QueryAsync<FeatureDeviation>("SQL: cechy okna", """
            SELECT Feature, FeatureGroup, Value, RobustZ, UnitMedian, UnitP01, UnitP99, UserMedian, RuleViolated
            FROM dbo.WindowFeatures WHERE WindowId = @windowId AND Variant = @variant
            ORDER BY CASE WHEN RobustZ IS NULL THEN 1 ELSE 0 END, RobustZ DESC, Feature
            """, new { windowId, variant }, ct);

    public Task<IReadOnlyList<XeEvent>> GetEventsAsync(long windowId, CancellationToken ct = default) =>
        QueryAsync<XeEvent>("SQL: zdarzenia XE", """
            SELECT EventUid, SimTimestamp, PhysicalTimestamp, XeEventName, XeEventSequence, XeSessionId, XeConnectionId,
                   LoginName, HostName, Device, Ip, OperationType, TargetTable, SqlText, DurationUs, CpuTimeUs,
                   LogicalReads, PhysicalReads, Writes, [RowCount], Result, GtIncidentId, GtAnomalyClass
            FROM dbo.Events WHERE WindowId = @windowId ORDER BY SimTimestamp, XeEventSequence
            """, new { windowId }, ct);

    public Task<IReadOnlyList<IncidentInfo>> GetIncidentsAsync(int runId, IReadOnlyCollection<string> incidentIds,
                                                               CancellationToken ct = default) =>
        incidentIds.Count == 0
            ? Task.FromResult<IReadOnlyList<IncidentInfo>>([])
            : QueryAsync<IncidentInfo>("SQL: incydenty", """
                SELECT IncidentId, AnomalyClass, Intensity, UserId, RoleId, StartTime, EndTime, SeverityLevel,
                       EventCount, Tables, Operations
                FROM dbo.Incidents WHERE RunId = @runId AND IncidentId IN @incidentIds ORDER BY StartTime
                """, new { runId, incidentIds }, ct);
}
