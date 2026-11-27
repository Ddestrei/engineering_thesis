using AnomalyPanel.Core.Data;
using AnomalyPanel.Core.Models;
using AnomalyPanel.Core.ViewModels;

namespace AnomalyPanel.Tests;

internal static class TestData
{
    public static readonly DateTime T0 = new(2026, 4, 6, 9, 0, 0);

    public static AlertRow Alert(long id, string user = "U1", string role = "SALES", int minutes = 0, double risk = 0.75,
                                 double norm = 0.97, double excess = 0, bool anomaly = false, string classes = "",
                                 string hardNormal = "", string incidents = "") => new()
    {
        AlertId = id, WindowId = 1000 + id, UserId = user, RoleId = role, WindowStart = T0.AddMinutes(minutes),
        RiskScore = risk, NormScore = norm, TailExcess = excess, IsAnomaly = anomaly, AnomalyClasses = classes,
        HardNormal = hardNormal, IncidentIds = incidents, ThresholdGroup = role, MainThreshold = 0.95, OperationCount = 5,
    };

    public static RunInfo Run(int id = 1, int seed = 11) => new()
    {
        RunId = id, Seed = seed, Split = "test", Config = "full", CodeCommit = new string('a', 40),
        FrozenSha256 = new string('b', 64), EvaluationCommit = new string('c', 40), WindowCount = 100, EventCount = 400,
    };

    public static ModelInfo Model(int id, string variant, string detector, int runId = 1) => new()
    {
        ModelId = id, RunId = runId, Variant = variant, Detector = detector, Hyperparameters = "{\"nu\": 0.1}",
        FeatureCount = 28, ValPrAuc = 0.55, ValMainFpr = 0.02, ValMainRecall = 0.5, ValMainMcc = 0.54,
    };
}

/// <summary>Repozytorium w pamięci; opcjonalne opóźnienie per model pozwala sprawdzić wyścigi ładowania.</summary>
internal sealed class FakeRepository(PerformanceLog? log = null) : IAnomalyRepository
{
    public List<RunInfo> Runs { get; } = [TestData.Run()];
    public List<ModelInfo> Models { get; } = [];
    public Dictionary<int, List<AlertRow>> AlertsByModel { get; } = [];
    public Dictionary<int, TimeSpan> DelayByModel { get; } = [];
    public List<FeatureDeviation> Features { get; } = [];
    public List<XeEvent> Events { get; } = [];
    public List<IncidentInfo> Incidents { get; } = [];
    public int AlertQueries { get; private set; }
    public Exception? FailAlertsWith { get; set; }

    private Task<IReadOnlyList<T>> Logged<T>(string op, IReadOnlyList<T> items)
    {
        log?.Record("SQL: " + op, TimeSpan.FromMilliseconds(1), items.Count);
        return Task.FromResult(items);
    }

    public Task<IReadOnlyList<RunInfo>> GetRunsAsync(CancellationToken ct = default) => Logged("przebiegi", (IReadOnlyList<RunInfo>)Runs);

    public Task<IReadOnlyList<ModelInfo>> GetModelsAsync(int runId, CancellationToken ct = default) =>
        Logged("modele", (IReadOnlyList<ModelInfo>)Models.Where(m => m.RunId == runId).ToList());

    public Task<IReadOnlyList<ThresholdInfo>> GetThresholdsAsync(int modelId, CancellationToken ct = default) =>
        Logged("progi", (IReadOnlyList<ThresholdInfo>)[new ThresholdInfo { GroupKey = "SALES", MainThreshold = 0.95 }]);

    public async Task<IReadOnlyList<AlertRow>> GetAlertsAsync(int modelId, CancellationToken ct = default)
    {
        AlertQueries++;
        if (FailAlertsWith is not null) throw FailAlertsWith;
        if (DelayByModel.TryGetValue(modelId, out var delay)) await Task.Delay(delay, ct);
        return await Logged("alerty", (IReadOnlyList<AlertRow>)(AlertsByModel.GetValueOrDefault(modelId) ?? []));
    }

    public Task<IReadOnlyList<FeatureDeviation>> GetFeaturesAsync(long windowId, string variant, CancellationToken ct = default) =>
        Logged("cechy okna", (IReadOnlyList<FeatureDeviation>)Features);

    public Task<IReadOnlyList<XeEvent>> GetEventsAsync(long windowId, CancellationToken ct = default) =>
        Logged("zdarzenia XE", (IReadOnlyList<XeEvent>)Events);

    public Task<IReadOnlyList<IncidentInfo>> GetIncidentsAsync(int runId, IReadOnlyCollection<string> incidentIds,
                                                               CancellationToken ct = default) =>
        Logged("incydenty", (IReadOnlyList<IncidentInfo>)Incidents.Where(i => incidentIds.Contains(i.IncidentId)).ToList());
}

internal sealed class FakeFileDialog(string? path) : IFileDialogService
{
    public string? SaveFile(string title, string filter, string defaultFileName) => path;
}
