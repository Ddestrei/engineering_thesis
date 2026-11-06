using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;

namespace LOGGER
{
    class Program
    {
        static async Task Main(string[] args)
        {
            Console.WriteLine("=================================================================");
            Console.WriteLine(" LOGGER (.NET 9) - Extended Events to ML Feature JSON Extractor");
            Console.WriteLine("=================================================================\n");

            // 1. Ustalenie ścieżki pliku .xel (argument lub automatyczne wykrycie)
            string xelPath = "";
            if (args.Length > 0 && !string.IsNullOrWhiteSpace(args[0]))
            {
                xelPath = args[0];
            }
            else
            {
                string xelDir = @"C:\XELogs";
                if (Directory.Exists(xelDir))
                {
                    var xelFiles = Directory.GetFiles(xelDir, "AW2025_Behavioral_Anomaly*.xel")
                                           .Select(f => new FileInfo(f))
                                           .OrderByDescending(f => f.LastWriteTimeUtc)
                                           .ToList();

                    if (xelFiles.Count > 0)
                    {
                        xelPath = xelFiles.First().FullName;
                        Console.WriteLine($"[AUTO] Znaleziono najnowszy plik logów: {Path.GetFileName(xelPath)}");
                    }
                    else
                    {
                        xelPath = @"C:\XELogs\AW2025_Behavioral_Anomaly*.xel";
                    }
                }
                else
                {
                    xelPath = @"C:\XELogs\AW2025_Behavioral_Anomaly*.xel";
                }
            }

            // 2. Ustalenie ścieżki wyjściowej JSON
            string jsonOutputPath = "";
            if (args.Length > 1 && !string.IsNullOrWhiteSpace(args[1]))
            {
                jsonOutputPath = args[1];
            }
            else
            {
                string logiDir = @"C:\Users\ddest\Desktop\INŻYNIERKA\LOGI";
                jsonOutputPath = Path.Combine(logiDir, $"ml_feature_vectors_{DateTime.Now:yyyyMMdd_HHmmss}.json");
            }

            Console.WriteLine($"[1/3] Odczyt i ekstrakcja zdarzeń Extended Events z: {xelPath}...");

            var reader = new XeLogReader();
            List<AnomalyFeatureRecord> records;

            try
            {
                records = reader.ReadXelEvents(xelPath).ToList();
            }
            catch (Exception ex)
            {
                Console.ForegroundColor = ConsoleColor.Red;
                Console.WriteLine($"[BŁĄD ODCZYTU XEL]: {ex.Message}");
                Console.ResetColor();
                return;
            }

            Console.WriteLine($"-> Pomyślnie załadowano {records.Count} surowych zdarzeń.");

            if (records.Count == 0)
            {
                Console.WriteLine("[INFO] Brak zdarzeń w pliku XEL do eksportu.");
                return;
            }

            // 3. Sprawdzenie opcjonalnego filtra użytkownika (np. --user CEO_User)
            string? userFilter = null;
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i].Equals("--user", StringComparison.OrdinalIgnoreCase))
                {
                    userFilter = args[i + 1];
                    break;
                }
            }
            if (args.Contains("--ceo", StringComparer.OrdinalIgnoreCase))
            {
                userFilter = "CEO_User";
            }

            if (!string.IsNullOrEmpty(userFilter))
            {
                records = records.Where(r => r.Username.Equals(userFilter, StringComparison.OrdinalIgnoreCase)).ToList();
                Console.WriteLine($"[FILTR] Zastosowano filtr użytkownika: {userFilter} (pozostało zdarzeń: {records.Count})");
            }

            // 4. Statystyki wyekstrahowanych cech
            int normalCount = records.Count(r => r.IsAnomaly == 0);
            int anomalyCount = records.Count(r => r.IsAnomaly == 1);
            Console.WriteLine($"\n[2/3] Statystyka Ground Truth:");
            Console.WriteLine($"   * Normalne zapytania:  {normalCount}");
            Console.WriteLine($"   * Wykryte anomalie:    {anomalyCount}");

            var anomalyClasses = records.Where(r => r.IsAnomaly == 1)
                                        .GroupBy(r => r.AnomalyClass)
                                        .Select(g => $"{g.Key}: {g.Count()}");
            if (anomalyCount > 0)
            {
                Console.WriteLine($"   * Klasy anomalii:      {string.Join(", ", anomalyClasses)}");
            }

            var devices = records.GroupBy(r => r.DeviceContext)
                                 .Select(g => $"{g.Key}: {g.Count()}");
            Console.WriteLine($"   * Konteksty urządzeń:  {string.Join(", ", devices)}");

            // 4. Eksport do JSON
            Console.WriteLine($"\n[3/3] Zapisywanie ustrukturyzowanych cech ML do JSON: {jsonOutputPath}...");
            await MLFeatureExporter.SaveFeatureVectorsToJsonAsync(records, jsonOutputPath);

            Console.ForegroundColor = ConsoleColor.Green;
            Console.WriteLine($"\n-> Sukces! Plik JSON został wygenerowany pomyślnie.");
            Console.WriteLine($"   Ścieżka: {jsonOutputPath}");
            Console.ResetColor();
        }
    }
}
