namespace AnomalyPanel.Core.Models;

/// <summary>Alert = okno z alarmem przy głównym progu (D7) modelu; dbo.Alerts złączone z dbo.Windows.</summary>
public sealed class AlertRow
{
    public long AlertId { get; init; }
    public long WindowId { get; init; }
    public string UserId { get; init; } = "";
    public string RoleId { get; init; } = "";
    public DateTime WindowStart { get; init; }
    public int OperationCount { get; init; }

    public double RawScore { get; init; }
    public double NormScore { get; init; }
    public double TailExcess { get; init; }
    public double RiskScore { get; init; }
    public string ThresholdGroup { get; init; } = "";
    public double MainThreshold { get; init; }
    public bool AlarmRecall80 { get; init; }

    // Składniki RiskScore (D10).
    public double SeverityWeight { get; init; }
    public double ContextWeight { get; init; }
    public bool CtxNonPrimaryDevice { get; init; }
    public bool CtxAfterHours { get; init; }
    public bool CtxUnknownIp { get; init; }

    // Ground truth generatora: do oceny alertu, model jej nie widzi.
    public bool IsAnomaly { get; init; }
    public string IncidentIds { get; init; } = "";
    public string AnomalyClasses { get; init; } = "";
    public string Intensities { get; init; } = "";
    public int Severity { get; init; }
    public string HardNormal { get; init; } = "";

    /// <summary>Pozycja w bieżącej kolejce (po filtrach i sortowaniu).</summary>
    public int Rank { get; set; }

    public DateTime WindowEnd => WindowStart.AddMinutes(30);
    public string AlarmRecall80Text => AlarmRecall80 ? "tak" : "nie";
    public string ContextText
    {
        get
        {
            var items = new List<string>();
            if (CtxNonPrimaryDevice) items.Add("inne niż podstawowe urządzenie");
            if (CtxAfterHours) items.Add("poza typowymi godzinami");
            if (CtxUnknownIp) items.Add("nieznany adres IP");
            return items.Count == 0 ? "typowy kontekst" : string.Join(", ", items);
        }
    }
    public RiskLevel RiskLevel => RiskLevels.FromScore(RiskScore);
    public string RiskLevelLabel => RiskLevels.Label(RiskLevel);
    public IReadOnlyList<string> Classes => Split(AnomalyClasses);
    public IReadOnlyList<string> Incidents => Split(IncidentIds);

    public string GroundTruth => IsAnomaly
        ? "anomalia: " + string.Join(", ", Classes.Select(Labels.AnomalyClass))
        : HardNormal.Length > 0 ? "norma (trudna: " + HardNormal.Replace("HARD_NORMAL_", "").Replace(";", ", ") + ")" : "norma";

    /// <summary>Wkład składników do RiskScore = 0,6·norm + 0,25·severity + 0,15·kontekst.</summary>
    public double RiskFromScore => RiskWeights.Anomaly * NormScore;
    public double RiskFromSeverity => RiskWeights.Severity * SeverityWeight;
    public double RiskFromContext => RiskWeights.Context * ContextWeight;

    internal static IReadOnlyList<string> Split(string s) =>
        s.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
}

/// <summary>Wagi RiskScore z METODOLOGIA D10 (ustalone a priori).</summary>
public static class RiskWeights
{
    public const double Anomaly = 0.60;
    public const double Severity = 0.25;
    public const double Context = 0.15;
}
