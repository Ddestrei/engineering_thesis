using Microsoft.SqlServer.XEvent.Linq;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;

namespace LOGGER
{
    /// <summary>
    /// Odczyt plików .xel i mapowanie zdarzeń na surowe rekordy (XeEventRecord / XeErrorRecord).
    /// Czytnik nie zna etykiet ground truth i nie liczy cech; robi to Python po połączeniu po EventUid.
    /// </summary>
    public class XeLogReader
    {
        // Format znacznika musi być zgodny z anomaly/execution/gt_tag.py.
        private static readonly Regex GtTagRegex = new(@"/\*\s*gt:(?<uid>[A-Za-z0-9_-]+)\s*\*/", RegexOptions.Compiled);

        // Komentarze blokowe i liniowe na początku zapytania (pomijane przy wyznaczaniu typu operacji).
        private static readonly Regex LeadingCommentsRegex = new(@"^(\s*(/\*.*?\*/|--[^\n]*\n))*\s*", RegexOptions.Compiled | RegexOptions.Singleline);

        // Tabele schematu SalesLT, np. SalesLT.Customer, [SalesLT].[Customer].
        private static readonly Regex TableRegex = new(@"\[?SalesLT\]?\s*\.\s*\[?(?<table>\w+)\]?", RegexOptions.Compiled | RegexOptions.IgnoreCase);

        // Tabela główna zapytania: pierwsza po FROM (SELECT), INTO, UPDATE albo DELETE FROM.
        // Ten sam wzorzec ma Python (anomaly/execution/SQLQueryBuilder.py, PRIMARY_TABLE_REGEX).
        private static readonly Regex PrimaryTableRegex = new(
            @"^\s*(?:SELECT\b.*?\bFROM|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+\[?SalesLT\]?\s*\.\s*\[?(?<table>\w+)\]?",
            RegexOptions.Compiled | RegexOptions.IgnoreCase | RegexOptions.Singleline);

        private static readonly HashSet<string> CompletedEventNames = new(StringComparer.OrdinalIgnoreCase)
        {
            "sql_batch_completed", "rpc_completed", "sql_statement_completed"
        };

        public XeReadResult ReadXelFiles(IEnumerable<string> xelFiles, string? userFilter)
        {
            var result = new XeReadResult();
            foreach (string file in xelFiles)
            {
                result.SourceFiles.Add(file);
                using var events = new QueryableXEventData(file);
                foreach (PublishedEvent ev in events)
                {
                    result.Stats.TotalEventsRead++;
                    Map(ev, userFilter, result);
                }
            }
            result.Stats.ExportedEvents = result.Events.Count;
            result.Stats.ExportedErrors = result.Errors.Count;
            return result;
        }

        private void Map(PublishedEvent ev, string? userFilter, XeReadResult result)
        {
            bool isError = ev.Name.Equals("error_reported", StringComparison.OrdinalIgnoreCase);
            // Tekst SQL z pola samego zdarzenia (batch_text / statement). Akcja sql_text to bufor wejściowy
            // sesji: dla wewnętrznych wywołań RPC sterownika ODBC (rpc_completed) zawiera jeszcze poprzedni
            // batch razem ze znacznikiem gt, co dawało zdublowane EventUid. Dla error_reported (bez pola
            // z tekstem) akcja sql_text wskazuje batch, który zgłosił błąd.
            string rawSql = (isError ? null : GetFieldString(ev, "batch_text") ?? GetFieldString(ev, "statement"))
                ?? GetActionString(ev, "sql_text") ?? string.Empty;
            string clientApp = GetActionString(ev, "client_app_name") ?? string.Empty;
            string username = GetActionString(ev, "server_principal_name") ?? GetActionString(ev, "username") ?? string.Empty;

            // Szum systemowy: telemetria serwera, reset połączeń z puli, komunikaty informacyjne.
            if (username.Equals(@"NT SERVICE\SQLTELEMETRY", StringComparison.OrdinalIgnoreCase)
                || clientApp.Contains("SQLServerCEIP", StringComparison.OrdinalIgnoreCase)
                || rawSql.Contains("sp_reset_connection", StringComparison.OrdinalIgnoreCase))
            {
                result.Stats.SkippedSystemNoise++;
                return;
            }
            int errorNumber = isError ? (int)GetFieldLong(ev, "error_number") : 0;
            if (isError && (errorNumber == 5701 || errorNumber == 5703))
            {
                result.Stats.SkippedSystemNoise++;
                return;
            }
            if (!isError && !CompletedEventNames.Contains(ev.Name))
            {
                result.Stats.SkippedSystemNoise++;
                return;
            }

            Match gt = GtTagRegex.Match(rawSql);
            if (!gt.Success)
            {
                result.Stats.SkippedWithoutGtTag++;
                return;
            }
            if (!string.IsNullOrEmpty(userFilter) && !username.Equals(userFilter, StringComparison.OrdinalIgnoreCase))
            {
                result.Stats.SkippedByUserFilter++;
                return;
            }

            string eventUid = gt.Groups["uid"].Value;
            string sqlText = StripGtTag(rawSql);
            int sessionId = (int)GetActionLong(ev, "session_id");

            if (isError)
            {
                result.Errors.Add(new XeErrorRecord
                {
                    EventUid = eventUid,
                    PhysicalTimestamp = ev.Timestamp,
                    SessionId = sessionId,
                    Username = username,
                    ErrorNumber = errorNumber,
                    Severity = (int)GetFieldLong(ev, "severity"),
                    Message = GetFieldString(ev, "message") ?? string.Empty,
                    SqlText = sqlText,
                });
                return;
            }

            var (appRun, appDevice, appIp) = ParseAppName(clientApp);
            result.Events.Add(new XeEventRecord
            {
                EventUid = eventUid,
                EventName = ev.Name,
                PhysicalTimestamp = ev.Timestamp,
                EventSequence = GetActionLong(ev, "event_sequence"),
                SessionId = sessionId,
                ClientConnectionId = GetActionString(ev, "client_connection_id") ?? string.Empty,
                Username = username,
                HostName = GetActionString(ev, "client_hostname") ?? string.Empty,
                ClientAppName = clientApp,
                AppRun = appRun,
                AppDevice = appDevice,
                AppIp = appIp,
                SqlText = sqlText,
                SqlTextLength = sqlText.Length,
                OperationType = DetermineOperationType(sqlText),
                TargetTable = ExtractTargetTable(sqlText),
                Tables = ExtractTables(sqlText),
                Result = GetFieldString(ev, "result") ?? string.Empty,
                DurationMicroseconds = GetFieldLong(ev, "duration"),
                CpuTimeMicroseconds = GetFieldLong(ev, "cpu_time"),
                LogicalReads = GetFieldLong(ev, "logical_reads"),
                PhysicalReads = GetFieldLong(ev, "physical_reads"),
                Writes = GetFieldLong(ev, "writes"),
                RowCount = GetFieldLong(ev, "row_count"),
            });
        }

