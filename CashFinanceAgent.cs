using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Web;
using System.Data.SQLite;

namespace SixKa.CashFinanceAgent
{
    internal static class Program
    {
        private const string Version = "0.1.0";
        private static readonly DateTimeOffset StartedAt = DateTimeOffset.Now;
        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
        private static string _baseDir;
        private static string _logDir;
        private static string _logPath;
        private static string _listenPrefix;
        private static string _financeDbPath;

        private static int Main(string[] args)
        {
            _baseDir = AppDomain.CurrentDomain.BaseDirectory;
            _logDir = Path.Combine(_baseDir, "logs");
            _logPath = Path.Combine(_logDir, "agent.log");
            Directory.CreateDirectory(_logDir);

            _listenPrefix = Environment.GetEnvironmentVariable("CASH_FINANCE_AGENT_LISTEN_PREFIX");
            if (String.IsNullOrWhiteSpace(_listenPrefix))
            {
                _listenPrefix = "http://+:3012/";
            }

            _financeDbPath = Environment.GetEnvironmentVariable("CASH_FINANCE_DB_PATH");
            if (String.IsNullOrWhiteSpace(_financeDbPath))
            {
                _financeDbPath = @"C:\Protech\Suit.Kiosk\Database\finance.db";
            }

            WriteLog("starting 6ka cash finance agent " + Version + " on " + _listenPrefix + " db=" + _financeDbPath);

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

                if (method == "GET" && path == "/api/finance/status")
                {
                    SendJson(context, 200, StatusPayload());
                    return;
                }

                if (method == "GET" && path == "/api/finance/db")
                {
                    SendFinanceDb(context);
                    return;
                }

                if (method == "GET" && path == "/api/finance/incremental")
                {
                    SendJson(context, 200, IncrementalCashRecordPayload(context.Request));
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
                { "service", "6ka-cash-finance-agent" },
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
            return new Dictionary<string, object> {
                { "ok", File.Exists(_financeDbPath) },
                { "agent", new Dictionary<string, object> {
                    { "online", true },
                    { "version", Version },
                    { "host", health["host"] },
                    { "started_at", health["started_at"] },
                    { "uptime_seconds", health["uptime_seconds"] }
                }},
                { "database", FileStatus(_financeDbPath) },
                { "wal", FileStatus(_financeDbPath + "-wal") },
                { "shm", FileStatus(_financeDbPath + "-shm") }
            };
        }

        private static Dictionary<string, object> IncrementalCashRecordPayload(HttpListenerRequest request)
        {
            var query = HttpUtility.ParseQueryString(request.Url.Query);
            long afterId = 0;
            int limit = 5000;
            if (!String.IsNullOrWhiteSpace(query["after_id"]))
            {
                Int64.TryParse(query["after_id"], out afterId);
                if (afterId < 0) afterId = 0;
            }
            if (!String.IsNullOrWhiteSpace(query["limit"]))
            {
                Int32.TryParse(query["limit"], out limit);
                if (limit < 1) limit = 1;
                if (limit > 50000) limit = 50000;
            }

            var rows = new List<Dictionary<string, object>>();
            long latestId = afterId;
            string latestTimestamp = null;
            var sw = Stopwatch.StartNew();

            using (var conn = new SQLiteConnection("Data Source=" + _financeDbPath + ";Version=3;Read Only=True;Default Timeout=5;"))
            {
                conn.Open();
                using (var cmd = conn.CreateCommand())
                {
                    cmd.CommandTimeout = 10;
                    cmd.CommandText =
                        "select Id, TotalAmount, Quantity, Value, Type, Target, FlowMode, cast(Timestamp as text) as Timestamp " +
                        "from CashRecord where Id > @afterId order by Id limit @limit";
                    cmd.Parameters.AddWithValue("@afterId", afterId);
                    cmd.Parameters.AddWithValue("@limit", limit);
                    using (var reader = cmd.ExecuteReader())
                    {
                        while (reader.Read())
                        {
                            var row = new Dictionary<string, object>();
                            for (var i = 0; i < reader.FieldCount; i++)
                            {
                                var value = reader.GetValue(i);
                                row[reader.GetName(i)] = value == DBNull.Value ? null : value;
                            }
                            latestId = Convert.ToInt64(row["Id"]);
                            latestTimestamp = Convert.ToString(row["Timestamp"]);
                            rows.Add(row);
                        }
                    }
                }

                if (rows.Count == 0)
                {
                    using (var cmd = conn.CreateCommand())
                    {
                        cmd.CommandTimeout = 10;
                        cmd.CommandText = "select max(Id) as latest_id, max(Timestamp) as latest_timestamp from CashRecord";
                        using (var reader = cmd.ExecuteReader())
                        {
                            if (reader.Read())
                            {
                                if (reader["latest_id"] != DBNull.Value)
                                {
                                    latestId = Convert.ToInt64(reader["latest_id"]);
                                }
                                if (reader["latest_timestamp"] != DBNull.Value)
                                {
                                    latestTimestamp = Convert.ToString(reader["latest_timestamp"]);
                                }
                            }
                        }
                    }
                }
            }

            return new Dictionary<string, object> {
                { "ok", true },
                { "generated_at", DateTimeOffset.Now.ToString("yyyy-MM-ddTHH:mm:sszzz") },
                { "after_id", afterId },
                { "limit", limit },
                { "row_count", rows.Count },
                { "latest_id", latestId },
                { "latest_timestamp", latestTimestamp },
                { "has_more", rows.Count >= limit },
                { "latency_ms", (int)sw.ElapsedMilliseconds },
                { "database", FileStatus(_financeDbPath) },
                { "cash_records", rows }
            };
        }

        private static Dictionary<string, object> FileStatus(string path)
        {
            var payload = new Dictionary<string, object> {
                { "path", path },
                { "exists", File.Exists(path) }
            };
            if (!File.Exists(path))
            {
                payload["size_bytes"] = 0;
                payload["last_write_time"] = null;
                return payload;
            }

            var info = new FileInfo(path);
            payload["size_bytes"] = info.Length;
            payload["last_write_time"] = info.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss");
            payload["last_write_time_utc"] = info.LastWriteTimeUtc.ToString("yyyy-MM-ddTHH:mm:ssZ");
            return payload;
        }

        private static void SendFinanceDb(HttpListenerContext context)
        {
            if (!File.Exists(_financeDbPath))
            {
                SendJson(context, 404, new Dictionary<string, object> {
                    { "ok", false },
                    { "error", "finance db not found" },
                    { "path", _financeDbPath }
                });
                return;
            }

            var snapshotDir = Path.Combine(_baseDir, "snapshots");
            Directory.CreateDirectory(snapshotDir);
            var snapshotPath = Path.Combine(snapshotDir, "finance-" + DateTime.Now.ToString("yyyyMMdd-HHmmss-fff") + ".db");
            CopySharedFile(_financeDbPath, snapshotPath);

            var info = new FileInfo(snapshotPath);
            context.Response.StatusCode = 200;
            context.Response.ContentType = "application/octet-stream";
            context.Response.ContentLength64 = info.Length;
            context.Response.Headers["X-Source-Path"] = _financeDbPath;
            context.Response.Headers["X-Source-Last-Write-Time"] = File.GetLastWriteTime(_financeDbPath).ToString("yyyy-MM-dd HH:mm:ss");
            context.Response.Headers["X-Snapshot-Time"] = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");

            using (var input = File.Open(snapshotPath, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                input.CopyTo(context.Response.OutputStream);
            }
            context.Response.OutputStream.Close();

            TryDeleteOldSnapshots(snapshotDir);
        }

        private static void CopySharedFile(string source, string destination)
        {
            using (var input = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (var output = new FileStream(destination, FileMode.Create, FileAccess.Write, FileShare.None))
            {
                input.CopyTo(output);
            }
        }

        private static void TryDeleteOldSnapshots(string snapshotDir)
        {
            try
            {
                var files = new DirectoryInfo(snapshotDir).GetFiles("finance-*.db");
                Array.Sort(files, (a, b) => String.CompareOrdinal(b.Name, a.Name));
                for (var i = 20; i < files.Length; i++)
                {
                    files[i].Delete();
                }
            }
            catch
            {
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
