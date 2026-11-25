using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;

namespace LOGGER
{
    /// <summary>
    /// LOGGER: eksport zdarzeń Extended Events (.xel) do JSON (schemat xe-export/v2).
    ///
    /// Użycie:
    ///   LOGGER [xel] [wyjście.json] [--user LOGIN | --ceo]
    ///     xel          plik .xel, katalog albo wzorzec z '*'
    ///                  (domyślnie wszystkie pliki C:\XELogs\AW2025_Behavioral_Anomaly*.xel)
    ///     wyjście.json domyślnie data/xe_json/xe_events_&lt;data&gt;.json w repozytorium
    ///     --user LOGIN eksportuj tylko zdarzenia danego loginu; --ceo = --user CEO_User
    ///
    /// LOGGER nie zna etykiet ground truth i nie liczy cech. Eksportuje surowe fakty z XE
    /// oraz EventUid ze znacznika /* gt:... */; resztę robi anomaly/features/xe_join.py.
    /// </summary>
    class Program
    {
        private const string DefaultXelPattern = @"C:\XELogs\AW2025_Behavioral_Anomaly*.xel";

        static async Task<int> Main(string[] args)
        {
            Console.WriteLine("=================================================================");
            Console.WriteLine(" LOGGER (.NET 9) - Extended Events -> JSON (xe-export/v2)");
            Console.WriteLine("=================================================================\n");

            var (positional, userFilter) = ParseArgs(args);
            string xelSpec = positional.ElementAtOrDefault(0) ?? DefaultXelPattern;
            string jsonOutputPath = positional.ElementAtOrDefault(1)
                ?? Path.Combine(ResolveDefaultOutputDir(), $"xe_events_{DateTime.Now:yyyyMMdd_HHmmss}.json");

            List<string> xelFiles = ResolveXelFiles(xelSpec);
            if (xelFiles.Count == 0)
            {
                WriteColored($"[BŁĄD] Nie znaleziono plików .xel dla: {xelSpec}", ConsoleColor.Red);
                return 1;
            }

            Console.WriteLine($"[1/3] Odczyt {xelFiles.Count} plików .xel:");
            xelFiles.ForEach(f => Console.WriteLine($"      {f}"));
            if (!string.IsNullOrEmpty(userFilter))
            {
                Console.WriteLine($"      filtr użytkownika: {userFilter}");
            }

            XeReadResult result;
            try
            {
                result = new XeLogReader().ReadXelFiles(xelFiles, userFilter);
            }
            catch (Exception ex)
            {
                WriteColored($"[BŁĄD ODCZYTU XEL]: {ex.Message}", ConsoleColor.Red);
                return 1;
            }

            XeReadStats s = result.Stats;
            Console.WriteLine("\n[2/3] Statystyka odczytu:");
            Console.WriteLine($"   * Wszystkie zdarzenia w plikach:     {s.TotalEventsRead}");
            Console.WriteLine($"   * Wyeksportowane zapytania (gt):     {s.ExportedEvents}");
            Console.WriteLine($"   * Błędy SQL ze znacznikiem gt:       {s.ExportedErrors}");
            Console.WriteLine($"   * Pominięte: szum systemowy:         {s.SkippedSystemNoise}");
            Console.WriteLine($"   * Pominięte: bez znacznika gt:       {s.SkippedWithoutGtTag}");
            Console.WriteLine($"   * Pominięte: filtr użytkownika:      {s.SkippedByUserFilter}");
            foreach (var run in result.Events.GroupBy(e => e.AppRun).OrderBy(g => g.Key))
            {
                Console.WriteLine($"   * Przebieg {(string.IsNullOrEmpty(run.Key) ? "(brak)" : run.Key),-20} {run.Count()} zdarzeń");
            }
            if (s.ExportedErrors > 0)
            {
                WriteColored($"   ! {s.ExportedErrors} błędów SQL w operacjach symulatora: walidacja (xe_join) przerwie przebieg.", ConsoleColor.Yellow);
            }

            Console.WriteLine($"\n[3/3] Zapis JSON: {jsonOutputPath}");
            await JsonExporter.SaveAsync(result, userFilter, jsonOutputPath);
            WriteColored("-> Sukces.", ConsoleColor.Green);
            return 0;
        }

        private static (List<string> positional, string? userFilter) ParseArgs(string[] args)
        {
            var positional = new List<string>();
            string? userFilter = null;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i].Equals("--ceo", StringComparison.OrdinalIgnoreCase))
                {
                    userFilter = "CEO_User";
                }
                else if (args[i].Equals("--user", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                {
                    userFilter = args[++i];
                }
                else if (!string.IsNullOrWhiteSpace(args[i]))
                {
                    positional.Add(args[i]);
                }
            }
            return (positional, userFilter);
        }

        /// <summary>Plik, katalog (wszystkie *.xel) albo wzorzec z '*'. Pliki posortowane po nazwie (kolejność rollover).</summary>
        private static List<string> ResolveXelFiles(string spec)
        {
            if (File.Exists(spec))
            {
                return new List<string> { Path.GetFullPath(spec) };
            }
            string dir, pattern;
            if (Directory.Exists(spec))
            {
                (dir, pattern) = (spec, "*.xel");
            }
            else
            {
                dir = Path.GetDirectoryName(spec) ?? ".";
                pattern = Path.GetFileName(spec);
            }
            if (!Directory.Exists(dir))
            {
                return new List<string>();
            }
            return Directory.GetFiles(dir, pattern).OrderBy(f => f, StringComparer.OrdinalIgnoreCase).ToList();
        }

        /// <summary>
        /// Domyślny katalog wyjściowy: data/xe_json w katalogu głównym repozytorium.
        /// Repozytorium szukane jest w górę od katalogu programu i od katalogu bieżącego
        /// (rozpoznawane po pliku config.example.json). Można je nadpisać zmienną środowiskową
        /// ANOMALY_XE_JSON_DIR albo drugim argumentem programu.
        /// </summary>
        private static string ResolveDefaultOutputDir()
        {
            string? fromEnv = Environment.GetEnvironmentVariable("ANOMALY_XE_JSON_DIR");
            if (!string.IsNullOrWhiteSpace(fromEnv))
            {
                return fromEnv;
            }

            foreach (string start in new[] { AppContext.BaseDirectory, Directory.GetCurrentDirectory() })
            {
                DirectoryInfo? dir = new DirectoryInfo(start);
                while (dir != null)
                {
                    if (File.Exists(Path.Combine(dir.FullName, "config.example.json")))
                    {
                        return Path.Combine(dir.FullName, "data", "xe_json");
                    }
                    dir = dir.Parent;
                }
            }

            return Path.Combine(Directory.GetCurrentDirectory(), "xe_json");
        }

        private static void WriteColored(string text, ConsoleColor color)
        {
            Console.ForegroundColor = color;
            Console.WriteLine(text);
            Console.ResetColor();
        }
    }
}
