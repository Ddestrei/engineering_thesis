namespace AnomalyPanel.Core.Models;

/// <summary>Pełna ścieżka alertu: model i próg → okno i jego cechy → zdarzenia XE → tekst SQL; plus ground truth.</summary>
public sealed class AlertDetail
{
    public required AlertRow Alert { get; init; }
    public required ModelInfo Model { get; init; }
    public required RunInfo Run { get; init; }
    public ThresholdInfo? Threshold { get; init; }
    public IReadOnlyList<FeatureDeviation> Features { get; init; } = [];
    public IReadOnlyList<XeEvent> Events { get; init; } = [];
    public IReadOnlyList<IncidentInfo> Incidents { get; init; } = [];

    /// <summary>Cechy, które najbardziej odbiegają od normy jednostki (największe |z| robust, D12).</summary>
    public IReadOnlyList<FeatureDeviation> TopDeviations(int k = 5) =>
        Features.Where(f => f.RobustZ is not null).OrderByDescending(f => f.RobustZ).ThenBy(f => f.Feature)
                .Take(k).ToList();
}
