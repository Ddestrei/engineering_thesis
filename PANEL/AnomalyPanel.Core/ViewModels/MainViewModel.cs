using System.Collections.ObjectModel;
using System.Diagnostics;
using AnomalyPanel.Core.Data;
using AnomalyPanel.Core.Export;
using AnomalyPanel.Core.Filtering;
using AnomalyPanel.Core.Models;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;

namespace AnomalyPanel.Core.ViewModels;

/// <summary>
/// Główny widok: wybór przebiegu i modelu, kolejka alertów (domyślnie według RiskScore), filtry, eksport, pomiary.
/// Filtrowanie i sortowanie odbywa się w pamięci na alertach modelu wczytanych jednym zapytaniem SQL.
/// </summary>
public sealed partial class MainViewModel : ObservableObject
{
    /// <summary>Wartość filtra „wszyscy / wszystkie”.</summary>
    public const string All = "";

    private readonly IAnomalyRepository _repository;
    private readonly PerformanceLog _log;
    private readonly IFileDialogService _files;
    private readonly SynchronizationContext? _ui;
    private readonly Dictionary<int, IReadOnlyList<ModelInfo>> _modelsByRun = [];
    private List<AlertRow> _all = [];
    private int _loadVersion;
    private bool _suspendReload;
    private bool _suspendFilter;

    public MainViewModel(IAnomalyRepository repository, PerformanceLog log, IFileDialogService files)
    {
        _repository = repository;
        _log = log;
        _files = files;
        _ui = SynchronizationContext.Current;
        Detail = new AlertDetailViewModel(repository, log);
        _log.Recorded += OnTimingRecorded;
    }

    public AlertDetailViewModel Detail { get; }
    public ObservableCollection<RunInfo> Runs { get; } = [];
    public ObservableCollection<QueryTiming> Timings { get; } = [];
    public ObservableCollection<Option<string>> UserOptions { get; } = [new(All, "(wszyscy)")];
    public ObservableCollection<Option<string>> RoleOptions { get; } = [new(All, "(wszystkie)")];

    public IReadOnlyList<Option<string>> Variants { get; } =
        [new("global", "global"), new("role", "per rola"), new("user", "per użytkownik")];

    public IReadOnlyList<Option<string>> Detectors { get; } =
    [
        new("rules", Labels.Detector("rules")), new("robust", Labels.Detector("robust")),
        new("iforest", Labels.Detector("iforest")), new("ocsvm", Labels.Detector("ocsvm")), new("lof", Labels.Detector("lof")),
    ];

    public IReadOnlyList<Option<SortMode>> SortModes { get; } =
    [
        new(SortMode.RiskScore, "RiskScore (D10)"), new(SortMode.DetectorScore, "score detektora (D8)"),
        new(SortMode.Chronological, "chronologicznie"),
    ];

    public IReadOnlyList<Option<string>> ClassOptions { get; } =
    [
        new(All, "(wszystkie)"), new(ClassFilter.AnyAnomaly, "dowolna anomalia"),
        new("volume_spike", Labels.AnomalyClass("volume_spike")), new("unusual_time", Labels.AnomalyClass("unusual_time")),
        new("unusual_ratio", Labels.AnomalyClass("unusual_ratio")),
        new("rare_resource_access", Labels.AnomalyClass("rare_resource_access")),
        new(ClassFilter.Normal, "norma (fałszywe alarmy)"), new(ClassFilter.HardNormal, "trudna norma (HARD_NORMAL)"),
    ];

    public IReadOnlyList<Option<RiskLevel>> RiskLevelOptions { get; } =
    [
        new(RiskLevel.Low, "dowolny"), new(RiskLevel.Medium, $"co najmniej średni (≥ {RiskLevels.MediumFrom:0.00})"),
        new(RiskLevel.High, $"co najmniej wysoki (≥ {RiskLevels.HighFrom:0.00})"),
        new(RiskLevel.Critical, $"krytyczny (≥ {RiskLevels.CriticalFrom:0.00})"),
    ];

    [ObservableProperty] private RunInfo? _selectedRun;
    [ObservableProperty] private string _selectedVariant = "role";
    [ObservableProperty] private string _selectedDetector = "ocsvm";
    [ObservableProperty] private SortMode _selectedSort = SortMode.RiskScore;
    [ObservableProperty] private string _selectedUser = All;
    [ObservableProperty] private string _selectedRole = All;
    [ObservableProperty] private DateTime? _dateFrom;
    [ObservableProperty] private DateTime? _dateTo;
    [ObservableProperty] private string _selectedClass = All;
    [ObservableProperty] private RiskLevel _minRiskLevel = RiskLevel.Low;

    [ObservableProperty] private ObservableCollection<AlertRow> _alerts = [];
    [ObservableProperty] private AlertRow? _selectedAlert;
    [ObservableProperty] private ModelInfo? _currentModel;
    [ObservableProperty] private IReadOnlyList<ThresholdInfo> _thresholds = [];
    [ObservableProperty] private int _totalAlerts;
    [ObservableProperty] private string _status = "Gotowe.";
    [ObservableProperty] private string? _lastError;
    [ObservableProperty] private bool _isBusy;
    [ObservableProperty] private double? _lastLoadMs;
    [ObservableProperty] private double? _lastSqlMs;
    [ObservableProperty] private double? _lastFilterMs;

