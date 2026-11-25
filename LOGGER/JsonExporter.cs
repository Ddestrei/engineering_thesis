using System;
using System.IO;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Threading.Tasks;

namespace LOGGER
{
    /// <summary>
    /// Zapis wyniku odczytu do JSON (schemat "xe-export/v3"; v3 dodaje TargetTable):
    /// { schema, exported_at, source_files, user_filter, stats, events[], errors[] }.
    /// Plik czyta anomaly/features/xe_join.py.
    /// </summary>
    public static class JsonExporter
    {
        public const string Schema = "xe-export/v3";

        public static async Task SaveAsync(XeReadResult result, string? userFilter, string outputPath)
        {
            var document = new
            {
                schema = Schema,
                exported_at = DateTimeOffset.Now.ToString("o"),
                source_files = result.SourceFiles,
                user_filter = userFilter,
                stats = result.Stats,
                events = result.Events,
                errors = result.Errors,
            };

            var options = new JsonSerializerOptions
            {
                WriteIndented = true,
                Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping, // czytelne polskie znaki i operatory SQL
            };

            string? dir = Path.GetDirectoryName(outputPath);
            if (!string.IsNullOrEmpty(dir))
            {
                Directory.CreateDirectory(dir);
            }

            await using FileStream stream = File.Create(outputPath);
            await JsonSerializer.SerializeAsync(stream, document, options);
        }
    }
}
