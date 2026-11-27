using AnomalyPanel.Core.Models;

namespace AnomalyPanel.Core.Data;

/// <summary>Odczyt bazy AnomalyResults. Implementacje mierzą czas każdego zapytania w <see cref="PerformanceLog"/>.</summary>
public interface IAnomalyRepository
{
    Task<IReadOnlyList<RunInfo>> GetRunsAsync(CancellationToken ct = default);
    Task<IReadOnlyList<ModelInfo>> GetModelsAsync(int runId, CancellationToken ct = default);
    Task<IReadOnlyList<ThresholdInfo>> GetThresholdsAsync(int modelId, CancellationToken ct = default);
    Task<IReadOnlyList<AlertRow>> GetAlertsAsync(int modelId, CancellationToken ct = default);
    Task<IReadOnlyList<FeatureDeviation>> GetFeaturesAsync(long windowId, string variant, CancellationToken ct = default);
    Task<IReadOnlyList<XeEvent>> GetEventsAsync(long windowId, CancellationToken ct = default);
    Task<IReadOnlyList<IncidentInfo>> GetIncidentsAsync(int runId, IReadOnlyCollection<string> incidentIds,
                                                        CancellationToken ct = default);
}
