using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using System.Web.Script.Serialization;

namespace SixKa.KioskMonitorAgent
{
    internal static class Program
    {
        private const string Version = "0.1.0";
        private const int DefaultPort = 9581;
        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
        private static readonly Encoding Utf8NoBom = new UTF8Encoding(false);
        private static readonly DateTime StartedAt = DateTime.Now;
        private static string _baseDir;
        private static string _logPath;
        private static string _token;

        [STAThread]
        private static int Main(string[] args)
        {
            Json.MaxJsonLength = Int32.MaxValue;
            TrySetProcessDpiAware();
            _baseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            _logPath = Path.Combine(_baseDir, "kiosk-monitor-agent.log");
            _token = Environment.GetEnvironmentVariable("KIOSK_MONITOR_TOKEN") ?? "";
            int port = ReadInt("KIOSK_MONITOR_PORT", DefaultPort);
            string prefix = "http://+:" + port + "/";

            HttpListener listener = new HttpListener();
            listener.Prefixes.Add(prefix);
            try
            {
                listener.Start();
                Log("KioskMonitorAgent " + Version + " listening on " + prefix);
            }
            catch (Exception ex)
            {
                Log("listen failed: " + ex);
                return 1;
            }

            while (true)
            {
                try
                {
                    HttpListenerContext context = listener.GetContext();
                    ThreadPool.QueueUserWorkItem(_ => HandleContext(context));
                }
                catch (Exception ex)
                {
                    Log("accept failed: " + ex.Message);
                    Thread.Sleep(500);
                }
            }
        }

        private static void HandleContext(HttpListenerContext context)
        {
            try
            {
                string path = context.Request.Url.AbsolutePath;
                if (context.Request.HttpMethod == "OPTIONS")
                {
                    WriteEmpty(context, 204);
                    return;
                }
                if (context.Request.HttpMethod != "GET")
                {
                    WriteJson(context, 405, Error("method not allowed"));
                    return;
                }
                if (path == "/health")
                {
                    WriteJson(context, 200, new Dictionary<string, object> {
                        { "ok", true },
                        { "service", "6ka-kiosk-monitor-agent" },
                        { "version", Version },
                    });
                    return;
                }
                if (!ValidateToken(context.Request))
                {
                    WriteJson(context, 401, Error("invalid token"));
                    return;
                }

                if (path == "/api/status")
                {
                    WriteJson(context, 200, StatusPayload());
                    return;
                }
                if (path == "/api/screenshot")
                {
                    WriteScreenshot(context);
                    return;
                }
                if (path == "/api/processes")
                {
                    WriteJson(context, 200, new Dictionary<string, object> {
                        { "ok", true },
                        { "processes", ProcessPayload() },
                    });
                    return;
                }
                if (path == "/api/windows")
                {
                    WriteJson(context, 200, new Dictionary<string, object> {
                        { "ok", true },
                        { "windows", WindowPayload(context.Request.QueryString["include_empty"] == "1") },
                    });
                    return;
                }
                if (path == "/api/desktop")
                {
                    WriteJson(context, 200, new Dictionary<string, object> {
                        { "ok", true },
                        { "items", DesktopPayload() },
                    });
                    return;
                }
                if (path == "/api/logs")
                {
                    WriteJson(context, 200, LogPayload(context.Request.QueryString["name"], ReadInt(context.Request.QueryString["lines"], 80, 1, 500)));
                    return;
                }

                WriteJson(context, 404, Error("not found"));
            }
            catch (Exception ex)
            {
                Log("request failed: " + ex);
                TryWriteJson(context, 500, Error(ex.Message));
            }
        }

        private static bool ValidateToken(HttpListenerRequest request)
        {
            if (String.IsNullOrEmpty(_token)) return true;
            string header = request.Headers["X-Monitor-Token"];
            string query = request.QueryString["token"];
            return String.Equals(header, _token, StringComparison.Ordinal) || String.Equals(query, _token, StringComparison.Ordinal);
        }

        private static Dictionary<string, object> StatusPayload()
        {
            Rectangle bounds = SystemInformation.VirtualScreen;
            return new Dictionary<string, object> {
                { "ok", true },
                { "version", Version },
                { "host", Environment.MachineName },
                { "started_at", StartedAt.ToString("yyyy-MM-ddTHH:mm:sszzz") },
                { "uptime_seconds", (int)(DateTime.Now - StartedAt).TotalSeconds },
                { "user", Environment.UserDomainName + "\\" + Environment.UserName },
                { "screen", new Dictionary<string, object> {
                    { "x", bounds.Left },
                    { "y", bounds.Top },
                    { "width", bounds.Width },
                    { "height", bounds.Height },
                } },
                { "cursor", CursorPayload() },
                { "drives", DrivePayload() },
            };
        }

