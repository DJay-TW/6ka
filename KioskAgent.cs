using System;
using System.Collections.Generic;
using System.Data.SqlClient;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Web;

namespace SixKa.KioskAgent
{
    internal static class Program
    {
        private const string Version = "0.1.5";
        private static readonly DateTimeOffset StartedAt = DateTimeOffset.Now;
        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
        private static string _baseDir;
        private static string _logDir;
        private static string _logPath;
        private static string _statePath;
        private static string _listenPrefix;
        private static string _sqlInstance;
        private static string _sqlDatabase;
        private static string _restartShortcutPath;
        private static string _controlAllowedIps;

        private static int Main(string[] args)
        {
            _baseDir = AppDomain.CurrentDomain.BaseDirectory;
            _logDir = Path.Combine(_baseDir, "logs");
            _logPath = Path.Combine(_logDir, "agent.log");
            _statePath = Path.Combine(_baseDir, "kiosk_agent_state.json");
            _listenPrefix = Environment.GetEnvironmentVariable("KIOSK_AGENT_LISTEN_PREFIX");
            if (String.IsNullOrWhiteSpace(_listenPrefix))
            {
                _listenPrefix = "http://+:3010/";
            }
            _sqlInstance = Environment.GetEnvironmentVariable("KIOSK_SQL_INSTANCE");
            if (String.IsNullOrWhiteSpace(_sqlInstance))
            {
                _sqlInstance = @"localhost\SQLEXPRESS";
            }
            _sqlDatabase = Environment.GetEnvironmentVariable("KIOSK_SQL_DATABASE");
            if (String.IsNullOrWhiteSpace(_sqlDatabase))
            {
                _sqlDatabase = "SuitRepository";
            }
            _restartShortcutPath = Environment.GetEnvironmentVariable("KIOSK_RESTART_SHORTCUT_PATH");
            _controlAllowedIps = Environment.GetEnvironmentVariable("KIOSK_CONTROL_ALLOWED_IPS");
            if (String.IsNullOrWhiteSpace(_controlAllowedIps))
            {
                _controlAllowedIps = "127.0.0.1,::1,100.114.61.65";
            }

            Directory.CreateDirectory(_logDir);
            Json.MaxJsonLength = Int32.MaxValue;
            WriteLog("starting 6ka kiosk agent " + Version + " on " + _listenPrefix);

            var listener = new HttpListener();
            listener.Prefixes.Add(_listenPrefix);

            try
            {
                listener.Start();
            }
            catch (Exception ex)
            {
                WriteLog("listener start failed: " + ex);
                return 1;
            }

            while (listener.IsListening)
            {
                try
                {
                    var context = listener.GetContext();
                    ThreadPool.QueueUserWorkItem(_ => HandleRequest(context));
                }
                catch (Exception ex)
                {
                    WriteLog("listener error: " + ex.Message);
                }
            }

            return 0;
        }

        private static void HandleRequest(HttpListenerContext context)
        {
            try
            {
                var path = context.Request.Url.AbsolutePath;
                var method = context.Request.HttpMethod.ToUpperInvariant();
                WriteLog(method + " " + path + " from " + context.Request.RemoteEndPoint);

                if (method == "GET" && path == "/health")
                {
                    SendJson(context, 200, HealthPayload());
                    return;
                }

                if (method == "GET" && path == "/api/status")
                {
                    SendJson(context, 200, StatusPayload());
                    return;
                }

                if (method == "GET" && path == "/api/sales/incremental")
                {
                    SendJson(context, 200, IncrementalSalesPayload(context.Request));
                    return;
                }

                if (method == "GET" && path == "/api/cash-exception")
                {
                    SendJson(context, 200, CashExceptionPayload(context.Request));
                    return;
                }

                if (method == "GET" && path == "/api/kitchen-log")
                {
                    SendJson(context, 200, KitchenLogPayload(context.Request));
                    return;
                }

                if (method == "POST" && path == "/api/sync/run")
                {
                    SendJson(context, 202, AcceptSyncRun());
                    return;
                }

                if (method == "POST" && path == "/api/control/restart-shortcut")
                {
                    if (!IsControlAllowed(context.Request))
                    {
                        SendJson(context, 403, new Dictionary<string, object> {
                            { "ok", false },
                            { "error", "control request is not allowed from this remote address" }
                        });
                        return;
                    }
                    SendJson(context, 202, RestartShortcutPayload(context.Request));
                    return;
                }

                SendJson(context, 404, new Dictionary<string, object> {
                    { "ok", false },
                    { "error", "not found" }
                });
            }
            catch (Exception ex)
            {
                WriteLog("request error: " + ex);
                try
                {
                    SendJson(context, 500, new Dictionary<string, object> {
                        { "ok", false },
                        { "error", ex.Message }
                    });
                }
                catch
                {
                }
            }
        }

