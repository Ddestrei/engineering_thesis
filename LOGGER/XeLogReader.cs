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
    public record AnomalyFeatureRecord
    {
        // Metadane identyfikacyjne i korelacyjne
        public string EventName { get; init; } = string.Empty;
        public DateTimeOffset Timestamp { get; init; }
        public DateTimeOffset PhysicalTimestamp { get; init; }
        public int SessionId { get; init; }
        public string Username { get; init; } = string.Empty;
        public string HostName { get; init; } = string.Empty;
        public string ClientAppName { get; init; } = string.Empty;
        public string SimulatedIp { get; init; } = string.Empty;
        public string DeviceContext { get; init; } = string.Empty;
        public string ScenarioId { get; init; } = string.Empty; // Ground Truth
        public int IsAnomaly { get; init; }
        public string AnomalyClass { get; init; } = "None";

        // Cechy zapytania / operacji
        public string SqlText { get; init; } = string.Empty;
        public string OperationType { get; init; } = string.Empty; // SELECT, INSERT, UPDATE, DELETE, EXEC
        public string TargetTable { get; init; } = string.Empty;   // Customer, Product, SalesOrderHeader, Other
        public int SqlTextLength { get; init; }

        // Mierniki wydajnościowe (Cechy numeryczne dla ML)
        public long DurationMicroseconds { get; init; }
        public long CpuTimeMicroseconds { get; init; }
        public long LogicalReads { get; init; }
        public long Writes { get; init; }
        public long RowCount { get; init; }

        // Sygnały błędów
        public bool IsError { get; init; }
        public int ErrorNumber { get; init; }
        public int Severity { get; init; }
        public string ErrorMessage { get; init; } = string.Empty;
    }

    public class XeLogReader
    {
        private static readonly Regex ScenarioRegex = new(@"\[ScenarioID:(?<id>[^\|\]]+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex UserRegex = new(@"User:(?<val>[^\|\]]+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex HostRegex = new(@"Host:(?<val>[^\|\]]+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex IpRegex = new(@"IP:(?<val>[^\|\]]+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex TableRegex = new(@"Table:(?<val>[^\|\]]+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex SimTimeRegex = new(@"SimTime:(?<val>[^\|\]]+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);

        /// <summary>
        /// Odczytuje plik(i) .xel i parsuje je do listy obiektów AnomalyFeatureRecord.
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
            // Pobranie wspólnych akcji (Actions)
            string sqlText = GetActionValue<string>(ev, "sql_text") ?? GetFieldValue<string>(ev, "statement") ?? string.Empty;
            string clientApp = GetActionValue<string>(ev, "client_app_name") ?? string.Empty;
            
            // Login: prefer server_principal_name, fallback to username
            string username = GetActionValue<string>(ev, "server_principal_name") 
                           ?? GetActionValue<string>(ev, "username") 
                           ?? string.Empty;

            // 1. Filtracja szumu telemetrycznego i wewnętrznego serwera
            if (username.Equals(@"NT SERVICE\SQLTELEMETRY", StringComparison.OrdinalIgnoreCase) ||
                clientApp.Contains("SQLServerCEIP", StringComparison.OrdinalIgnoreCase))
            {
                return null;
            }

            bool isErrorEvent = ev.Name.Equals("error_reported", StringComparison.OrdinalIgnoreCase);
            int errorNumber = isErrorEvent ? GetFieldValue<int>(ev, "error_number") : 0;

            // 2. Filtracja komunikatów informacyjnych serwera (5701 = zmiana bazy, 5703 = zmiana języka)
            if (isErrorEvent && (errorNumber == 5701 || errorNumber == 5703))
            {
                return null;
            }

            // 3. Filtracja wewnętrznych wywołań resetu połączeń puli (sp_reset_connection)
            if (sqlText.Contains("sp_reset_connection", StringComparison.OrdinalIgnoreCase))
            {
                return null;
            }

            // Host: w Extended Events nazwa akcji to client_hostname
            string hostName = GetActionValue<string>(ev, "client_hostname") 
                           ?? GetActionValue<string>(ev, "host_name") 
                           ?? string.Empty;

            int sessionId = GetActionValue<int>(ev, "session_id");

            // Ekstrakcja metadanych (ScenarioId, IP, Device, Table) z clientApp lub komentarza SQL
            string scenarioId = ExtractTag(clientApp, sqlText, "ScenarioID", ScenarioRegex, "UNLABELED_TRAFFIC");
            
            // 4. Odrzucenie nieoznakowanego szumu tła (wszystkie zapytania symulatora posiadają ScenarioID)
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

            // Ekstrakcja symulowanego znacznika czasu z CSV (SimTime)
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
            // 1. Sprawdzenie w Connection String (client_app_name)
            string needle = $"{tagKey}=";
            if (clientApp.Contains(needle, StringComparison.OrdinalIgnoreCase))
            {
                var parts = clientApp.Split(';');
                var match = parts.FirstOrDefault(p => p.StartsWith(needle, StringComparison.OrdinalIgnoreCase));
                if (match != null) return match.Split('=')[1].Trim();
            }

            // 2. Sprawdzenie w komentarzu zapytania SQL
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

            // Heurystyka schematu SalesLT
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

            // Sprawdzenie flagi IsAnomaly w clientApp
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

    public static class MLFeatureExporter
    {
        /// <summary>
        /// Transformuje surowe logi Extended Events na bogaty format JSON z metadanymi i wektorami dla modeli ML.
        /// </summary>
        public static async Task SaveFeatureVectorsToJsonAsync(IEnumerable<AnomalyFeatureRecord> records, string outputPath)
        {
            var exportList = records.Select(r =>
            {
                // Cechy czasowe (Time-based features dla modeli behawioralnych)
                int hourOfDay = r.Timestamp.Hour;
                int dayOfWeek = (int)r.Timestamp.DayOfWeek; // 0=Sunday, 6=Saturday
                double isWeekend = (dayOfWeek == 0 || dayOfWeek == 6) ? 1.0 : 0.0;
                double isNightHours = (hourOfDay >= 22 || hourOfDay < 6) ? 1.0 : 0.0;

                // One-Hot Encoding dla operacji
                double isSelect = r.OperationType == "SELECT" ? 1.0 : 0.0;
                double isInsert = r.OperationType == "INSERT" ? 1.0 : 0.0;
                double isUpdate = r.OperationType == "UPDATE" ? 1.0 : 0.0;
                double isDelete = r.OperationType == "DELETE" ? 1.0 : 0.0;
                double isOtherOp = (!isSelect.Equals(1.0) && !isInsert.Equals(1.0) && !isUpdate.Equals(1.0) && !isDelete.Equals(1.0)) ? 1.0 : 0.0;

                // One-Hot Encoding dla tabel docelowych
                double isCustomer = r.TargetTable.Equals("Customer", StringComparison.OrdinalIgnoreCase) ? 1.0 : 0.0;
                double isProduct = r.TargetTable.Equals("Product", StringComparison.OrdinalIgnoreCase) ? 1.0 : 0.0;
                double isSalesOrderHeader = r.TargetTable.Equals("SalesOrderHeader", StringComparison.OrdinalIgnoreCase) ? 1.0 : 0.0;
                double isOtherTable = (!isCustomer.Equals(1.0) && !isProduct.Equals(1.0) && !isSalesOrderHeader.Equals(1.0)) ? 1.0 : 0.0;

                // One-Hot Encoding dla urządzeń
                double isOfficePc = r.DeviceContext.Equals("Office_PC", StringComparison.OrdinalIgnoreCase) ? 1.0 : 0.0;
                double isHomePc = r.DeviceContext.Equals("Home_PC", StringComparison.OrdinalIgnoreCase) ? 1.0 : 0.0;
                double isMobileLaptop = r.DeviceContext.Equals("Mobile_Laptop", StringComparison.OrdinalIgnoreCase) ? 1.0 : 0.0;
                double isRogueDevice = (!isOfficePc.Equals(1.0) && !isHomePc.Equals(1.0) && !isMobileLaptop.Equals(1.0)) ? 1.0 : 0.0;

                // Czysty wektor numeryczny gotowy dla algorytmów (Isolation Forest, LOF, OCSVM)
                double[] featureVector = new double[]
                {
                    r.DurationMicroseconds,
                    r.CpuTimeMicroseconds,
                    r.LogicalReads,
                    r.Writes,
                    r.RowCount,
                    r.SqlTextLength,
                    hourOfDay,
                    dayOfWeek,
                    isWeekend,
                    isNightHours,
                    r.IsError ? 1.0 : 0.0,
                    r.ErrorNumber,
                    isSelect,
                    isInsert,
                    isUpdate,
                    isDelete,
                    isOtherOp,
                    isCustomer,
                    isProduct,
                    isSalesOrderHeader,
                    isOtherTable,
                    isOfficePc,
                    isHomePc,
                    isMobileLaptop,
                    isRogueDevice
                };

                return new
                {
                    // 1. Identyfikacja i Ground Truth dla ewaluacji modeli
                    GroundTruth = new
                    {
                        ScenarioId = r.ScenarioId,
                        IsAnomaly = r.IsAnomaly,
                        AnomalyClass = r.AnomalyClass,
                    },

                    // 2. Kontekst audytowy i środowiskowy
                    Context = new
                    {
                        Timestamp = r.Timestamp.ToString("o", CultureInfo.InvariantCulture),
                        PhysicalServerTimestamp = r.PhysicalTimestamp.ToString("o", CultureInfo.InvariantCulture),
                        Username = r.Username,
                        HostName = r.HostName,
                        SimulatedIp = r.SimulatedIp,
                        DeviceContext = r.DeviceContext,
                        OperationType = r.OperationType,
                        TargetTable = r.TargetTable,
                        EventName = r.EventName,
                        SessionId = r.SessionId,
                        IsError = r.IsError,
                        ErrorMessage = r.ErrorMessage
                    },

                    // 3. Tablica numeryczna wektora cech (X) do uczenia maszynowego
                    Features = featureVector
                };
            }).ToList();

            var options = new JsonSerializerOptions
            {
                WriteIndented = true
            };

            // Zapewnienie istnienia katalogu docelowego
            string dir = Path.GetDirectoryName(outputPath) ?? "";
            if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
            {
                Directory.CreateDirectory(dir);
            }

            using FileStream createStream = File.Create(outputPath);
            await JsonSerializer.SerializeAsync(createStream, exportList, options);
        }
    }
}
