using AnomalyPanel.Core.Data;
using AnomalyPanel.Core.Filtering;
using AnomalyPanel.Core.Models;
using AnomalyPanel.Core.ViewModels;
using static AnomalyPanel.Tests.TestData;

namespace AnomalyPanel.Tests;

/// <summary>Testy jednostkowe ViewModeli na repozytorium w pamięci.</summary>
public class MainViewModelTests
{
    private static (MainViewModel vm, FakeRepository repo, PerformanceLog log) Create(string? savePath = null)
    {
        var log = new PerformanceLog();
        var repo = new FakeRepository(log);
        repo.Models.AddRange([Model(1, "role", "ocsvm"), Model(2, "global", "lof"), Model(3, "user", "rules")]);
        repo.AlertsByModel[1] =
        [
            Alert(1, "U1", "SALES", 0, risk: 0.71, anomaly: true, classes: "volume_spike", incidents: "INC-1"),
            Alert(2, "U2", "SALES", 30, risk: 0.93),
            Alert(3, "U3", "CFO", 60, risk: 0.82, anomaly: true, classes: "unusual_time"),
        ];
        repo.AlertsByModel[2] = [Alert(10, "U7", "MKT", 0, risk: 0.66)];
        return (new MainViewModel(repo, log, new FakeFileDialog(savePath)), repo, log);
    }

    [Fact]
    public async Task InitializeLoadsDefaultModelSortedByRiskScore()
    {
        var (vm, _, _) = Create();
        await vm.InitializeCommand.ExecuteAsync(null);
        Assert.Equal("ocsvm", vm.CurrentModel!.Detector);
        Assert.Equal("role", vm.CurrentModel.Variant);
        Assert.Equal(SortMode.RiskScore, vm.SelectedSort);
        Assert.Equal([2, 3, 1], vm.Alerts.Select(a => a.AlertId));
        Assert.Equal(3, vm.TotalAlerts);
        Assert.Equal(["", "U1", "U2", "U3"], vm.UserOptions.Select(o => o.Value));
        Assert.Equal(["", "CFO", "SALES"], vm.RoleOptions.Select(o => o.Value));
        Assert.NotNull(vm.LastLoadMs);
        Assert.Null(vm.LastError);
    }

    [Fact]
    public async Task FiltersUpdateQueueAndClearRestoresIt()
    {
        var (vm, _, _) = Create();
        await vm.InitializeCommand.ExecuteAsync(null);
        vm.SelectedRole = "SALES";
        Assert.Equal([2, 1], vm.Alerts.Select(a => a.AlertId));
        vm.SelectedClass = ClassFilter.AnyAnomaly;
        Assert.Equal([1], vm.Alerts.Select(a => a.AlertId));
        vm.MinRiskLevel = RiskLevel.High;
        Assert.Empty(vm.Alerts);
        vm.ClearFiltersCommand.Execute(null);
        Assert.Equal(3, vm.Alerts.Count);
        Assert.Equal(MainViewModel.All, vm.SelectedRole);
    }

    [Fact]
    public async Task SortModeChangesOrderWithoutNewQuery()
    {
        var (vm, repo, _) = Create();
        await vm.InitializeCommand.ExecuteAsync(null);
        var queries = repo.AlertQueries;
        vm.SelectedSort = SortMode.Chronological;
        Assert.Equal([1, 2, 3], vm.Alerts.Select(a => a.AlertId));
        Assert.Equal(queries, repo.AlertQueries);
    }

    [Fact]
    public async Task ChangingModelReloadsAndResetsMissingUserFilter()
    {
        var (vm, _, _) = Create();
        await vm.InitializeCommand.ExecuteAsync(null);
        vm.SelectedUser = "U1";
        vm.SelectedVariant = "global";
        vm.SelectedDetector = "lof";
        await vm.LoadAlertsCommand.ExecuteAsync(null);
        Assert.Equal(2, vm.CurrentModel!.ModelId);
        Assert.Equal(MainViewModel.All, vm.SelectedUser);       // U1 nie występuje w kolejce LOF
        Assert.Equal([10], vm.Alerts.Select(a => a.AlertId));
    }

    [Fact]
    public async Task MissingModelGivesEmptyQueueAndMessage()
    {
        var (vm, _, _) = Create();
        await vm.InitializeCommand.ExecuteAsync(null);
        vm.SelectedDetector = "iforest";
        await vm.LoadAlertsCommand.ExecuteAsync(null);
        Assert.Null(vm.CurrentModel);
        Assert.Empty(vm.Alerts);
        Assert.Contains("Brak modelu", vm.Status);
    }

    [Fact]
    public async Task SlowOlderLoadDoesNotOverwriteNewerSelection()
    {
        var (vm, repo, _) = Create();
        await vm.InitializeCommand.ExecuteAsync(null);
        repo.DelayByModel[1] = TimeSpan.FromMilliseconds(300);
        var slow = vm.LoadAlertsCommand.ExecuteAsync(null);    // model 1 (wolny)
        vm.SelectedVariant = "global";
        vm.SelectedDetector = "lof";
        await vm.LoadAlertsCommand.ExecuteAsync(null);          // model 2 (szybki)
        await slow;
        Assert.Equal(2, vm.CurrentModel!.ModelId);
        Assert.Equal([10], vm.Alerts.Select(a => a.AlertId));
    }