        private static Dictionary<string, object> HealthPayload()
        {
            var now = DateTimeOffset.Now;
            return new Dictionary<string, object> {
                { "ok", true },
                { "service", "6ka-kiosk-agent" },
                { "version", Version },
                { "host", Environment.MachineName },
                { "started_at", StartedAt.ToString("yyyy-MM-ddTHH:mm:sszzz") },
                { "uptime_seconds", (int)(now - StartedAt).TotalSeconds },
                { "time", now.ToString("yyyy-MM-ddTHH:mm:sszzz") }
            };
        }

        private static Dictionary<string, object> StatusPayload()
        {
            var health = HealthPayload();
            var state = ReadState();
            var db = DatabaseStatus();
            if (ToBool(db, "ok") && db.ContainsKey("latest_order_time") && db["latest_order_time"] != null)
            {
                state["source_latest_order_time"] = db["latest_order_time"];
            }

            return new Dictionary<string, object> {
                { "ok", ToBool(db, "ok") },
                { "agent", new Dictionary<string, object> {
                    { "online", true },
                    { "version", Version },
                    { "host", health["host"] },
                    { "started_at", health["started_at"] },
                    { "uptime_seconds", health["uptime_seconds"] }
                }},
                { "database", db },
                { "sync", new Dictionary<string, object> {
                    { "running", ToBool(state, "running") },
                    { "last_success_at", GetOrNull(state, "last_success_at") },
                    { "last_error_at", GetOrNull(state, "last_error_at") },
                    { "last_error", GetOrNull(state, "last_error") },
                    { "source_latest_order_time", GetOrNull(state, "source_latest_order_time") },
                    { "last_exported_order_time", GetOrNull(state, "last_exported_order_time") }
                }}
            };
        }

        private static Dictionary<string, object> DatabaseStatus()
        {
            var sw = Stopwatch.StartNew();
            try
            {
                using (var conn = new SqlConnection("Server=" + _sqlInstance + ";Database=" + _sqlDatabase + ";Integrated Security=True;Connection Timeout=5;"))
                {
                    conn.Open();
                    using (var cmd = conn.CreateCommand())
                    {
                        cmd.CommandTimeout = 10;
                        cmd.CommandText =
                            "select convert(varchar(10), max(BusinessDate), 120) as max_business_date, " +
                            "convert(varchar(19), max(Timestamp), 120) as latest_order_time, " +
                            "count(*) as order_rows from dbo.[Order];";
                        using (var reader = cmd.ExecuteReader())
                        {
                            if (reader.Read())
                            {
                                return new Dictionary<string, object> {
                                    { "ok", true },
                                    { "instance", _sqlInstance },
                                    { "database", _sqlDatabase },
                                    { "latency_ms", (int)sw.ElapsedMilliseconds },
                                    { "latest_order_time", DbString(reader, "latest_order_time") },
                                    { "max_business_date", DbString(reader, "max_business_date") },
                                    { "order_rows", Convert.ToInt64(reader["order_rows"]) }
                                };
                            }
                        }
                    }
                }

                return new Dictionary<string, object> {
                    { "ok", true },
                    { "instance", _sqlInstance },
                    { "database", _sqlDatabase },
                    { "latency_ms", (int)sw.ElapsedMilliseconds },
                    { "latest_order_time", null },
                    { "max_business_date", null },
                    { "order_rows", 0 }
                };
            }
            catch (Exception ex)
            {
                return new Dictionary<string, object> {
                    { "ok", false },
                    { "instance", _sqlInstance },
                    { "database", _sqlDatabase },
                    { "latency_ms", (int)sw.ElapsedMilliseconds },
                    { "error", ex.Message }
                };
            }
        }

