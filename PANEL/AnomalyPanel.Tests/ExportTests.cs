using AnomalyPanel.Core.Export;
using AnomalyPanel.Core.Filtering;
using AnomalyPanel.Core.Models;
using static AnomalyPanel.Tests.TestData;

namespace AnomalyPanel.Tests;

public class ExportTests
{
    [Fact]
    public void CsvHasHeaderInvariantNumbersAndEscaping()
    {
        var a = Alert(7, risk: 0.8125, anomaly: true, classes: "unusual_ratio;volume_spike", hardNormal: "");
        a.Rank = 1;
        var w = new StringWriter();
        CsvExporter.Write(w, [a], Model(1, "role", "ocsvm"));
        var lines = w.ToString().TrimEnd().Split(Environment.NewLine);
        Assert.Equal(string.Join(",", CsvExporter.Header), lines[0]);
        Assert.Contains(",0.8125,", lines[1]);
        Assert.Contains(",role,ocsvm,", lines[1]);
        Assert.Contains(",unusual_ratio;volume_spike,", lines[1]);
        Assert.Equal("\"a,\"\"b\"\"\"", CsvExporter.Escape("a,\"b\""));
    }

    [Fact]
    public void PdfReportIsGeneratedWithAndWithoutDetail()
    {
        var alerts = AlertQuery.Apply(Enumerable.Range(1, 40).Select(i => Alert(i, minutes: 30 * i, risk: 0.6 + i / 100.0)),
                                      AlertFilter.None, SortMode.RiskScore);
        var content = new ReportContent
        {
            Run = Run(), Model = Model(1, "role", "ocsvm"), Alerts = alerts, FilterDescription = "bez filtrów",
        };
        var plain = PdfReportExporter.Generate(content);
        Assert.Equal("%PDF", System.Text.Encoding.ASCII.GetString(plain, 0, 4));

        var detail = new AlertDetail
        {
            Alert = alerts[0], Model = content.Model, Run = content.Run,
            Features = [new FeatureDeviation { Feature = "operation_count", Value = 40, RobustZ = 9.5, UnitP99 = 20 }],
            Events = [new XeEvent { EventUid = "e1", SqlText = "SELECT TOP 10 * FROM SalesLT.Customer", OperationType = "SELECT" }],
        };
        var withDetail = PdfReportExporter.Generate(new ReportContent
        {
            Run = content.Run, Model = content.Model, Alerts = alerts, FilterDescription = "bez filtrów", Detail = detail,
        });
        Assert.True(withDetail.Length > plain.Length);
    }
}
