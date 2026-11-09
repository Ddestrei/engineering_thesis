using Microsoft.SqlServer.XEvent.Linq;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

namespace LOGGER
{
    

    public class XeLogReader
    {
        private static readonly Regex ScenarioRegex = new(@"\[ScenarioID:(?<id>[^\|\]]+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex UserRegex = new(@"User:(?<val>[^\|\]]+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex HostRegex = new(@"Host:(?<val>[^\|\]]+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex IpRegex = new(@"IP:(?<val>[^\|\]]+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex TableRegex = new(@"Table:(?<val>[^\|\]]+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex SimTimeRegex = new(@"SimTime:(?<val>[^\|\]]+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);

        /// <summary>
        /// Odczytuje plik .xel i parsuje je do listy obiektów AnomalyFeatureRecord.
        /// </summary>
        public IEnumerable<AnomalyFeatureRecord> ReadXelEvents(string xelFilePath)
        {
            if (!File.Exists(xelFilePath) && !xelFilePath.Contains('*'))
            {
                throw new FileNotFoundException($"Plik logów XE nie został odnaleziony: {xelFilePath}");
            }

            using var events = new QueryableXEventData(xelFilePath);

            foreach (PublishedEvent ev in events)
            {
                var record = MapToFeatureRecord(ev);
                if (record != null)
                {
                    yield return record;
                }
            }
        }

        private AnomalyFeatureRecord? MapToFeatureRecord(PublishedEvent ev)
        {
            // Pobranie wspólnych akcji
            string sqlText = GetActionValue<string>(ev, "sql_text") ?? GetFieldValue<string>(ev, "statement") ?? string.Empty;
            string clientApp = GetActionValue<string>(ev, "client_app_name") ?? string.Empty;
            
            string username = GetActionValue<string>(ev, "server_principal_name") 
                           ?? GetActionValue<string>(ev, "username") 
                           ?? string.Empty;

            //Filtracja szumu telemetrycznego i wewnętrznego serwera
            if (username.Equals(@"NT SERVICE\SQLTELEMETRY", StringComparison.OrdinalIgnoreCase) ||
                clientApp.Contains("SQLServerCEIP", StringComparison.OrdinalIgnoreCase))
            {
                return null;
            }

            bool isErrorEvent = ev.Name.Equals("error_reported", StringComparison.OrdinalIgnoreCase);
            int errorNumber = isErrorEvent ? GetFieldValue<int>(ev, "error_number") : 0;

            //Filtracja komunikatów informacyjnych serwera (5701 = zmiana bazy, 5703 = zmiana języka)
            if (isErrorEvent && (errorNumber == 5701 || errorNumber == 5703))
            {
                return null;
            }

            //Filtracja wewnętrznych wywołań resetu połączeń puli (sp_reset_connection)
            if (sqlText.Contains("sp_reset_connection", StringComparison.OrdinalIgnoreCase))
            {
                return null;
            }

            string hostName = GetActionValue<string>(ev, "client_hostname") 
                           ?? GetActionValue<string>(ev, "host_name") 
                           ?? string.Empty;

            int sessionId = GetActionValue<int>(ev, "session_id");

            string scenarioId = ExtractTag(clientApp, sqlText, "ScenarioID", ScenarioRegex, "UNLABELED_TRAFFIC");
            
            //Odrzucenie nieoznakowanego szumu tła (wszystkie zapytania symulatora posiadają ScenarioID)
            if (scenarioId.Equals("UNLABELED_TRAFFIC", StringComparison.OrdinalIgnoreCase))
            {
                return null;
            }
            string simulatedIp = ExtractTag(clientApp, sqlText, "IP", IpRegex, "Unknown_IP");
            string deviceContext = ExtractTag(clientApp, sqlText, "Dev", null, "Unknown_Device");
            if (deviceContext == "Unknown_Device")
            {
                deviceContext = ExtractTag(clientApp, sqlText, "Device", null, DetermineDeviceFromHost(hostName));
            }

            string targetTable = ExtractTargetTable(sqlText, clientApp);
            var (isAnomaly, anomalyClass) = DetermineAnomaly(scenarioId, clientApp);

            //Ekstrakcja symulowanego znacznika czasu z CSV
            string simTimeStr = ExtractTag(clientApp, sqlText, "SimTime", SimTimeRegex, "");
            DateTimeOffset effectiveTimestamp = ev.Timestamp;
            if (!string.IsNullOrWhiteSpace(simTimeStr))
            {
                if (DateTimeOffset.TryParse(simTimeStr, CultureInfo.InvariantCulture, DateTimeStyles.None, out var parsedDto))
                {
                    effectiveTimestamp = parsedDto;
                }
                else if (DateTime.TryParse(simTimeStr, CultureInfo.InvariantCulture, DateTimeStyles.None, out var parsedDt))
                {
                    effectiveTimestamp = new DateTimeOffset(parsedDt);
                }
            }

            return new AnomalyFeatureRecord
            {
                EventName = ev.Name,
                Timestamp = effectiveTimestamp,
                PhysicalTimestamp = ev.Timestamp,
                SessionId = sessionId,
                Username = username,
                HostName = hostName,
                ClientAppName = clientApp,
                SimulatedIp = simulatedIp,
                DeviceContext = deviceContext,
                ScenarioId = scenarioId,
                IsAnomaly = isAnomaly,
                AnomalyClass = anomalyClass,
                SqlText = sqlText,
                SqlTextLength = sqlText.Length,
                OperationType = DetermineOperationType(sqlText),
                TargetTable = targetTable,

                // Wartości liczbowe dla zdarzeń zakończonych sukcesem
                DurationMicroseconds = GetFieldValue<long>(ev, "duration"),
                CpuTimeMicroseconds = GetFieldValue<long>(ev, "cpu_time"),
                LogicalReads = GetFieldValue<long>(ev, "logical_reads"),
                Writes = GetFieldValue<long>(ev, "writes"),
                RowCount = GetFieldValue<long>(ev, "row_count"),

                // Wartości błędów
                IsError = isErrorEvent,
                ErrorNumber = isErrorEvent ? GetFieldValue<int>(ev, "error_number") : 0,
                Severity = isErrorEvent ? GetFieldValue<int>(ev, "severity") : 0,
                ErrorMessage = isErrorEvent ? GetFieldValue<string>(ev, "message") ?? string.Empty : string.Empty
            };
        }

        private static T GetActionValue<T>(PublishedEvent ev, string actionName)
        {
            if (ev.Actions.TryGetValue(actionName, out var action) && action.Value != null)
            {
                return (T)Convert.ChangeType(action.Value, typeof(T));
            }
            return default!;
        }

        private static T GetFieldValue<T>(PublishedEvent ev, string fieldName)
        {
            if (ev.Fields.TryGetValue(fieldName, out var field) && field.Value != null)
            {
                return (T)Convert.ChangeType(field.Value, typeof(T));
            }
            return default!;
        }

        private static string ExtractTag(string clientApp, string sqlText, string tagKey, Regex? regex, string defaultValue)
        {
            //Sprawdzenie w Connection String (client_app_name)
            string needle = $"{tagKey}=";
            if (clientApp.Contains(needle, StringComparison.OrdinalIgnoreCase))
            {
                var parts = clientApp.Split(';');
                var match = parts.FirstOrDefault(p => p.StartsWith(needle, StringComparison.OrdinalIgnoreCase));
                if (match != null) return match.Split('=')[1].Trim();
            }

            //Sprawdzenie w komentarzu zapytania SQL
            if (regex != null)
            {
                var sqlMatch = regex.Match(sqlText);
                if (sqlMatch.Success)
                {
                    return sqlMatch.Groups["val"].Success ? sqlMatch.Groups["val"].Value.Trim() : sqlMatch.Groups["id"].Value.Trim();
                }
            }

            return defaultValue;
        }

        private static string DetermineDeviceFromHost(string hostName)
        {
            if (string.IsNullOrWhiteSpace(hostName)) return "Unknown_Device";
            string upper = hostName.ToUpperInvariant();
            if (upper.Contains("OFFICE")) return "Office_PC";
            if (upper.Contains("HOME")) return "Home_PC";
            if (upper.Contains("MACBOOK") || upper.Contains("AIR") || upper.Contains("LAPTOP") || upper.Contains("MOBILE")) return "Mobile_Laptop";
            if (upper.Contains("ROGUE") || upper.Contains("EXT")) return "Rogue_Device";
            return "Other_Device";
        }

        private static string ExtractTargetTable(string sqlText, string clientApp)
        {
            var match = TableRegex.Match(sqlText);
            if (match.Success) return match.Groups["val"].Value.Trim();

            //Heurystyka schematu SalesLT
            string upper = sqlText.ToUpperInvariant();
            if (upper.Contains("SALESLT.CUSTOMER") || upper.Contains("FROM CUSTOMER") || upper.Contains("INTO CUSTOMER"))
                return "Customer";
            if (upper.Contains("SALESLT.SALESORDERHEADER") || upper.Contains("FROM SALESORDERHEADER") || upper.Contains("INTO SALESORDERHEADER"))
                return "SalesOrderHeader";
            if (upper.Contains("SALESLT.PRODUCT") || upper.Contains("FROM PRODUCT") || upper.Contains("INTO PRODUCT"))
                return "Product";

            return "Other";
        }

        private static (int isAnomaly, string anomalyClass) DetermineAnomaly(string scenarioId, string clientApp)
        {
            if (string.IsNullOrWhiteSpace(scenarioId) || scenarioId == "NORMAL_TRAFFIC" || scenarioId == "UNLABELED_TRAFFIC")
            {
                return (0, "None");
            }

            if (scenarioId.StartsWith("ANOM_TIME", StringComparison.OrdinalIgnoreCase))
                return (1, "unusual_time");
            if (scenarioId.StartsWith("ANOM_VOLUME", StringComparison.OrdinalIgnoreCase))
                return (1, "volume_spike");
            if (scenarioId.StartsWith("ANOM_RATIO", StringComparison.OrdinalIgnoreCase))
                return (1, "unusual_ratio");
            if (scenarioId.StartsWith("ANOM_RARE", StringComparison.OrdinalIgnoreCase))
                return (1, "rare_resource_access");

            //Sprawdzenie flagi IsAnomaly w clientApp
            if (clientApp.Contains("IsAnomaly=1", StringComparison.OrdinalIgnoreCase))
                return (1, "unspecified_anomaly");

            return (0, "None");
        }

        private static string DetermineOperationType(string sqlText)
        {
            if (string.IsNullOrWhiteSpace(sqlText)) return "UNKNOWN";

            var trimmed = sqlText.TrimStart().ToUpperInvariant();
            if (trimmed.StartsWith("SELECT")) return "SELECT";
            if (trimmed.StartsWith("INSERT")) return "INSERT";
            if (trimmed.StartsWith("UPDATE")) return "UPDATE";
            if (trimmed.StartsWith("DELETE")) return "DELETE";
            if (trimmed.StartsWith("EXEC") || trimmed.StartsWith("EXECUTE")) return "EXEC";

            return "OTHER";
        }
    }

}