    /// <summary>Zadanie ostatniego ładowania szczegółów (dla testów i pomiarów).</summary>
    public Task DetailTask { get; private set; } = Task.CompletedTask;

    public AlertFilter CurrentFilter => new(
        UserId: SelectedUser == All ? null : SelectedUser,
        RoleId: SelectedRole == All ? null : SelectedRole,
        From: DateFrom, To: DateTo,
        AnomalyClass: SelectedClass == All ? null : SelectedClass,
        MinRiskLevel: MinRiskLevel);

    public IReadOnlyList<AlertRow> LoadedAlerts => _all;

    // --- reakcje na zmiany ---------------------------------------------------------------------

    partial void OnSelectedRunChanged(RunInfo? value) => ReloadIfActive();
    partial void OnSelectedVariantChanged(string value) => ReloadIfActive();
    partial void OnSelectedDetectorChanged(string value) => ReloadIfActive();
    partial void OnSelectedSortChanged(SortMode value) => ApplyFilter();
    partial void OnSelectedUserChanged(string value) => ApplyFilter();
    partial void OnSelectedRoleChanged(string value) => ApplyFilter();
    partial void OnDateFromChanged(DateTime? value) => ApplyFilter();
    partial void OnDateToChanged(DateTime? value) => ApplyFilter();
    partial void OnSelectedClassChanged(string value) => ApplyFilter();
    partial void OnMinRiskLevelChanged(RiskLevel value) => ApplyFilter();

    partial void OnSelectedAlertChanged(AlertRow? value)
    {
        if (value is null || CurrentModel is null || SelectedRun is null)
        {
            Detail.Clear();
            DetailTask = Task.CompletedTask;
            return;
        }
        DetailTask = Detail.LoadAsync(value, CurrentModel, SelectedRun, Thresholds);
    }

    private void ReloadIfActive()
    {
        if (!_suspendReload)
            _ = LoadAlertsCommand.ExecuteAsync(null);
    }

    private void OnTimingRecorded(QueryTiming t)
    {
        if (_ui is null || _ui == SynchronizationContext.Current)
            Timings.Add(t);
        else
            _ui.Post(_ => Timings.Add(t), null);
    }

    // --- polecenia -----------------------------------------------------------------------------

    /// <summary>Wczytuje listę przebiegów i kolejkę domyślnego modelu (pierwszy przebieg, OCSVM per rola).</summary>
    [RelayCommand]
    private async Task InitializeAsync()
    {
        try
        {
            IsBusy = true;
            var runs = await _repository.GetRunsAsync();
            _suspendReload = true;
            Runs.Clear();
            foreach (var r in runs)
                Runs.Add(r);
            SelectedRun = Runs.FirstOrDefault(r => r.Split == "test" && r.Config == "full") ?? Runs.FirstOrDefault();
        }
        catch (Exception ex)
        {
            Fail("Nie udało się połączyć z bazą wyników", ex);
            return;
        }
        finally
        {
            _suspendReload = false;
            IsBusy = false;
        }
        if (SelectedRun is null)
        {
            Status = "Baza wyników jest pusta: uruchom eksport (python -m anomaly.export.results_db).";
            return;
        }
        await LoadAlertsAsync();
    }

    /// <summary>Wczytuje alerty wybranego modelu (jedno zapytanie SQL) i stosuje bieżące filtry.</summary>
    [RelayCommand]
    private async Task LoadAlertsAsync()
    {
        if (SelectedRun is not { } run)
            return;
        var version = ++_loadVersion;
        var sw = Stopwatch.StartNew();
        var sqlMs = 0.0;
        void Sum(QueryTiming t) { if (t.Operation.StartsWith("SQL", StringComparison.Ordinal)) sqlMs += t.Milliseconds; }
        _log.Recorded += Sum;
        try
        {
            IsBusy = true;
            LastError = null;
            if (!_modelsByRun.TryGetValue(run.RunId, out var models))
                _modelsByRun[run.RunId] = models = await _repository.GetModelsAsync(run.RunId);
            var model = models.FirstOrDefault(m => m.Variant == SelectedVariant && m.Detector == SelectedDetector);
            if (model is null)
            {
                if (version == _loadVersion)
                    SetQueue(null, [], []);
                Status = $"Brak modelu {Labels.Detector(SelectedDetector)} / {Labels.Variant(SelectedVariant)} w {run.Label}.";
                return;
            }
            var thresholds = await _repository.GetThresholdsAsync(model.ModelId);
            var alerts = await _repository.GetAlertsAsync(model.ModelId);
            if (version != _loadVersion)
                return;                                   // nowszy wybór już się ładuje
            SetQueue(model, thresholds, alerts);
            sw.Stop();
            LastLoadMs = sw.Elapsed.TotalMilliseconds;
            LastSqlMs = sqlMs;
            _log.Record("Panel: ładowanie kolejki", sw.Elapsed, alerts.Count);
            Status = $"{run.Label}, {model.Label}: {alerts.Count} alertów; ładowanie {LastLoadMs:0} ms " +
                     $"(SQL {LastSqlMs:0} ms), filtrowanie {LastFilterMs:0.0} ms.";
        }
        catch (Exception ex) when (version == _loadVersion)
        {
            Fail("Nie udało się wczytać alertów", ex);
        }
        finally
        {
            _log.Recorded -= Sum;
            if (version == _loadVersion)
                IsBusy = false;
        }
    }

