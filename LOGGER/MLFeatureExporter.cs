using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

namespace LOGGER
{
    public static class MLFeatureExporter
    {
        /// <summary>
        /// Transformuje surowe logi Extended Events na bogaty format JSON z metadanymi i wektorami dla modeli ML.
        /// </summary>
        public static async Task SaveFeatureVectorsToJsonAsync(IEnumerable<AnomalyFeatureRecord> records, string outputPath)
        {
            var exportList = records.Select(r =>
            {
                // Cechy czasowe Time-based features dla modeli behawioralnych
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

                // Czysty wektor numeryczny gotowy dla algorytmów Isolation Forest, LOF, OCSVM
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
                    //Identyfikacja i Ground Truth dla ewaluacji modeli
                    GroundTruth = new
                    {
                        ScenarioId = r.ScenarioId,
                        IsAnomaly = r.IsAnomaly,
                        AnomalyClass = r.AnomalyClass,
                    },

                    //Kontekst audytowy i środowiskowy
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

                    //Tablica numeryczna wektora cech (X) do uczenia maszynowego
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
