using System.Globalization;
using AnomalyPanel.Core.Filtering;
using AnomalyPanel.Core.Models;
using QuestPDF.Fluent;
using QuestPDF.Helpers;
using QuestPDF.Infrastructure;

namespace AnomalyPanel.Core.Export;

/// <summary>Zawartość raportu: bieżąca kolejka (po filtrach) i opcjonalnie szczegóły wybranego alertu.</summary>
public sealed class ReportContent
{
    public required RunInfo Run { get; init; }
    public required ModelInfo Model { get; init; }
    public required IReadOnlyList<AlertRow> Alerts { get; init; }
    public required string FilterDescription { get; init; }
    public SortMode Sort { get; init; } = SortMode.RiskScore;
    public AlertDetail? Detail { get; init; }
    public DateTime GeneratedAt { get; init; } = DateTime.Now;
    public int MaxAlertRows { get; init; } = 500;
    public int MaxEventRows { get; init; } = 60;
}

/// <summary>Raport PDF (QuestPDF, licencja Community).</summary>
public static class PdfReportExporter
{
    private static readonly CultureInfo Pl = CultureInfo.GetCultureInfo("pl-PL");

    static PdfReportExporter()
    {
        QuestPDF.Settings.License = LicenseType.Community;
    }

    public static byte[] Generate(ReportContent r) => Build(r).GeneratePdf();

    public static void Save(string path, ReportContent r) => Build(r).GeneratePdf(path);

    private static string Short(string s, int n) => s.Length <= n ? s : s[..n];

    private static string F(double v, string fmt = "0.000") => v.ToString(fmt, Pl);
    private static string F(double? v, string fmt = "0.000") => v is { } x ? x.ToString(fmt, Pl) : "–";

    private static string SortLabel(SortMode s) => s switch
    {
        SortMode.RiskScore => "RiskScore malejąco",
        SortMode.DetectorScore => "score detektora (D8) malejąco",
        _ => "chronologicznie",
    };

    private static IContainer Cell(IContainer c) =>
        c.BorderBottom(0.5f).BorderColor(Colors.Grey.Lighten2).PaddingVertical(2).PaddingHorizontal(3);

    private static IContainer HeaderCell(IContainer c) =>
        c.Background(Colors.Grey.Lighten3).PaddingVertical(3).PaddingHorizontal(3);

    private static Document Build(ReportContent r) => Document.Create(doc => doc.Page(page =>
    {
        page.Size(PageSizes.A4.Landscape());
        page.Margin(1.2f, Unit.Centimetre);
        page.DefaultTextStyle(t => t.FontSize(8.5f));
        page.Header().Column(h =>
        {
            h.Item().Text("Raport alertów – system wykrywania anomalii w operacjach na bazie danych").SemiBold().FontSize(13);
            h.Item().Text($"{r.Run.Label} · {r.Model.Label} · wygenerowano {r.GeneratedAt:yyyy-MM-dd HH:mm}")
                    .FontColor(Colors.Grey.Darken2);
        });
        page.Content().PaddingTop(8).Column(col =>
        {
            col.Spacing(6);
            col.Item().Text(t =>
            {
                t.Span("Model: ").SemiBold();
                t.Span($"{r.Model.Label}, hiperparametry {r.Model.Hyperparameters}, {r.Model.FeatureCount} cech. ");
                t.Span("Strojenie na VAL: ").SemiBold();
                t.Span($"PR-AUC {F(r.Model.ValPrAuc)}, FPR {F(r.Model.ValMainFpr)}, recall {F(r.Model.ValMainRecall)}, " +
                       $"MCC {F(r.Model.ValMainMcc)}.");
            });
            col.Item().Text(t =>
            {
                t.Span("Wersja: ").SemiBold();
                t.Span($"zamrożona konfiguracja {Short(r.Run.FrozenSha256, 12)}…, kod {Short(r.Run.CodeCommit, 7)}" +
                       $"{(r.Run.CodeDirty ? " (dirty)" : "")}, ewaluacja {Short(r.Run.EvaluationCommit, 7)}.");
            });
            col.Item().Text(t =>
            {
                t.Span("Kolejka: ").SemiBold();
                t.Span($"{r.Alerts.Count} alertów ({r.FilterDescription}); sortowanie: {SortLabel(r.Sort)}. " +
                       $"Etykiety ground truth: {r.Alerts.Count(a => a.IsAnomaly)} anomalii, " +
                       $"{r.Alerts.Count(a => !a.IsAnomaly)} okien normalnych.");
            });

            col.Item().Table(table =>
            {
                table.ColumnsDefinition(c =>
                {
                    c.ConstantColumn(28); c.ConstantColumn(78); c.RelativeColumn(1.1f); c.RelativeColumn(0.9f);
                    c.ConstantColumn(44); c.ConstantColumn(52); c.ConstantColumn(44); c.ConstantColumn(44);
                    c.ConstantColumn(44); c.RelativeColumn(2.4f);
                });
                table.Header(h =>
                {
                    foreach (var name in new[] { "#", "Okno", "Użytkownik", "Rola", "Risk", "Poziom", "Norm", "Severity",
                                                 "Kontekst", "Ground truth" })
                        h.Cell().Element(HeaderCell).Text(name).SemiBold();
                });
                foreach (var a in r.Alerts.Take(r.MaxAlertRows))
                {
                    table.Cell().Element(Cell).Text(a.Rank.ToString(Pl));
                    table.Cell().Element(Cell).Text(a.WindowStart.ToString("yyyy-MM-dd HH:mm", Pl));
                    table.Cell().Element(Cell).Text(a.UserId);
                    table.Cell().Element(Cell).Text(a.RoleId);
                    table.Cell().Element(Cell).Text(F(a.RiskScore));
                    table.Cell().Element(Cell).Text(a.RiskLevelLabel);
                    table.Cell().Element(Cell).Text(F(a.NormScore));
                    table.Cell().Element(Cell).Text(F(a.SeverityWeight, "0.00"));
                    table.Cell().Element(Cell).Text(F(a.ContextWeight, "0.00"));
                    table.Cell().Element(Cell).Text(a.GroundTruth);
                }
            });
            if (r.Alerts.Count > r.MaxAlertRows)
                col.Item().Text($"(pokazano {r.MaxAlertRows} z {r.Alerts.Count}; pełna lista w eksporcie CSV)").Italic();

            if (r.Detail is { } d)
                DetailSection(col, d, r.MaxEventRows);
        });
        page.Footer().AlignCenter().Text(t =>
        {
            t.Span("Strona ");
            t.CurrentPageNumber();
            t.Span(" / ");
            t.TotalPages();
        });
    }));

