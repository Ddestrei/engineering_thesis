using System.Globalization;
using System.Text;
using AnomalyPanel.Core.Models;

namespace AnomalyPanel.Core.Export;

/// <summary>Eksport kolejki alertów do CSV (RFC 4180: przecinek, kropka dziesiętna, UTF-8), np. do pandas lub arkusza.</summary>
public static class CsvExporter
{
    public static readonly string[] Header =
    [
        "rank", "alert_id", "window_start", "user_id", "role_id", "variant", "detector", "risk_score", "risk_level",
        "norm_score", "tail_excess", "raw_score", "threshold_group", "main_threshold", "severity_weight",
        "context_weight", "operation_count", "ground_truth_is_anomaly", "ground_truth_classes", "incident_ids",
        "hard_normal",
    ];

    public static void Write(TextWriter writer, IEnumerable<AlertRow> alerts, ModelInfo? model)
    {
        var inv = CultureInfo.InvariantCulture;
        writer.WriteLine(string.Join(",", Header));
        foreach (var a in alerts)
        {
            string[] fields =
            [
                a.Rank.ToString(inv), a.AlertId.ToString(inv), a.WindowStart.ToString("yyyy-MM-dd HH:mm:ss", inv),
                a.UserId, a.RoleId, model?.Variant ?? "", model?.Detector ?? "", a.RiskScore.ToString("R", inv),
                a.RiskLevelLabel, a.NormScore.ToString("R", inv), a.TailExcess.ToString("R", inv),
                a.RawScore.ToString("R", inv), a.ThresholdGroup, a.MainThreshold.ToString("R", inv),
                a.SeverityWeight.ToString("R", inv), a.ContextWeight.ToString("R", inv),
                a.OperationCount.ToString(inv), a.IsAnomaly ? "1" : "0", a.AnomalyClasses, a.IncidentIds, a.HardNormal,
            ];
            writer.WriteLine(string.Join(",", fields.Select(Escape)));
        }
    }

    public static void WriteFile(string path, IEnumerable<AlertRow> alerts, ModelInfo? model)
    {
        using var w = new StreamWriter(path, false, new UTF8Encoding(encoderShouldEmitUTF8Identifier: true));
        Write(w, alerts, model);
    }

    public static void WriteTimings(string path, IEnumerable<QueryTiming> timings)
    {
        var inv = CultureInfo.InvariantCulture;
        using var w = new StreamWriter(path, false, new UTF8Encoding(true));
        w.WriteLine("at,operation,milliseconds,rows");
        foreach (var t in timings)
            w.WriteLine(string.Join(",", Escape(t.At.ToString("yyyy-MM-dd HH:mm:ss.fff", inv)), Escape(t.Operation),
                                    t.Milliseconds.ToString("0.###", inv), t.Rows.ToString(inv)));
    }

    internal static string Escape(string s) =>
        s.IndexOfAny([',', '"', '\n', '\r']) >= 0 ? "\"" + s.Replace("\"", "\"\"") + "\"" : s;
}
