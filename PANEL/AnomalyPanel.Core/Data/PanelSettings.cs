using System.Text.Json;

namespace AnomalyPanel.Core.Data;

/// <summary>
/// Ustawienia połączenia. Kolejność: zmienna środowiskowa ANOMALY_RESULTS_CONNECTION, plik appsettings.json obok
/// programu, wartość domyślna. Połączenie przez Windows Authentication: w plikach nie ma haseł.
/// </summary>
public sealed class PanelSettings
{
    public const string EnvironmentVariable = "ANOMALY_RESULTS_CONNECTION";
    public const string DefaultConnectionString =
        "Server=localhost;Database=AnomalyResults;Integrated Security=true;TrustServerCertificate=true;" +
        "Application Name=AnomalyPanel";

    public string ConnectionString { get; init; } = DefaultConnectionString;

    public static PanelSettings Load(string? directory = null)
    {
        var env = Environment.GetEnvironmentVariable(EnvironmentVariable);
        if (!string.IsNullOrWhiteSpace(env))
            return new PanelSettings { ConnectionString = env };
        var path = Path.Combine(directory ?? AppContext.BaseDirectory, "appsettings.json");
        if (File.Exists(path))
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(path));
            if (doc.RootElement.TryGetProperty("ConnectionString", out var cs) && cs.GetString() is { Length: > 0 } s)
                return new PanelSettings { ConnectionString = s };
        }
        return new PanelSettings();
    }
}
