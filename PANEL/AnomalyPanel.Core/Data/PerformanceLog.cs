using System.Diagnostics;
using AnomalyPanel.Core.Models;

namespace AnomalyPanel.Core.Data;

/// <summary>Dziennik pomiarów czasu (zapytania SQL, ładowanie widoków, eksport). Bezpieczny wątkowo.</summary>
public sealed class PerformanceLog
{
    private readonly List<QueryTiming> _items = [];
    private readonly Lock _lock = new();

    public event Action<QueryTiming>? Recorded;

    public IReadOnlyList<QueryTiming> Snapshot()
    {
        lock (_lock) return _items.ToList();
    }

    public QueryTiming Record(string operation, TimeSpan elapsed, int rows)
    {
        var t = new QueryTiming(DateTime.Now, operation, elapsed.TotalMilliseconds, rows);
        lock (_lock) _items.Add(t);
        Recorded?.Invoke(t);
        return t;
    }

    /// <summary>Mierzy operację zwracającą listę i zapisuje czas oraz liczbę wierszy.</summary>
    public async Task<IReadOnlyList<T>> MeasureAsync<T>(string operation, Func<Task<IReadOnlyList<T>>> action)
    {
        var sw = Stopwatch.StartNew();
        var result = await action();
        Record(operation, sw.Elapsed, result.Count);
        return result;
    }
}