        private static Dictionary<string, object> CursorPayload()
        {
            POINT point;
            if (!GetCursorPos(out point))
            {
                return new Dictionary<string, object> { { "ok", false } };
            }
            return new Dictionary<string, object> {
                { "ok", true },
                { "x", point.X },
                { "y", point.Y },
            };
        }

        private static List<object> DrivePayload()
        {
            List<object> result = new List<object>();
            foreach (DriveInfo drive in DriveInfo.GetDrives())
            {
                try
                {
                    if (!drive.IsReady) continue;
                    result.Add(new Dictionary<string, object> {
                        { "name", drive.Name },
                        { "type", drive.DriveType.ToString() },
                        { "total_bytes", drive.TotalSize },
                        { "free_bytes", drive.AvailableFreeSpace },
                    });
                }
                catch
                {
                }
            }
            return result;
        }

        private static void WriteScreenshot(HttpListenerContext context)
        {
            Rectangle bounds = SystemInformation.VirtualScreen;
            int maxWidth = ReadInt(context.Request.QueryString["max_width"], 0, 0, 4000);
            using (Bitmap source = new Bitmap(bounds.Width, bounds.Height))
            using (Graphics graphics = Graphics.FromImage(source))
            using (MemoryStream ms = new MemoryStream())
            {
                graphics.CopyFromScreen(bounds.Left, bounds.Top, 0, 0, bounds.Size);
                Bitmap output = source;
                Bitmap resized = null;
                try
                {
                    if (maxWidth > 0 && maxWidth < source.Width)
                    {
                        int height = Math.Max(1, (int)Math.Round(source.Height * (maxWidth / (double)source.Width)));
                        resized = new Bitmap(maxWidth, height);
                        using (Graphics rg = Graphics.FromImage(resized))
                        {
                            rg.SmoothingMode = SmoothingMode.HighQuality;
                            rg.InterpolationMode = InterpolationMode.HighQualityBicubic;
                            rg.PixelOffsetMode = PixelOffsetMode.HighQuality;
                            rg.DrawImage(source, 0, 0, maxWidth, height);
                        }
                        output = resized;
                    }
                    output.Save(ms, System.Drawing.Imaging.ImageFormat.Png);
                }
                finally
                {
                    if (resized != null) resized.Dispose();
                }
                byte[] body = ms.ToArray();
                context.Response.StatusCode = 200;
                context.Response.ContentType = "image/png";
                context.Response.ContentLength64 = body.Length;
                context.Response.Headers["Cache-Control"] = "no-store";
                context.Response.Headers["Access-Control-Allow-Origin"] = "*";
                context.Response.OutputStream.Write(body, 0, body.Length);
                context.Response.OutputStream.Close();
            }
        }

        private static List<object> ProcessPayload()
        {
            List<object> result = new List<object>();
            foreach (Process process in Process.GetProcesses())
            {
                try
                {
                    string path = "";
                    string started = "";
                    try { path = process.MainModule.FileName; } catch { }
                    try { started = process.StartTime.ToString("yyyy-MM-dd HH:mm:ss"); } catch { }
                    result.Add(new Dictionary<string, object> {
                        { "pid", process.Id },
                        { "name", process.ProcessName },
                        { "path", path },
                        { "start_time", started },
                        { "main_window_title", process.MainWindowTitle },
                    });
                }
                catch
                {
                }
            }
            result.Sort((a, b) => String.Compare(Convert.ToString(((Dictionary<string, object>)a)["name"]), Convert.ToString(((Dictionary<string, object>)b)["name"]), StringComparison.OrdinalIgnoreCase));
            return result;
        }

        private static List<object> WindowPayload(bool includeEmpty)
        {
            List<object> result = new List<object>();
            EnumWindows(delegate(IntPtr hwnd, IntPtr lParam)
            {
                if (!IsWindowVisible(hwnd)) return true;
                int length = GetWindowTextLength(hwnd);
                if (length <= 0 && !includeEmpty) return true;

                StringBuilder titleBuilder = new StringBuilder(Math.Max(length + 1, 256));
                GetWindowText(hwnd, titleBuilder, titleBuilder.Capacity);
                string title = titleBuilder.ToString();
                if (String.IsNullOrWhiteSpace(title) && !includeEmpty) return true;

                int pid;
                GetWindowThreadProcessId(hwnd, out pid);
                string name = "";
                try { name = Process.GetProcessById(pid).ProcessName; } catch { }
                RECT rect;
                GetWindowRect(hwnd, out rect);
                result.Add(new Dictionary<string, object> {
                    { "hwnd", hwnd.ToInt64() },
                    { "pid", pid },
                    { "process", name },
                    { "title", title },
                    { "rect", new Dictionary<string, object> {
                        { "left", rect.Left },
                        { "top", rect.Top },
                        { "right", rect.Right },
                        { "bottom", rect.Bottom },
                        { "width", rect.Right - rect.Left },
                        { "height", rect.Bottom - rect.Top },
                    } },
                });
                return true;
            }, IntPtr.Zero);
            return result;
        }

