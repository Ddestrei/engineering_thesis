using System.Diagnostics;
using AnomalyPanel.Core.Data;
using AnomalyPanel.Core.Filtering;
using AnomalyPanel.Core.ViewModels;
using Dapper;
using Microsoft.Data.SqlClient;
using Xunit.Abstractions;

namespace AnomalyPanel.Tests;

/// <summary>
/// Testy na prawdziwej bazie AnomalyResults (po eksporcie z Pythona): zgodność panelu z bazą i czasy zapytań SQL.
/// Wymagają SQL Server; pominięcie: dotnet test --filter "Category!=Integration".
/// </summary>
[Trait("Category", "Integration")]
public class SqlIntegrationTests(ITestOutputHelper output)
{
    private static readonly string Cs = PanelSettings.Load().ConnectionString;

    [Fact]
    public async Task EveryModelQueueMatchesDatabaseCounts()
    {
        var log = new PerformanceLog();
        var repo = new SqlAnomalyRepository(Cs, log);
        var runs = await repo.GetRunsAsync();
        Assert.NotEmpty(runs);
        var checkedModels = 0;
        foreach (var run in runs)
        {
            foreach (var model in await repo.GetModelsAsync(run.RunId))
            {
                var alerts = await repo.GetAlertsAsync(model.ModelId);
                Assert.Equal(model.AlertCount, alerts.Count);
                Assert.All(alerts, a => Assert.True(a.NormScore >= a.MainThreshold));   // alert = alarm przy progu głównym
                checkedModels++;
            }
        }
        var sql = log.Snapshot().Where(t => t.Operation == "SQL: alerty").Select(t => t.Milliseconds).Order().ToList();
        output.WriteLine($"{checkedModels} modeli; zapytanie o alerty: mediana {sql[sql.Count / 2]:0.0} ms, max {sql[^1]:0.0} ms");
        Assert.True(sql[sql.Count / 2] < 500);
    }

    [Fact]
    public async Task InMemoryFiltersAgreeWithSqlWhere()
    {
        var repo = new SqlAnomalyRepository(Cs, new PerformanceLog());
        var run = (await repo.GetRunsAsync()).First(r => r.Split == "test");
        var model = (await repo.GetModelsAsync(run.RunId)).Single(m => m.Variant == "role" && m.Detector == "ocsvm");
        var alerts = await repo.GetAlertsAsync(model.ModelId);
        await using var conn = new SqlConnection(Cs);

        foreach (var role in alerts.Select(a => a.RoleId).Distinct())
        {
            var expected = await conn.ExecuteScalarAsync<int>("""
                SELECT COUNT(*) FROM dbo.Alerts a JOIN dbo.Windows w ON w.WindowId = a.WindowId
                WHERE a.ModelId = @m AND w.RoleId = @role AND w.IsAnomaly = 1 AND a.RiskScore >= 0.8
                """, new { m = model.ModelId, role });
            var f = new AlertFilter(RoleId: role, AnomalyClass: ClassFilter.AnyAnomaly, MinRiskLevel: Core.Models.RiskLevel.High);
            Assert.Equal(expected, AlertQuery.Apply(alerts, f, SortMode.RiskScore).Count);
        }
        var volume = await conn.ExecuteScalarAsync<int>("""
            SELECT COUNT(*) FROM dbo.Alerts a JOIN dbo.Windows w ON w.WindowId = a.WindowId
            WHERE a.ModelId = @m AND ';' + w.AnomalyClasses + ';' LIKE '%;volume_spike;%'
            """, new { m = model.ModelId });
        Assert.Equal(volume, AlertQuery.Apply(alerts, new AlertFilter(AnomalyClass: "volume_spike"), SortMode.RiskScore).Count);
    }

    [Fact]
    public async Task AlertPathReachesXeEventsAndSqlText()
    {
        var log = new PerformanceLog();
        var vm = new MainViewModel(new SqlAnomalyRepository(Cs, log), log, new FakeFileDialog(null));
        var sw = Stopwatch.StartNew();
        await vm.InitializeCommand.ExecuteAsync(null);
        output.WriteLine($"Start do pierwszej kolejki: {sw.Elapsed.TotalMilliseconds:0} ms ({vm.TotalAlerts} alertów, SQL {vm.LastSqlMs:0} ms)");
        Assert.Null(vm.LastError);
        Assert.NotEmpty(vm.Alerts);

        vm.SelectedAlert = vm.Alerts[0];
        await vm.DetailTask;
        var d = vm.Detail.Current!;
        Assert.Equal(vm.CurrentModel!.FeatureCount, d.Features.Count);
        Assert.NotEmpty(d.TopDeviations());
        Assert.Equal(vm.Alerts[0].OperationCount, d.Events.Count);                 // okno = wszystkie zdarzenia XE użytkownika
        Assert.All(d.Events, e => Assert.False(string.IsNullOrWhiteSpace(e.SqlText)));
        Assert.All(d.Events, e => Assert.InRange(e.SimTimestamp, vm.Alerts[0].WindowStart, vm.Alerts[0].WindowEnd));
        var detailMs = log.Snapshot().Last(t => t.Operation == "Panel: szczegóły alertu").Milliseconds;
        output.WriteLine($"Szczegóły alertu (cechy + zdarzenia + incydenty): {detailMs:0} ms");
        Assert.True(detailMs < 2000);
    }
}
