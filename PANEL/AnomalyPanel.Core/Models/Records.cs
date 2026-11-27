namespace AnomalyPanel.Core.Models;

/// <summary>Przebieg: ziarno × split × konfiguracja (tabela dbo.Runs).</summary>
public sealed class RunInfo
{
    public int RunId { get; init; }
    public int Seed { get; init; }
    public string Split { get; init; } = "";
    public string Config { get; init; } = "";
    public string CodeCommit { get; init; } = "";
    public bool CodeDirty { get; init; }
    public string FrozenSha256 { get; init; } = "";
    public string EvaluationCommit { get; init; } = "";
    public DateTime ExportedAt { get; init; }
    public int WindowCount { get; init; }
    public int AnomalousWindows { get; init; }
    public int EventCount { get; init; }
    public string? XeJsonPath { get; init; }

    public string Label => $"ziarno {Seed} ({Split}/{Config})";
    public override string ToString() => Label;
}

/// <summary>Model: wariant × detektor w przebiegu (dbo.Models), z wynikami strojenia na VAL.</summary>
public sealed class ModelInfo
{
    public int ModelId { get; init; }
    public int RunId { get; init; }
    public string Variant { get; init; } = "";
    public string Detector { get; init; } = "";
    public string Hyperparameters { get; init; } = "{}";
    public int FeatureCount { get; init; }
    public double? ValPrAuc { get; init; }
    public double? ValMainFpr { get; init; }
    public double? ValMainRecall { get; init; }
    public double? ValMainMcc { get; init; }
    public int AlertCount { get; init; }

    public string Label => $"{Labels.Detector(Detector)} / {Labels.Variant(Variant)}";
}

/// <summary>Próg D7 grupy (ALL albo rola).</summary>
public sealed class ThresholdInfo
{
    public string GroupKey { get; init; } = "";
    public double MainThreshold { get; init; }
    public double Recall80Threshold { get; init; }
    public double? ValFpr { get; init; }
    public double? ValRecall { get; init; }
}

/// <summary>Cecha okna i jej porównanie z profilem normalnym z TRAIN (dbo.WindowFeatures).</summary>
public sealed class FeatureDeviation
{
    public string Feature { get; init; } = "";
    public string FeatureGroup { get; init; } = "";
    public double Value { get; init; }
    public double? RobustZ { get; init; }
    public double UnitMedian { get; init; }
    public double UnitP01 { get; init; }
    public double UnitP99 { get; init; }
    public double? UserMedian { get; init; }
    public bool? RuleViolated { get; init; }

    /// <summary>Wartość poza przedziałem [P1, P99] okien TRAIN jednostki modelu.</summary>
    public bool OutsideUnitRange => Value < UnitP01 || Value > UnitP99;
    public string UnitRange => $"[{UnitP01:0.###}; {UnitP99:0.###}]";
    public string RuleText => RuleViolated switch { true => "naruszona", false => "ok", _ => "" };
    public string GroupLabel => Labels.FeatureGroup(FeatureGroup);
}

/// <summary>Zdarzenie Extended Events okna (dbo.Events).</summary>
public sealed class XeEvent
{
    public string EventUid { get; init; } = "";
    public DateTime SimTimestamp { get; init; }
    public string PhysicalTimestamp { get; init; } = "";
    public string XeEventName { get; init; } = "";
    public long XeEventSequence { get; init; }
    public int XeSessionId { get; init; }
    public string XeConnectionId { get; init; } = "";
    public string LoginName { get; init; } = "";
    public string HostName { get; init; } = "";
    public string Device { get; init; } = "";
    public string Ip { get; init; } = "";
    public string OperationType { get; init; } = "";
    public string TargetTable { get; init; } = "";
    public string SqlText { get; init; } = "";
    public long DurationUs { get; init; }
    public long CpuTimeUs { get; init; }
    public long LogicalReads { get; init; }
    public long PhysicalReads { get; init; }
    public long Writes { get; init; }
    public long RowCount { get; init; }
    public string Result { get; init; } = "";
    public string GtIncidentId { get; init; } = "";
    public string GtAnomalyClass { get; init; } = "";
}

/// <summary>Incydent z ground truth generatora (dbo.Incidents).</summary>
public sealed class IncidentInfo
{
    public string IncidentId { get; init; } = "";
    public string AnomalyClass { get; init; } = "";
    public string Intensity { get; init; } = "";
    public string UserId { get; init; } = "";
    public string RoleId { get; init; } = "";
    public DateTime StartTime { get; init; }
    public DateTime EndTime { get; init; }
    public int SeverityLevel { get; init; }
    public int EventCount { get; init; }
    public string Tables { get; init; } = "";
    public string Operations { get; init; } = "";

    public string ClassLabel => Labels.AnomalyClass(AnomalyClass);
    public string IntensityLabel => Labels.Intensity(Intensity);
}

/// <summary>Pomiar czasu operacji (zapytanie SQL, ładowanie widoku, eksport).</summary>
public sealed record QueryTiming(DateTime At, string Operation, double Milliseconds, int Rows);