        private static List<object> DesktopPayload()
        {
            List<object> result = new List<object>();
            string[] dirs = new string[] {
                Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Desktop"),
                @"C:\Users\Public\Desktop",
            };
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string dir in dirs)
            {
                if (String.IsNullOrWhiteSpace(dir) || seen.Contains(dir)) continue;
                seen.Add(dir);
                if (!Directory.Exists(dir)) continue;
                foreach (FileInfo file in new DirectoryInfo(dir).GetFiles())
                {
                    result.Add(new Dictionary<string, object> {
                        { "directory", dir },
                        { "name", file.Name },
                        { "path", file.FullName },
                        { "extension", file.Extension },
                        { "length", file.Length },
                        { "last_write_time", file.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss") },
                    });
                }
            }
            return result;
        }

        private static Dictionary<string, object> LogPayload(string name, int lines)
        {
            Dictionary<string, string> logs = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase) {
                { "ticket-pad", @"C:\6KA\ticket-pad-controller\ticket-pad-controller.log" },
                { "kiosk-agent", @"C:\6KA\kiosk-agent\logs\agent.log" },
                { "cash-finance-agent", @"C:\6KA\cash-finance-agent\logs\agent.log" },
                { "monitor-agent", _logPath },
            };
            if (String.IsNullOrWhiteSpace(name)) name = "monitor-agent";
            string path;
            if (!logs.TryGetValue(name, out path))
            {
                return new Dictionary<string, object> {
                    { "ok", false },
                    { "message", "unknown log name" },
                    { "available", new List<string>(logs.Keys) },
                };
            }
            return new Dictionary<string, object> {
                { "ok", true },
                { "name", name },
                { "path", path },
                { "exists", File.Exists(path) },
                { "lines", TailLines(path, lines) },
            };
        }

        private static List<string> TailLines(string path, int lines)
        {
            Queue<string> queue = new Queue<string>();
            if (!File.Exists(path)) return new List<string>();
            using (FileStream fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            using (StreamReader reader = new StreamReader(fs, Encoding.UTF8, true))
            {
                string line;
                while ((line = reader.ReadLine()) != null)
                {
                    queue.Enqueue(line);
                    while (queue.Count > lines) queue.Dequeue();
                }
            }
            return new List<string>(queue);
        }

        private static Dictionary<string, object> Error(string message)
        {
            return new Dictionary<string, object> {
                { "ok", false },
                { "message", message },
            };
        }

        private static void TryWriteJson(HttpListenerContext context, int statusCode, object payload)
        {
            try { WriteJson(context, statusCode, payload); } catch { }
        }

        private static void WriteJson(HttpListenerContext context, int statusCode, object payload)
        {
            byte[] body = Utf8NoBom.GetBytes(Json.Serialize(payload));
            context.Response.StatusCode = statusCode;
            context.Response.ContentType = "application/json; charset=utf-8";
            context.Response.ContentLength64 = body.Length;
            context.Response.Headers["Cache-Control"] = "no-store";
            context.Response.Headers["Access-Control-Allow-Origin"] = "*";
            context.Response.OutputStream.Write(body, 0, body.Length);
            context.Response.OutputStream.Close();
        }

        private static void WriteEmpty(HttpListenerContext context, int statusCode)
        {
            context.Response.StatusCode = statusCode;
            context.Response.Headers["Access-Control-Allow-Origin"] = "*";
            context.Response.OutputStream.Close();
        }

        private static int ReadInt(string name, int defaultValue)
        {
            return ReadInt(Environment.GetEnvironmentVariable(name), defaultValue, 1, 65535);
        }

        private static int ReadInt(string value, int defaultValue, int min, int max)
        {
            int parsed;
            if (Int32.TryParse(value, out parsed))
            {
                if (parsed < min) return min;
                if (parsed > max) return max;
                return parsed;
            }
            return defaultValue;
        }

        private static void TrySetProcessDpiAware()
        {
            try { SetProcessDPIAware(); } catch { }
        }

        private static void Log(string message)
        {
            try
            {
                Directory.CreateDirectory(_baseDir);
                File.AppendAllText(_logPath, "[" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "] " + message + Environment.NewLine, Utf8NoBom);
            }
            catch
            {
            }
        }

        private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern bool SetProcessDPIAware();

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool GetCursorPos(out POINT lpPoint);

        [DllImport("user32.dll")]
        private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern bool IsWindowVisible(IntPtr hWnd);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowTextLength(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out int lpdwProcessId);

        [DllImport("user32.dll")]
        private static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

        [StructLayout(LayoutKind.Sequential)]
        private struct POINT
        {
            public int X;
            public int Y;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct RECT
        {
            public int Left;
            public int Top;
            public int Right;
            public int Bottom;
        }
    }
}