    private static void DetailSection(ColumnDescriptor col, AlertDetail d, int maxEvents)
    {
        var a = d.Alert;
        col.Item().PageBreak();
        col.Item().Text($"Szczegóły alertu #{a.Rank}: {a.UserId} ({a.RoleId}), okno {a.WindowStart:yyyy-MM-dd HH:mm}–{a.WindowEnd:HH:mm}")
           .SemiBold().FontSize(11);
        col.Item().Text($"RiskScore {F(a.RiskScore)} = 0,6·norm {F(a.NormScore)} ({F(a.RiskFromScore)}) + 0,25·severity " +
                        $"{F(a.SeverityWeight, "0.00")} ({F(a.RiskFromSeverity)}) + 0,15·kontekst {F(a.ContextWeight, "0.00")} " +
                        $"({F(a.RiskFromContext)}). Próg główny grupy {a.ThresholdGroup}: {F(a.MainThreshold, "0.0000")}; " +
                        $"surowy score {F(a.RawScore, "0.0000")}.");
        col.Item().Text($"Ground truth: {a.GroundTruth}" +
                        (d.Incidents.Count > 0 ? "; incydenty: " + string.Join(", ", d.Incidents.Select(i =>
                            $"{i.IncidentId} ({i.ClassLabel}, intensywność {i.IntensityLabel}, severity {i.SeverityLevel})")) : ""));

        col.Item().Text("Cechy najbardziej odbiegające od profilu normalnego (|z| robust, D12)").SemiBold();
        col.Item().Table(table =>
        {
            table.ColumnsDefinition(c =>
            {
                c.RelativeColumn(1.6f); c.RelativeColumn(0.8f); c.ConstantColumn(60); c.ConstantColumn(60);
                c.RelativeColumn(1.2f); c.ConstantColumn(60); c.ConstantColumn(48); c.ConstantColumn(50);
            });
            table.Header(h =>
            {
                foreach (var name in new[] { "Cecha", "Grupa", "Wartość", "Mediana jedn.", "[P1; P99] jedn.", "Mediana użytk.",
                                             "|z|", "Reguła" })
                    h.Cell().Element(HeaderCell).Text(name).SemiBold();
            });
            foreach (var f in d.TopDeviations(10))
            {
                table.Cell().Element(Cell).Text(f.Feature);
                table.Cell().Element(Cell).Text(Labels.FeatureGroup(f.FeatureGroup));
                table.Cell().Element(Cell).Text(F(f.Value, "0.###"));
                table.Cell().Element(Cell).Text(F(f.UnitMedian, "0.###"));
                table.Cell().Element(Cell).Text(f.UnitRange);
                table.Cell().Element(Cell).Text(F(f.UserMedian, "0.###"));
                table.Cell().Element(Cell).Text(F(f.RobustZ, "0.00"));
                table.Cell().Element(Cell).Text(f.RuleViolated switch { true => "naruszona", false => "ok", _ => "–" });
            }
        });

        col.Item().Text($"Zdarzenia XE okna ({d.Events.Count})").SemiBold();
        col.Item().Table(table =>
        {
            table.ColumnsDefinition(c =>
            {
                c.ConstantColumn(62); c.ConstantColumn(44); c.RelativeColumn(1f); c.ConstantColumn(50);
                c.ConstantColumn(42); c.RelativeColumn(4f);
            });
            table.Header(h =>
            {
                foreach (var name in new[] { "Czas", "Operacja", "Tabela", "Czas [µs]", "Odczyty", "SQL" })
                    h.Cell().Element(HeaderCell).Text(name).SemiBold();
            });
            foreach (var e in d.Events.Take(maxEvents))
            {
                table.Cell().Element(Cell).Text(e.SimTimestamp.ToString("HH:mm:ss", Pl));
                table.Cell().Element(Cell).Text(e.OperationType);
                table.Cell().Element(Cell).Text(e.TargetTable);
                table.Cell().Element(Cell).Text(e.DurationUs.ToString(Pl));
                table.Cell().Element(Cell).Text(e.LogicalReads.ToString(Pl));
                table.Cell().Element(Cell).Text(e.SqlText).FontFamily(Fonts.CourierNew).FontSize(7);
            }
        });
        if (d.Events.Count > maxEvents)
            col.Item().Text($"(pokazano {maxEvents} z {d.Events.Count} zdarzeń)").Italic();
    }
}