        private static Dictionary<string, object> AcceptSyncRun()
        {
            var state = ReadState();
            state["running"] = false;
            state["last_error_at"] = DateTimeOffset.Now.ToString("yyyy-MM-ddTHH:mm:sszzz");
            state["last_error"] = "sync worker not implemented yet";
            WriteState(state);
            return new Dictionary<string, object> {
                { "ok", true },
                { "accepted", true },
                { "job_id", DateTime.Now.ToString("yyyyMMdd-HHmmss") },
                { "message", "sync worker not implemented yet" }
            };
        }

        private static Dictionary<string, object> RestartShortcutPayload(HttpListenerRequest request)
        {
            var shortcutPath = ResolveRestartShortcutPath();
            if (String.IsNullOrWhiteSpace(shortcutPath))
            {
                throw new InvalidOperationException("restart shortcut not found; set KIOSK_RESTART_SHORTCUT_PATH");
            }

            var launchedAt = DateTimeOffset.Now.ToString("yyyy-MM-ddTHH:mm:sszzz");
            var startInfo = new ProcessStartInfo();
            startInfo.FileName = shortcutPath;
            startInfo.WorkingDirectory = Path.GetDirectoryName(shortcutPath);
            startInfo.UseShellExecute = true;
            startInfo.WindowStyle = ProcessWindowStyle.Hidden;

            var process = Process.Start(startInfo);
            WriteLog("restart shortcut launched from " + request.RemoteEndPoint + ": " + shortcutPath);

            return new Dictionary<string, object> {
                { "ok", true },
                { "accepted", true },
                { "action", "restart_shortcut" },
                { "shortcut_path", shortcutPath },
                { "launched_at", launchedAt },
                { "process_id", process == null ? (object)null : process.Id }
            };
        }