    private void SetQueue(ModelInfo? model, IReadOnlyList<ThresholdInfo> thresholds, IReadOnlyList<AlertRow> alerts)
    {
        _suspendFilter = true;
        try
        {
            SelectedAlert = null;
            CurrentModel = model;
            Thresholds = thresholds;
            _all = alerts.ToList();
            TotalAlerts = _all.Count;
            Rebuild(UserOptions, _all.Select(a => a.UserId), "(wszyscy)");
            Rebuild(RoleOptions, _all.Select(a => a.RoleId), "(wszystkie)");
            if (UserOptions.All(o => o.Value != SelectedUser)) SelectedUser = All;
            if (RoleOptions.All(o => o.Value != SelectedRole)) SelectedRole = All;
        }
        finally
        {
            _suspendFilter = false;
        }
        ApplyFilter();
    }

    private static void Rebuild(ObservableCollection<Option<string>> target, IEnumerable<string> values, string allLabel)
    {
        target.Clear();
        target.Add(new Option<string>(All, allLabel));
        foreach (var v in values.Distinct().Order(StringComparer.Ordinal))
            target.Add(new Option<string>(v, v));
    }

    /// <summary>Filtruje i sortuje wczytane alerty; zachowuje wybór, jeśli alert nadal jest w kolejce.</summary>
    public void ApplyFilter()
    {
        if (_suspendFilter)
            return;
        var sw = Stopwatch.StartNew();
        var keep = SelectedAlert;
        var list = AlertQuery.Apply(_all, CurrentFilter, SelectedSort);
        Alerts = new ObservableCollection<AlertRow>(list);
        LastFilterMs = sw.Elapsed.TotalMilliseconds;
        if (keep is not null && !list.Contains(keep))
            SelectedAlert = null;
    }

    [RelayCommand]
    private void ClearFilters()
    {
        _suspendFilter = true;
        try
        {
            SelectedUser = All;
            SelectedRole = All;
            DateFrom = null;
            DateTo = null;
            SelectedClass = All;
            MinRiskLevel = RiskLevel.Low;
        }
        finally
        {
            _suspendFilter = false;
        }
        ApplyFilter();
    }

    private string DefaultName(string ext) =>
        $"alerty_s{SelectedRun?.Seed}_{CurrentModel?.Variant}_{CurrentModel?.Detector}_{DateTime.Now:yyyyMMdd_HHmm}.{ext}";

    [RelayCommand]
    private void ExportCsv()
    {
        if (CurrentModel is null) return;
        var path = _files.SaveFile("Eksport kolejki alertów (CSV)", "CSV (*.csv)|*.csv", DefaultName("csv"));
        if (path is null) return;
        if (Measure("Eksport CSV", Alerts.Count, () => CsvExporter.WriteFile(path, Alerts, CurrentModel)))
            Status = $"Zapisano {Alerts.Count} alertów: {path}";
    }

    [RelayCommand]
    private void ExportPdf()
    {
        if (CurrentModel is null || SelectedRun is null) return;
        var path = _files.SaveFile("Raport alertów (PDF)", "PDF (*.pdf)|*.pdf", DefaultName("pdf"));
        if (path is null) return;
        var content = new ReportContent
        {
            Run = SelectedRun, Model = CurrentModel, Alerts = Alerts.ToList(), Sort = SelectedSort,
            FilterDescription = CurrentFilter.Describe(),
            Detail = Detail.Current?.Alert == SelectedAlert ? Detail.Current : null,
        };
        if (Measure("Eksport PDF", Alerts.Count, () => PdfReportExporter.Save(path, content)))
            Status = $"Zapisano raport PDF: {path}";
    }

    [RelayCommand]
    private void ExportTimings()
    {
        var path = _files.SaveFile("Eksport pomiarów czasu (CSV)", "CSV (*.csv)|*.csv", $"pomiary_{DateTime.Now:yyyyMMdd_HHmm}.csv");
        if (path is null) return;
        CsvExporter.WriteTimings(path, _log.Snapshot());
        Status = $"Zapisano pomiary: {path}";
    }

    private bool Measure(string operation, int rows, Action action)
    {
        try
        {
            var sw = Stopwatch.StartNew();
            action();
            _log.Record(operation, sw.Elapsed, rows);
            return true;
        }
        catch (Exception ex)
        {
            Fail(operation + " nie powiódł się", ex);
            return false;
        }
    }

    private void Fail(string what, Exception ex)
    {
        LastError = $"{what}: {ex.Message}";
        Status = LastError;
    }
}