        /// <summary>Tekst zapytania bez znacznika gt (zgodnie z gt_tag.strip_tag w Pythonie).</summary>
        public static string StripGtTag(string sql)
        {
            string stripped = GtTagRegex.Replace(sql, string.Empty).TrimEnd();
            return stripped.TrimEnd(';').TrimEnd();
        }

        /// <summary>Rozbiór nazwy aplikacji SIM|run:..|dev:..|ip:.. (anomaly/execution/SessionConnectionManager.py).</summary>
        public static (string run, string device, string ip) ParseAppName(string appName)
        {
            if (!appName.StartsWith("SIM|", StringComparison.Ordinal))
            {
                return (string.Empty, string.Empty, string.Empty);
            }
            var fields = appName.Split('|')
                .Skip(1)
                .Select(part => part.Split(':', 2))
                .Where(kv => kv.Length == 2)
                .ToDictionary(kv => kv[0], kv => kv[1]);
            return (fields.GetValueOrDefault("run", ""), fields.GetValueOrDefault("dev", ""), fields.GetValueOrDefault("ip", ""));
        }

        public static string DetermineOperationType(string sqlText)
        {
            string body = LeadingCommentsRegex.Replace(sqlText, string.Empty, 1).ToUpperInvariant();
            foreach (string op in new[] { "SELECT", "INSERT", "UPDATE", "DELETE", "MERGE" })
            {
                if (body.StartsWith(op, StringComparison.Ordinal)) return op;
            }
            if (body.StartsWith("EXEC", StringComparison.Ordinal)) return "EXEC";
            return string.IsNullOrWhiteSpace(body) ? "UNKNOWN" : "OTHER";
        }

        public static string ExtractTargetTable(string sqlText)
        {
            string body = LeadingCommentsRegex.Replace(sqlText, string.Empty, 1);
            Match m = PrimaryTableRegex.Match(body);
            return m.Success ? m.Groups["table"].Value : string.Empty;
        }

        public static IReadOnlyList<string> ExtractTables(string sqlText)
        {
            return TableRegex.Matches(sqlText)
                .Select(m => m.Groups["table"].Value)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToList();
        }

        private static string? GetActionString(PublishedEvent ev, string name)
        {
            return ev.Actions.TryGetValue(name, out var action) && action.Value != null
                ? Convert.ToString(action.Value, CultureInfo.InvariantCulture)
                : null;
        }

        private static string? GetFieldString(PublishedEvent ev, string name)
        {
            return ev.Fields.TryGetValue(name, out var field) && field.Value != null
                ? Convert.ToString(field.Value, CultureInfo.InvariantCulture)
                : null;
        }

        private static long GetActionLong(PublishedEvent ev, string name)
        {
            return ev.Actions.TryGetValue(name, out var action) ? ToLong(action.Value) : 0L;
        }

        private static long GetFieldLong(PublishedEvent ev, string name)
        {
            return ev.Fields.TryGetValue(name, out var field) ? ToLong(field.Value) : 0L;
        }

        private static long ToLong(object? value)
        {
            if (value == null) return 0L;
            try
            {
                return Convert.ToInt64(value, CultureInfo.InvariantCulture);
            }
            catch (Exception ex) when (ex is InvalidCastException or FormatException or OverflowException)
            {
                return long.TryParse(Convert.ToString(value, CultureInfo.InvariantCulture), out long parsed) ? parsed : 0L;
            }
        }
    }
}
