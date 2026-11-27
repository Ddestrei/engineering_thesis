namespace AnomalyPanel.Core.Models;

/// <summary>Poziom ryzyka alertu wyznaczany z RiskScore stałymi progami.</summary>
public enum RiskLevel
{
    Low = 0,
    Medium = 1,
    High = 2,
    Critical = 3,
}

/// <summary>
/// Progi poziomów ryzyka ustalone przed obejrzeniem alertów w panelu: RiskScore alertu leży w [0,6; 1]
/// (norm ≥ próg ≈ 0,95), progi dzielą ten zakres mniej więcej na kwartyle alertów TEST.
/// </summary>
public static class RiskLevels
{
    public const double MediumFrom = 0.70;
    public const double HighFrom = 0.80;
    public const double CriticalFrom = 0.90;

    public static RiskLevel FromScore(double risk) => risk switch
    {
        >= CriticalFrom => RiskLevel.Critical,
        >= HighFrom => RiskLevel.High,
        >= MediumFrom => RiskLevel.Medium,
        _ => RiskLevel.Low,
    };

    public static string Label(RiskLevel level) => level switch
    {
        RiskLevel.Critical => "krytyczny",
        RiskLevel.High => "wysoki",
        RiskLevel.Medium => "średni",
        _ => "niski",
    };
}

/// <summary>Polskie etykiety nazw z bazy.</summary>
public static class Labels
{
    public static string Variant(string v) => v switch
    {
        "global" => "global",
        "role" => "per rola",
        "user" => "per użytkownik",
        _ => v,
    };

    public static string Detector(string d) => d switch
    {
        "rules" => "reguły (D13)",
        "robust" => "robust z-score (D12)",
        "iforest" => "Isolation Forest",
        "ocsvm" => "One-Class SVM",
        "lof" => "LOF",
        _ => d,
    };

    public static string AnomalyClass(string c) => c switch
    {
        "volume_spike" => "wzrost wolumenu",
        "unusual_time" => "nietypowa pora",
        "unusual_ratio" => "nietypowa proporcja operacji",
        "rare_resource_access" => "dostęp do rzadkiego zasobu",
        _ => c,
    };

    public static string Intensity(string i) => i switch
    {
        "strong" => "silna",
        "weak" => "słaba",
        _ => i,
    };

    public static string FeatureGroup(string g) => g switch
    {
        "time" => "czas",
        "volume" => "wolumen",
        "context" => "kontekst",
        "ratios" => "proporcje",
        "resources" => "zasoby",
        "execution" => "wykonanie",
        _ => g,
    };
}