    [Fact]
    public async Task SelectingAlertLoadsDetailWithTopDeviationsAndEvents()
    {
        var (vm, repo, _) = Create();
        repo.Features.AddRange(
        [
            new FeatureDeviation { Feature = "operation_count", Value = 40, RobustZ = 9.5, UnitP01 = 1, UnitP99 = 20 },
            new FeatureDeviation { Feature = "is_weekend", Value = 0, RobustZ = null },
            new FeatureDeviation { Feature = "hour_rarity", Value = 1, RobustZ = 0.2, UnitP01 = 0, UnitP99 = 3 },
        ]);
        repo.Events.Add(new XeEvent { EventUid = "e1", SqlText = "SELECT 1" });
        repo.Incidents.Add(new IncidentInfo { IncidentId = "INC-1", AnomalyClass = "volume_spike" });
        await vm.InitializeCommand.ExecuteAsync(null);
        vm.SelectedAlert = vm.Alerts.Single(a => a.AlertId == 1);
        await vm.DetailTask;
        var d = vm.Detail;
        Assert.True(d.HasAlert);
        Assert.Equal(["operation_count", "hour_rarity"], d.TopFeatures.Select(f => f.Feature));
        Assert.True(d.TopFeatures[0].OutsideUnitRange);
        Assert.Equal("SELECT 1", d.SelectedSql);
        Assert.Equal("INC-1", Assert.Single(d.Incidents).IncidentId);
        Assert.Equal(0.95, d.Current!.Threshold!.MainThreshold);
    }

    [Fact]
    public async Task FilteringOutSelectedAlertClearsDetail()
    {
        var (vm, _, _) = Create();
        await vm.InitializeCommand.ExecuteAsync(null);
        vm.SelectedAlert = vm.Alerts.Single(a => a.AlertId == 3);
        await vm.DetailTask;
        vm.SelectedRole = "SALES";
        Assert.Null(vm.SelectedAlert);
        Assert.False(vm.Detail.HasAlert);
    }

    [Fact]
    public async Task RepositoryErrorIsReportedNotThrown()
    {
        var (vm, repo, _) = Create();
        repo.FailAlertsWith = new InvalidOperationException("brak połączenia");
        await vm.InitializeCommand.ExecuteAsync(null);
        Assert.Contains("brak połączenia", vm.LastError);
        Assert.Empty(vm.Alerts);
    }

    [Fact]
    public async Task TimingsAreRecordedForSqlAndViewLoad()
    {
        var (vm, _, log) = Create();
        await vm.InitializeCommand.ExecuteAsync(null);
        var ops = log.Snapshot().Select(t => t.Operation).ToList();
        Assert.Contains("SQL: alerty", ops);
        Assert.Contains("Panel: ładowanie kolejki", ops);
        Assert.Equal(ops.Count, vm.Timings.Count);
        Assert.True(vm.LastSqlMs > 0);
    }

    [Fact]
    public async Task ExportCsvWritesFilteredQueue()
    {
        var path = Path.Combine(Path.GetTempPath(), $"panel_test_{Guid.NewGuid():N}.csv");
        try
        {
            var (vm, _, log) = Create(path);
            await vm.InitializeCommand.ExecuteAsync(null);
            vm.SelectedRole = "SALES";
            vm.ExportCsvCommand.Execute(null);
            var lines = File.ReadAllLines(path);
            Assert.Equal(3, lines.Length);                         // nagłówek + 2 alerty
            Assert.StartsWith("1,2,", lines[1]);                   // rank 1 = alert 2 (RiskScore 0,93)
            Assert.Contains(log.Snapshot(), t => t.Operation == "Eksport CSV" && t.Rows == 2);
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public async Task ExportPdfWritesDocumentWithSelectedAlert()
    {
        var path = Path.Combine(Path.GetTempPath(), $"panel_test_{Guid.NewGuid():N}.pdf");
        try
        {
            var (vm, _, _) = Create(path);
            await vm.InitializeCommand.ExecuteAsync(null);
            vm.SelectedAlert = vm.Alerts[0];
            await vm.DetailTask;
            vm.ExportPdfCommand.Execute(null);
            Assert.Null(vm.LastError);
            var bytes = File.ReadAllBytes(path);
            Assert.True(bytes.Length > 1000);
            Assert.Equal("%PDF", System.Text.Encoding.ASCII.GetString(bytes, 0, 4));
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public async Task CancelledSaveDialogWritesNothing()
    {
        var (vm, _, log) = Create(savePath: null);
        await vm.InitializeCommand.ExecuteAsync(null);
        vm.ExportCsvCommand.Execute(null);
        Assert.DoesNotContain(log.Snapshot(), t => t.Operation == "Eksport CSV");
    }
}
