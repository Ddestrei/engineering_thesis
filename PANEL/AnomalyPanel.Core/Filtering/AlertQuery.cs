using AnomalyPanel.Core.Models;

namespace AnomalyPanel.Core.Filtering;

/// <summary>Sortowanie kolejki alertów (METODOLOGIA D21: remisy rozstrzygane chronologicznie).</summary>
public enum SortMode
{
    /// <summary>RiskScore malejąco (D10).</summary>
    RiskScore,
    /// <summary>Sam score detektora w kolejności D8: norm, potem nadwyżka ogona.</summary>
    DetectorScore,
    /// <summary>Kolejność napływu: początek okna rosnąco.</summary>
    Chronological,
}

/// <summary>Specjalne wartości filtra klasy (oprócz nazw klas anomalii).</summary>
public static class ClassFilter
{
    public const string AnyAnomaly = "__any_anomaly__";
    public const string Normal = "__normal__";
    public const string HardNormal = "__hard_normal__";
}

/// <summary>Filtr kolejki. Null = brak ograniczenia. Zakres dat obejmuje całe dni From i To.</summary>
public sealed record AlertFilter(
    string? UserId = null,
    string? RoleId = null,
    DateTime? From = null,
    DateTime? To = null,
    string? AnomalyClass = null,
    RiskLevel MinRiskLevel = RiskLevel.Low)
{
    public static AlertFilter None { get; } = new();

    public bool Matches(AlertRow a)
    {
        if (UserId is not null && a.UserId != UserId) return false;
        if (RoleId is not null && a.RoleId != RoleId) return false;
        if (From is { } from && a.WindowStart < from.Date) return false;
        if (To is { } to && a.WindowStart >= to.Date.AddDays(1)) return false;
        if (a.RiskLevel < MinRiskLevel) return false;
        return AnomalyClass switch
        {
            null => true,
            ClassFilter.AnyAnomaly => a.IsAnomaly,
            ClassFilter.Normal => !a.IsAnomaly,
            ClassFilter.HardNormal => !a.IsAnomaly && a.HardNormal.Length > 0,
            var c => a.Classes.Contains(c),
        };
    }

    public string Describe()
    {
        var parts = new List<string>();
        if (UserId is not null) parts.Add($"użytkownik {UserId}");
        if (RoleId is not null) parts.Add($"rola {RoleId}");
        if (From is not null || To is not null)
            parts.Add($"okna {From?.ToString("yyyy-MM-dd") ?? "…"} – {To?.ToString("yyyy-MM-dd") ?? "…"}");
        if (AnomalyClass is not null)
            parts.Add("klasa: " + AnomalyClass switch
            {
                ClassFilter.AnyAnomaly => "dowolna anomalia",
                ClassFilter.Normal => "norma (fałszywe alarmy)",
                ClassFilter.HardNormal => "trudna norma",
                var c => Labels.AnomalyClass(c),
            });
        if (MinRiskLevel > RiskLevel.Low) parts.Add($"ryzyko co najmniej {RiskLevels.Label(MinRiskLevel)}");
        return parts.Count == 0 ? "bez filtrów" : string.Join("; ", parts);
    }
}

public static class AlertQuery
{
    /// <summary>Filtruje, sortuje i numeruje kolejkę (Rank od 1).</summary>
    public static List<AlertRow> Apply(IEnumerable<AlertRow> alerts, AlertFilter filter, SortMode sort)
    {
        var result = Sort(alerts.Where(filter.Matches), sort).ToList();
        for (var i = 0; i < result.Count; i++)
            result[i].Rank = i + 1;
        return result;
    }

    public static IEnumerable<AlertRow> Sort(IEnumerable<AlertRow> alerts, SortMode sort)
    {
        var ordered = sort switch
        {
            SortMode.RiskScore => alerts.OrderByDescending(a => a.RiskScore),
            SortMode.DetectorScore => alerts.OrderByDescending(a => a.NormScore).ThenByDescending(a => a.TailExcess),
            _ => alerts.OrderBy(_ => 0),
        };
        // Remisy: chronologicznie, potem użytkownik (deterministycznie, jak w metrics.ranking_orders).
        return ordered.ThenBy(a => a.WindowStart).ThenBy(a => a.UserId, StringComparer.Ordinal);
    }
}
