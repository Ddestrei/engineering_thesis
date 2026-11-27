using AnomalyPanel.Core.Filtering;
using AnomalyPanel.Core.Models;
using static AnomalyPanel.Tests.TestData;

namespace AnomalyPanel.Tests;

/// <summary>Poprawność filtrowania i sortowania kolejki alertów.</summary>
public class AlertQueryTests
{
    private static readonly List<AlertRow> Queue =
    [
        Alert(1, "U1", "SALES", minutes: 0, risk: 0.65, norm: 0.99, anomaly: true, classes: "volume_spike"),
        Alert(2, "U2", "SALES", minutes: 30, risk: 0.91, norm: 0.96, hardNormal: "HARD_NORMAL_ALT_IP"),
        Alert(3, "U3", "CFO", minutes: 60 * 24, risk: 0.85, norm: 0.97, anomaly: true, classes: "unusual_ratio;volume_spike"),
        Alert(4, "U1", "SALES", minutes: 60 * 24 * 2, risk: 0.72, norm: 1.0, excess: 3.0, anomaly: true, classes: "unusual_time"),
        Alert(5, "U3", "CFO", minutes: 60 * 24 * 2 + 30, risk: 0.72, norm: 1.0, excess: 7.0),
    ];

    private static long[] Ids(IEnumerable<AlertRow> rows) => rows.Select(a => a.AlertId).ToArray();

    [Fact]
    public void NoFilterKeepsAllAndRanksFromOne()
    {
        var r = AlertQuery.Apply(Queue, AlertFilter.None, SortMode.RiskScore);
        Assert.Equal(5, r.Count);
        Assert.Equal([1, 2, 3, 4, 5], r.Select(a => a.Rank));
    }

    [Fact]
    public void FiltersByUserAndRole()
    {
        Assert.Equal([4, 1], Ids(AlertQuery.Apply(Queue, new AlertFilter(UserId: "U1"), SortMode.RiskScore)));
        Assert.Equal([3, 5], Ids(AlertQuery.Apply(Queue, new AlertFilter(RoleId: "CFO"), SortMode.RiskScore)));
        Assert.Empty(AlertQuery.Apply(Queue, new AlertFilter(UserId: "U1", RoleId: "CFO"), SortMode.RiskScore));
    }

    [Fact]
    public void DateRangeIncludesWholeDays()
    {
        var day1 = T0.Date.AddDays(1);
        Assert.Equal([3], Ids(AlertQuery.Apply(Queue, new AlertFilter(From: day1, To: day1), SortMode.Chronological)));
        Assert.Equal([3, 4, 5], Ids(AlertQuery.Apply(Queue, new AlertFilter(From: day1), SortMode.Chronological)));
        Assert.Equal([1, 2], Ids(AlertQuery.Apply(Queue, new AlertFilter(To: T0.Date.AddHours(23)), SortMode.Chronological)));
    }

    [Fact]
    public void ClassFilterMatchesAnyOfWindowClassesAndSpecialValues()
    {
        Assert.Equal([1, 3], Ids(AlertQuery.Apply(Queue, new AlertFilter(AnomalyClass: "volume_spike"), SortMode.Chronological)));
        Assert.Equal([3], Ids(AlertQuery.Apply(Queue, new AlertFilter(AnomalyClass: "unusual_ratio"), SortMode.Chronological)));
        Assert.Equal([1, 3, 4], Ids(AlertQuery.Apply(Queue, new AlertFilter(AnomalyClass: ClassFilter.AnyAnomaly), SortMode.Chronological)));
        Assert.Equal([2, 5], Ids(AlertQuery.Apply(Queue, new AlertFilter(AnomalyClass: ClassFilter.Normal), SortMode.Chronological)));
        Assert.Equal([2], Ids(AlertQuery.Apply(Queue, new AlertFilter(AnomalyClass: ClassFilter.HardNormal), SortMode.Chronological)));
    }

    [Fact]
    public void MinRiskLevelUsesFixedThresholds()
    {
        Assert.Equal([2, 3], Ids(AlertQuery.Apply(Queue, new AlertFilter(MinRiskLevel: RiskLevel.High), SortMode.RiskScore)));
        Assert.Equal([2], Ids(AlertQuery.Apply(Queue, new AlertFilter(MinRiskLevel: RiskLevel.Critical), SortMode.RiskScore)));
        Assert.Equal(4, AlertQuery.Apply(Queue, new AlertFilter(MinRiskLevel: RiskLevel.Medium), SortMode.RiskScore).Count);
    }

    [Fact]
    public void CombinedFiltersAreConjunctive()
    {
        var f = new AlertFilter(UserId: "U3", AnomalyClass: ClassFilter.AnyAnomaly, MinRiskLevel: RiskLevel.High);
        Assert.Equal([3], Ids(AlertQuery.Apply(Queue, f, SortMode.RiskScore)));
    }

    [Fact]
    public void RiskSortBreaksTiesChronologically()
    {
        // 4 i 5 mają ten sam RiskScore 0,72: wcześniejsze okno pierwsze (METODOLOGIA D21).
        Assert.Equal([2, 3, 4, 5, 1], Ids(AlertQuery.Apply(Queue, AlertFilter.None, SortMode.RiskScore)));
    }

    [Fact]
    public void DetectorScoreSortUsesTailExcessForSaturatedNorm()
    {
        // norm = 1 u 4 i 5: większa nadwyżka ogona (5) wyżej (D8).
        Assert.Equal([5, 4, 1, 3, 2], Ids(AlertQuery.Apply(Queue, AlertFilter.None, SortMode.DetectorScore)));
    }

    [Fact]
    public void ChronologicalSortOrdersByWindowThenUser()
    {
        var same = new List<AlertRow> { Alert(1, "U9"), Alert(2, "U1"), Alert(3, "U5", minutes: -30) };
        Assert.Equal([3, 2, 1], Ids(AlertQuery.Apply(same, AlertFilter.None, SortMode.Chronological)));
    }

    [Theory]
    [InlineData(0.6999, RiskLevel.Low)]
    [InlineData(0.70, RiskLevel.Medium)]
    [InlineData(0.80, RiskLevel.High)]
    [InlineData(0.8999, RiskLevel.High)]
    [InlineData(0.90, RiskLevel.Critical)]
    public void RiskLevelBoundaries(double risk, RiskLevel expected) => Assert.Equal(expected, RiskLevels.FromScore(risk));

    [Fact]
    public void FilterDescriptionIsReadable()
    {
        Assert.Equal("bez filtrów", AlertFilter.None.Describe());
        var d = new AlertFilter(UserId: "U1", AnomalyClass: "unusual_time", MinRiskLevel: RiskLevel.High).Describe();
        Assert.Contains("użytkownik U1", d);
        Assert.Contains("nietypowa pora", d);
        Assert.Contains("wysoki", d);
    }

    [Fact]
    public void GroundTruthLabel()
    {
        Assert.Equal("anomalia: nietypowa proporcja operacji, wzrost wolumenu", Queue[2].GroundTruth);
        Assert.Equal("norma (trudna: ALT_IP)", Queue[1].GroundTruth);
        Assert.Equal("norma", Queue[4].GroundTruth);
    }
}
