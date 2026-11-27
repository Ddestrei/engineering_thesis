using System.Diagnostics;
using AnomalyPanel.Core.Data;
using AnomalyPanel.Core.Filtering;
using AnomalyPanel.Core.Models;
using AnomalyPanel.Core.ViewModels;
using Xunit.Abstractions;
using static AnomalyPanel.Tests.TestData;

namespace AnomalyPanel.Tests;

/// <summary>
/// Czas ładowania i filtrowania dla N alertów oraz stabilność przy wielokrotnym przeładowaniu.
/// Progi są celowo luźne (rząd wielkości, nie benchmark): największa kolejka modelu w TEST ma ok. 130 alertów,
/// więc 50 000 to zapas ponad 300×.
/// </summary>
public class PerformanceTests(ITestOutputHelper output)
{
    private static List<AlertRow> Synthetic(int n)
    {
        var rng = new Random(42);
        string[] roles = ["CEO", "CFO", "MKT", "MKT_MGR", "SALES", "SALES_MGR"];
        string[] classes = ["", "volume_spike", "unusual_time", "unusual_ratio", "rare_resource_access"];
        return Enumerable.Range(0, n).Select(i =>
        {
            var c = classes[rng.Next(classes.Length)];
            return Alert(i, $"U{rng.Next(30):00}", roles[rng.Next(roles.Length)], minutes: 30 * rng.Next(2000),
                         risk: 0.6 + 0.4 * rng.NextDouble(), norm: 0.95 + 0.05 * rng.NextDouble(),
                         anomaly: c.Length > 0, classes: c);
        }).ToList();
    }

    [Theory]
    [InlineData(1_000)]
    [InlineData(10_000)]
    [InlineData(50_000)]
    public void FilterAndSortScale(int n)
    {
        var alerts = Synthetic(n);
        var filter = new AlertFilter(RoleId: "SALES", AnomalyClass: ClassFilter.AnyAnomaly, MinRiskLevel: RiskLevel.Medium);
        AlertQuery.Apply(alerts, filter, SortMode.RiskScore);                     // rozgrzewka JIT
        var sw = Stopwatch.StartNew();
        var r = AlertQuery.Apply(alerts, filter, SortMode.RiskScore);
        sw.Stop();
        output.WriteLine($"N = {n}: filtrowanie + sortowanie {sw.Elapsed.TotalMilliseconds:0.0} ms, wynik {r.Count}");
        Assert.Equal(alerts.Count(filter.Matches), r.Count);
        Assert.True(sw.Elapsed < TimeSpan.FromMilliseconds(500), $"{sw.Elapsed.TotalMilliseconds} ms");
    }

    [Theory]
    [InlineData(1_000)]
    [InlineData(10_000)]
    public async Task ViewModelLoadTimeForNAlerts(int n)
    {
        var log = new PerformanceLog();
        var repo = new FakeRepository(log);
        repo.Models.Add(Model(1, "role", "ocsvm"));
        repo.AlertsByModel[1] = Synthetic(n);
        var vm = new MainViewModel(repo, log, new FakeFileDialog(null));
        await vm.InitializeCommand.ExecuteAsync(null);
        output.WriteLine($"N = {n}: ładowanie kolejki w ViewModelu {vm.LastLoadMs:0.0} ms (filtrowanie {vm.LastFilterMs:0.0} ms)");
        Assert.Equal(n, vm.Alerts.Count);
        Assert.True(vm.LastLoadMs < 2000);
    }

    [Fact]
    public async Task StabilityUnderRepeatedReloadFilterAndSelection()
    {
        var log = new PerformanceLog();
        var repo = new FakeRepository(log);
        repo.Models.AddRange([Model(1, "role", "ocsvm"), Model(2, "user", "lof")]);
        repo.AlertsByModel[1] = Synthetic(2_000);
        repo.AlertsByModel[2] = Synthetic(1_500);
        repo.Features.Add(new FeatureDeviation { Feature = "operation_count", RobustZ = 3 });
        var vm = new MainViewModel(repo, log, new FakeFileDialog(null));
        await vm.InitializeCommand.ExecuteAsync(null);

        GC.Collect();
        GC.WaitForPendingFinalizers();
        var before = GC.GetTotalMemory(true);
        for (var i = 0; i < 300; i++)
        {
            vm.SelectedVariant = i % 2 == 0 ? "user" : "role";
            vm.SelectedDetector = i % 2 == 0 ? "lof" : "ocsvm";
            await vm.LoadAlertsCommand.ExecuteAsync(null);
            vm.SelectedRole = i % 3 == 0 ? "SALES" : MainViewModel.All;
            vm.MinRiskLevel = (RiskLevel)(i % 4);
            vm.SelectedSort = (SortMode)(i % 3);
            if (vm.Alerts.Count > 0)
            {
                vm.SelectedAlert = vm.Alerts[i % vm.Alerts.Count];
                await vm.DetailTask;
            }
            Assert.Null(vm.LastError);
        }
        vm.Timings.Clear();
        GC.Collect();
        GC.WaitForPendingFinalizers();
        var growthMb = (GC.GetTotalMemory(true) - before) / 1024.0 / 1024.0;
        output.WriteLine($"300 cykli: przyrost pamięci zarządzanej {growthMb:0.0} MB, pomiarów {log.Snapshot().Count}");
        Assert.True(growthMb < 50, $"{growthMb:0.0} MB");
    }
}
