using System.Diagnostics;
using AnomalyPanel.Core.Data;
using AnomalyPanel.Core.Models;
using CommunityToolkit.Mvvm.ComponentModel;

namespace AnomalyPanel.Core.ViewModels;

/// <summary>Szczegóły wybranego alertu: dlaczego alarm (cechy, RiskScore, model i próg) i ścieżka do zdarzeń XE.</summary>
public sealed partial class AlertDetailViewModel(IAnomalyRepository repository, PerformanceLog log) : ObservableObject
{
    public const int TopFeatureCount = 5;

    private int _version;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasAlert), nameof(Features), nameof(TopFeatures), nameof(Events), nameof(Incidents))]
    private AlertDetail? _current;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(SelectedSql))]
    private XeEvent? _selectedEvent;

    [ObservableProperty]
    private bool _isLoading;

    [ObservableProperty]
    private string? _error;

    public bool HasAlert => Current is not null;
    public IReadOnlyList<FeatureDeviation> Features => Current?.Features ?? [];
    public IReadOnlyList<FeatureDeviation> TopFeatures => Current?.TopDeviations(TopFeatureCount) ?? [];
    public IReadOnlyList<XeEvent> Events => Current?.Events ?? [];
    public IReadOnlyList<IncidentInfo> Incidents => Current?.Incidents ?? [];
    public string SelectedSql => SelectedEvent?.SqlText ?? "";

    public void Clear()
    {
        _version++;
        Current = null;
        SelectedEvent = null;
        Error = null;
    }

    /// <summary>Ładuje cechy, zdarzenia XE i incydenty okna alertu. Starsze, spóźnione wyniki są odrzucane.</summary>
    public async Task LoadAsync(AlertRow alert, ModelInfo model, RunInfo run, IReadOnlyList<ThresholdInfo> thresholds)
    {
        var version = ++_version;
        IsLoading = true;
        Error = null;
        var sw = Stopwatch.StartNew();
        try
        {
            var features = repository.GetFeaturesAsync(alert.WindowId, model.Variant);
            var events = repository.GetEventsAsync(alert.WindowId);
            var incidents = repository.GetIncidentsAsync(run.RunId, alert.Incidents);
            await Task.WhenAll(features, events, incidents);
            if (version != _version)
                return;
            var detail = new AlertDetail
            {
                Alert = alert, Model = model, Run = run,
                Threshold = thresholds.FirstOrDefault(t => t.GroupKey == alert.ThresholdGroup),
                Features = features.Result, Events = events.Result, Incidents = incidents.Result,
            };
            Current = detail;
            SelectedEvent = detail.Events.FirstOrDefault();
            log.Record("Panel: szczegóły alertu", sw.Elapsed, detail.Features.Count + detail.Events.Count);
        }
        catch (Exception ex) when (version == _version)
        {
            Current = null;
            Error = "Nie udało się wczytać szczegółów alertu: " + ex.Message;
        }
        finally
        {
            if (version == _version)
                IsLoading = false;
        }
    }
}