        private static bool IsControlAllowed(HttpListenerRequest request)
        {
            var remote = request.RemoteEndPoint == null ? "" : request.RemoteEndPoint.Address.ToString();
            if (String.IsNullOrWhiteSpace(remote))
            {
                return false;
            }

            var allowed = (_controlAllowedIps ?? "").Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries);
            foreach (var raw in allowed)
            {
                var ip = raw.Trim();
                if (String.Equals(ip, remote, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
                if (remote.StartsWith("::ffff:", StringComparison.OrdinalIgnoreCase) &&
                    String.Equals(ip, remote.Substring(7), StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }

            WriteLog("control denied from " + remote + " allowed=" + _controlAllowedIps);
            return false;
        }

        private static string ResolveRestartShortcutPath()
        {
            if (!String.IsNullOrWhiteSpace(_restartShortcutPath))
            {
                var configuredPath = Environment.ExpandEnvironmentVariables(_restartShortcutPath.Trim().Trim('"'));
                if (File.Exists(configuredPath))
                {
                    return configuredPath;
                }
                throw new FileNotFoundException("configured restart shortcut not found", configuredPath);
            }

            var desktopDirs = new List<string>();
            AddDirectory(desktopDirs, Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory));
            AddDirectory(desktopDirs, Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory));
            AddDirectory(desktopDirs, Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Desktop"));
            AddDirectory(desktopDirs, @"C:\Users\Public\Desktop");

            var patterns = new[] {
                "*" + "\u91cd\u65b0\u555f\u52d5" + "*.lnk",
                "*" + "\u91cd\u555f" + "*.lnk",
                "*restart*.lnk",
                "*reboot*.lnk"
            };

            foreach (var dir in desktopDirs)
            {
                if (!Directory.Exists(dir))
                {
                    continue;
                }
                foreach (var pattern in patterns)
                {
                    var matches = Directory.GetFiles(dir, pattern);
                    if (matches.Length > 0)
                    {
                        return matches[0];
                    }
                }
            }

            return null;
        }

        private static void AddDirectory(List<string> dirs, string path)
        {
            if (String.IsNullOrWhiteSpace(path))
            {
                return;
            }
            foreach (var existing in dirs)
            {
                if (String.Equals(existing, path, StringComparison.OrdinalIgnoreCase))
                {
                    return;
                }
            }
            dirs.Add(path);
        }

        private static Dictionary<string, object> IncrementalSalesPayload(HttpListenerRequest request)
        {
            var query = HttpUtility.ParseQueryString(request.Url.Query);
            var since = query["since"];
            var daysText = query["days"];
            int days = 2;
            if (!String.IsNullOrWhiteSpace(daysText))
            {
                Int32.TryParse(daysText, out days);
                if (days < 1) days = 1;
                if (days > 31) days = 31;
            }

            var minBusinessDate = DateTime.Today.AddDays(-(days - 1)).ToString("yyyy-MM-dd");
            var where = new StringBuilder("where o.BusinessDate >= @minBusinessDate");
            if (!String.IsNullOrWhiteSpace(since))
            {
                where.Append(" and o.Timestamp >= @since");
            }

            using (var conn = new SqlConnection("Server=" + _sqlInstance + ";Database=" + _sqlDatabase + ";Integrated Security=True;Connection Timeout=5;"))
            {
                conn.Open();
                var orders = QueryRows(
                    conn,
                    "select convert(varchar(36), o.Guid) as guid, o.ID as id, o.Provider as provider, o.Type as type, " +
                    "o.Status as status, o.TotalAmount as total_amount, convert(varchar(36), o.KioskGuid) as kiosk_guid, " +
                    "convert(varchar(10), o.BusinessDate, 120) as business_date, convert(varchar(36), o.StoreGuid) as store_guid, " +
                    "convert(varchar(19), o.OrderTime, 120) as order_time, convert(varchar(19), o.VoidTime, 120) as void_time, " +
                    "convert(varchar(19), o.Timestamp, 120) as timestamp, o.DisplayID as display_id " +
                    "from dbo.[Order] o " + where + " order by o.BusinessDate, o.Timestamp",
                    since,
                    minBusinessDate);

                var products = QueryRows(
                    conn,
                    "select convert(varchar(36), op.OrderGuid) as order_guid, convert(varchar(36), op.Parent) as parent, " +
                    "convert(varchar(36), op.Guid) as guid, op.ID as id, op.Name as name, op.Type as type, op.Tax as tax, " +
                    "op.UnitPrice as unit_price, op.AdditionalPrice as additional_price, op.Quantity as quantity, " +
                    "op.TotalPrice as total_price, op.Sequence as sequence, convert(varchar(36), op.StoreGuid) as store_guid, " +
                    "convert(varchar(19), op.Timestamp, 120) as timestamp " +
                    "from dbo.OrderProduct op join dbo.[Order] o on o.Guid = op.OrderGuid " + where + " order by op.Timestamp",
                    since,
                    minBusinessDate);

                var payments = QueryRows(
                    conn,
                    "select convert(varchar(36), op.OrderGuid) as order_guid, convert(varchar(36), op.Guid) as guid, " +
                    "op.PaymentTypeID as payment_type_id, op.Amount as amount, op.RedeemAmount as redeem_amount, " +
                    "op.[Change] as change_amount, convert(varchar(36), op.KioskGuid) as kiosk_guid, " +
                    "convert(varchar(36), op.StoreGuid) as store_guid, convert(varchar(19), op.Timestamp, 120) as timestamp " +
                    "from dbo.OrderPayment op join dbo.[Order] o on o.Guid = op.OrderGuid " + where + " order by op.Timestamp",
                    since,
                    minBusinessDate);

                var productCategories = QueryRows(
                    conn,
                    "select ID as id, Name as name, cast(Enabled as int) as enabled, Sequence as sequence, " +
                    "convert(varchar(36), StoreGuid) as store_guid, convert(varchar(19), Timestamp, 120) as timestamp " +
                    "from dbo.ProductCategory order by Sequence, ID",
                    null,
                    minBusinessDate);

                var productCategoryItems = QueryRows(
                    conn,
                    "select ProductCategoryID as product_category_id, ProductID as product_id, Sequence as sequence, " +
                    "convert(varchar(36), StoreGuid) as store_guid, convert(varchar(19), Timestamp, 120) as timestamp " +
                    "from dbo.ProductCategoryItem order by ProductCategoryID, Sequence, ProductID",
                    null,
                    minBusinessDate);

                var paymentTypes = QueryRows(
                    conn,
                    "select ID as id, Name as name, Type as type, convert(varchar(19), Timestamp, 120) as timestamp " +
                    "from dbo.PaymentType order by ID",
                    null,
                    minBusinessDate);

                var status = DatabaseStatus();
                return new Dictionary<string, object> {
                    { "ok", true },
                    { "generated_at", DateTimeOffset.Now.ToString("yyyy-MM-ddTHH:mm:sszzz") },
                    { "since", since },
                    { "min_business_date", minBusinessDate },
                    { "source_latest_order_time", status.ContainsKey("latest_order_time") ? status["latest_order_time"] : null },
                    { "orders", orders },
                    { "order_products", products },
                    { "order_payments", payments },
                    { "product_categories", productCategories },
                    { "product_category_items", productCategoryItems },
                    { "payment_types", paymentTypes }
                };
            }
        }

        private static Dictionary<string, object> CashExceptionPayload(HttpListenerRequest request)
        {
            var query = HttpUtility.ParseQueryString(request.Url.Query);
            var dateText = query["date"];
            DateTime parsedDate;
            if (String.IsNullOrWhiteSpace(dateText) ||
                !DateTime.TryParseExact(dateText, "yyyy-MM-dd", null, System.Globalization.DateTimeStyles.None, out parsedDate))
            {
                throw new InvalidOperationException("date must be YYYY-MM-DD");
            }

            var targetDate = parsedDate.ToString("yyyy-MM-dd");
            var path = Path.Combine(@"C:\ProtechFile", targetDate, "CashException", targetDate + "-log.txt");
            var lines = new List<string>();
            var exists = File.Exists(path);
            if (exists)
            {
                lines.AddRange(File.ReadAllLines(path, Encoding.Default));
            }

            return new Dictionary<string, object> {
                { "ok", true },
                { "date", targetDate },
                { "source", path },
                { "exists", exists },
                { "line_count", lines.Count },
                { "lines", lines }
            };
        }

        private static Dictionary<string, object> KitchenLogPayload(HttpListenerRequest request)
        {
            var query = HttpUtility.ParseQueryString(request.Url.Query);
            var dateText = query["date"];
            DateTime parsedDate;
            if (String.IsNullOrWhiteSpace(dateText) ||
                !DateTime.TryParseExact(dateText, "yyyy-MM-dd", null, System.Globalization.DateTimeStyles.None, out parsedDate))
            {
                throw new InvalidOperationException("date must be YYYY-MM-DD");
            }

            var targetDate = parsedDate.ToString("yyyy-MM-dd");
            var folder = Path.Combine(@"C:\ProtechFile", targetDate, "DeviceLog", "Kitchen");
            var files = new List<Dictionary<string, object>>();
            var exists = Directory.Exists(folder);
            if (exists)
            {
                var paths = Directory.GetFiles(folder, "*.txt");
                Array.Sort(paths, StringComparer.OrdinalIgnoreCase);
                foreach (var path in paths)
                {
                    var info = new FileInfo(path);
                    var ageSeconds = Math.Max(0, (int)(DateTime.Now - info.LastWriteTime).TotalSeconds);
                    var stable = info.Length > 0 && ageSeconds >= 2;
                    var lines = stable ? new List<string>(File.ReadAllLines(path, Encoding.Default)) : new List<string>();
                    files.Add(new Dictionary<string, object> {
                        { "file_name", info.Name },
                        { "size", info.Length },
                        { "last_write_time", info.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss") },
                        { "age_seconds", ageSeconds },
                        { "stable", stable },
                        { "lines", lines }
                    });
                }
            }

            return new Dictionary<string, object> {
                { "ok", true },
                { "date", targetDate },
                { "source", folder },
                { "exists", exists },
                { "file_count", files.Count },
                { "files", files }
            };
        }

        private static List<Dictionary<string, object>> QueryRows(SqlConnection conn, string sql, string since, string minBusinessDate)
        {
            using (var cmd = conn.CreateCommand())
            {
                cmd.CommandTimeout = 10;
                cmd.CommandText = sql;
                cmd.Parameters.AddWithValue("@minBusinessDate", minBusinessDate);
                if (!String.IsNullOrWhiteSpace(since))
                {
                    cmd.Parameters.AddWithValue("@since", since);
                }
                using (var reader = cmd.ExecuteReader())
                {
                    var rows = new List<Dictionary<string, object>>();
                    while (reader.Read())
                    {
                        var row = new Dictionary<string, object>();
                        for (var i = 0; i < reader.FieldCount; i++)
                        {
                            var value = reader.GetValue(i);
                            row[reader.GetName(i)] = value == DBNull.Value ? null : value;
                        }
                        rows.Add(row);
                    }
                    return rows;
                }
            }
        }

        private static Dictionary<string, object> ReadState()
        {
            var defaults = new Dictionary<string, object> {
                { "running", false },
                { "last_success_at", null },
                { "last_error_at", null },
                { "last_error", null },
                { "source_latest_order_time", null },
                { "last_exported_order_time", null }
            };

            try
            {
                if (!File.Exists(_statePath))
                {
                    WriteState(defaults);
                    return defaults;
                }

                var raw = File.ReadAllText(_statePath, Encoding.UTF8);
                var state = Json.Deserialize<Dictionary<string, object>>(raw);
                foreach (var item in defaults)
                {
                    if (!state.ContainsKey(item.Key))
                    {
                        state[item.Key] = item.Value;
                    }
                }
                return state;
            }
            catch (Exception ex)
            {
                defaults["last_error_at"] = DateTimeOffset.Now.ToString("yyyy-MM-ddTHH:mm:sszzz");
                defaults["last_error"] = ex.Message;
                return defaults;
            }
        }

        private static void WriteState(Dictionary<string, object> state)
        {
            WriteJsonAtomic(_statePath, state);
        }

        private static void WriteJsonAtomic(string path, object payload)
        {
            var directory = Path.GetDirectoryName(path);
            if (!String.IsNullOrWhiteSpace(directory))
            {
                Directory.CreateDirectory(directory);
            }

            var tempPath = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            File.WriteAllText(tempPath, Json.Serialize(payload), Encoding.UTF8);
            if (File.Exists(path))
            {
                File.Replace(tempPath, path, null);
            }
            else
            {
                File.Move(tempPath, path);
            }
        }

        private static void SendJson(HttpListenerContext context, int statusCode, object payload)
        {
            var json = Json.Serialize(payload);
            var bytes = Encoding.UTF8.GetBytes(json);
            context.Response.StatusCode = statusCode;
            context.Response.ContentType = "application/json; charset=utf-8";
            context.Response.ContentLength64 = bytes.Length;
            context.Response.OutputStream.Write(bytes, 0, bytes.Length);
            context.Response.OutputStream.Close();
        }

        private static string DbString(SqlDataReader reader, string name)
        {
            var value = reader[name];
            if (value == DBNull.Value)
            {
                return null;
            }
            return Convert.ToString(value);
        }

        private static object GetOrNull(Dictionary<string, object> data, string key)
        {
            return data.ContainsKey(key) ? data[key] : null;
        }

        private static bool ToBool(Dictionary<string, object> data, string key)
        {
            if (!data.ContainsKey(key) || data[key] == null)
            {
                return false;
            }
            try
            {
                return Convert.ToBoolean(data[key]);
            }
            catch
            {
                return false;
            }
        }

        private static void WriteLog(string message)
        {
            try
            {
                Directory.CreateDirectory(_logDir);
                var line = "[" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "] " + message + Environment.NewLine;
                File.AppendAllText(_logPath, line, Encoding.UTF8);
                var info = new FileInfo(_logPath);
                if (info.Exists && info.Length > 5 * 1024 * 1024)
                {
                    var archive = Path.Combine(_logDir, "agent-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".log");
                    File.Move(_logPath, archive);
                }
            }
            catch
            {
            }
        }
    }
}
