using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using System.Web.Script.Serialization;

namespace SixKa.TicketPadController
{
    internal static class Program
    {
        private const string Version = "0.2.7";
        private const int DefaultPort = 9580;
        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
        private static readonly Encoding Utf8NoBom = new UTF8Encoding(false);
        private static string _baseDir;
        private static string _staticDir;
        private static string _macroPath;
        private static string _logPath;
        private static string _pin;
        private static bool _dangerMacrosEnabled;
        private static bool _cursorOverlayEnabled;
        private static int _cursorOverlayIdleMs;
        private static string _screenRotation;
        private static TcpListener _listener;
        private static CursorOverlayForm _cursorOverlay;

        [STAThread]
        private static int Main(string[] args)
        {
            TrySetProcessDpiAware();
            Json.MaxJsonLength = Int32.MaxValue;
            _baseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            _staticDir = Path.Combine(_baseDir, "static");
            _macroPath = Path.Combine(_baseDir, "macros.json");
            _logPath = Path.Combine(_baseDir, "ticket-pad-controller.log");
            _pin = Environment.GetEnvironmentVariable("TICKET_PAD_PIN") ?? "";
            _dangerMacrosEnabled = ReadBool("TICKET_PAD_ENABLE_DANGER_MACROS", true);
            _cursorOverlayEnabled = ReadBool("TICKET_PAD_CURSOR_OVERLAY", true);
            _cursorOverlayIdleMs = Math.Max(250, ReadInt("TICKET_PAD_CURSOR_IDLE_MS", 5000));
            _screenRotation = NormalizeRotation(Environment.GetEnvironmentVariable("TICKET_PAD_SCREEN_ROTATION"));
            int port = ReadInt("TICKET_PAD_PORT", DefaultPort);

            if (!File.Exists(Path.Combine(_staticDir, "index.html")) || !File.Exists(Path.Combine(_staticDir, "app.js")))
            {
                Log("missing static assets under " + _staticDir);
                return 2;
            }

            try
            {
                _listener = new TcpListener(IPAddress.Any, port);
                _listener.Start();
                Log("TicketPadController " + Version + " listening on 0.0.0.0:" + port);
            }
            catch (Exception ex)
            {
                Log("listen failed: " + ex);
                return 1;
            }

            Thread serverThread = new Thread(RunServerLoop);
            serverThread.IsBackground = true;
            serverThread.Name = "TicketPadControllerHttp";
            serverThread.Start();

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            _cursorOverlay = new CursorOverlayForm(_cursorOverlayIdleMs, _cursorOverlayEnabled);
            Log("cursor overlay available enabled=" + _cursorOverlayEnabled + " idle_ms=" + _cursorOverlayIdleMs);
            Application.Run(_cursorOverlay);
            return 0;
        }

        private static void RunServerLoop()
        {
            while (true)
            {
                try
                {
                    TcpClient client = _listener.AcceptTcpClient();
                    ThreadPool.QueueUserWorkItem(_ => HandleClient(client));
                }
                catch (Exception ex)
                {
                    Log("accept failed: " + ex.Message);
                    Thread.Sleep(500);
                }
            }
        }

        private static bool ReadBool(string name, bool defaultValue)
        {
            string value = Environment.GetEnvironmentVariable(name);
            if (String.IsNullOrWhiteSpace(value)) return defaultValue;
            value = value.Trim().ToLowerInvariant();
            return value == "1" || value == "true" || value == "yes" || value == "on";
        }

        private static int ReadInt(string name, int defaultValue)
        {
            string value = Environment.GetEnvironmentVariable(name);
            int parsed;
            if (Int32.TryParse(value, out parsed) && parsed > 0 && parsed <= 65535) return parsed;
            return defaultValue;
        }

        private static void TrySetProcessDpiAware()
        {
            try
            {
                SetProcessDPIAware();
            }
            catch
            {
            }
        }

        private static void HandleClient(TcpClient client)
        {
            using (client)
            {
                client.ReceiveTimeout = 8000;
                client.SendTimeout = 8000;
                try
                {
                    using (NetworkStream stream = client.GetStream())
                    {
                        HttpRequest request = ReadRequest(stream);
                        if (request == null)
                        {
                            return;
                        }
                        HandleRequest(stream, request);
                    }
                }
                catch (Exception ex)
                {
                    Log("client failed: " + ex.Message);
                }
            }
        }

        private static HttpRequest ReadRequest(NetworkStream stream)
        {
            List<byte> headerBytes = new List<byte>();
            byte[] one = new byte[1];
            int matched = 0;
            byte[] needle = new byte[] { 13, 10, 13, 10 };
            while (headerBytes.Count < 64 * 1024)
            {
                int read = stream.Read(one, 0, 1);
                if (read <= 0) return null;
                byte b = one[0];
                headerBytes.Add(b);
                if (b == needle[matched])
                {
                    matched++;
                    if (matched == needle.Length) break;
                }
                else
                {
                    matched = b == needle[0] ? 1 : 0;
                }
            }

            string headerText = Encoding.ASCII.GetString(headerBytes.ToArray());
            string[] lines = headerText.Replace("\r\n", "\n").Split('\n');
            if (lines.Length == 0 || String.IsNullOrWhiteSpace(lines[0])) return null;
            string[] first = lines[0].Trim().Split(' ');
            if (first.Length < 2) return null;

            HttpRequest request = new HttpRequest();
            request.Method = first[0].Trim().ToUpperInvariant();
            request.Path = first[1].Trim();
            request.Headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

            for (int i = 1; i < lines.Length; i++)
            {
                string line = lines[i];
                int colon = line.IndexOf(':');
                if (colon <= 0) continue;
                request.Headers[line.Substring(0, colon).Trim()] = line.Substring(colon + 1).Trim();
            }

            int length = 0;
            string lengthText;
            if (request.Headers.TryGetValue("Content-Length", out lengthText))
            {
                Int32.TryParse(lengthText, out length);
            }
            if (length > 0)
            {
                byte[] body = new byte[length];
                int offset = 0;
                while (offset < length)
                {
                    int read = stream.Read(body, offset, length - offset);
                    if (read <= 0) break;
                    offset += read;
                }
                request.Body = Encoding.UTF8.GetString(body, 0, offset);
            }
            else
            {
                request.Body = "";
            }
            return request;
        }

        private static void HandleRequest(NetworkStream stream, HttpRequest request)
        {
            string path = request.Path;
            int queryIndex = path.IndexOf('?');
            if (queryIndex >= 0) path = path.Substring(0, queryIndex);
            path = Uri.UnescapeDataString(path);

            if (request.Method == "GET" && path == "/ws")
            {
                HandleWebSocket(stream, request);
                return;
            }
            if (request.Method == "GET" && (path == "/" || path == "/index.html"))
            {
                WriteFile(stream, Path.Combine(_staticDir, "index.html"), "text/html; charset=utf-8");
                return;
            }
            if (request.Method == "GET" && path == "/app.js")
            {
                WriteFile(stream, Path.Combine(_staticDir, "app.js"), "application/javascript; charset=utf-8");
                return;
            }
            if (request.Method == "GET" && path == "/api/state")
            {
                WriteJson(stream, 200, new Dictionary<string, object> {
                    { "ok", true },
                    { "version", Version },
                    { "pin_required", !String.IsNullOrEmpty(_pin) },
                    { "danger_macros_enabled", _dangerMacrosEnabled },
                    { "cursor_overlay_enabled", _cursorOverlayEnabled },
                    { "cursor_overlay_idle_ms", _cursorOverlayIdleMs },
                    { "screen_rotation", _screenRotation },
                    { "cursor_position", CurrentCursorPosition() },
                    { "virtual_screen", CurrentVirtualScreen() },
                    { "macros", LoadMacros() },
                });
                return;
            }
            if (request.Method == "GET" && path == "/api/screenshot")
            {
                WriteScreenshot(stream);
                return;
            }
            if (request.Method == "POST" && path == "/api/event")
            {
                if (!ValidatePin(request))
                {
                    WriteJson(stream, 401, new Dictionary<string, object> {
                        { "ok", false },
                        { "message", "invalid pin" },
                    });
                    return;
                }
                HandleEvent(stream, request.Body);
                return;
            }
            if (request.Method == "POST" && path == "/api/events")
            {
                if (!ValidatePin(request))
                {
                    WriteJson(stream, 401, new Dictionary<string, object> {
                        { "ok", false },
                        { "message", "invalid pin" },
                    });
                    return;
                }
                HandleEvents(stream, request.Body);
                return;
            }
            if (request.Method == "GET" && path.StartsWith("/static/", StringComparison.OrdinalIgnoreCase))
            {
                string name = path.Substring("/static/".Length).Replace('/', Path.DirectorySeparatorChar);
                if (name.IndexOf("..", StringComparison.Ordinal) >= 0)
                {
                    WriteJson(stream, 400, Error("bad path"));
                    return;
                }
                string filePath = Path.Combine(_staticDir, name);
                if (!File.Exists(filePath))
                {
                    WriteJson(stream, 404, Error("not found"));
                    return;
                }
                WriteFile(stream, filePath, MimeType(filePath));
                return;
            }
            if (request.Method == "GET" && path == "/macros.json")
            {
                WriteFile(stream, _macroPath, "application/json; charset=utf-8");
                return;
            }

            WriteJson(stream, 404, Error("not found"));
        }

        private static bool ValidatePin(HttpRequest request)
        {
            if (String.IsNullOrEmpty(_pin)) return true;
            string actual;
            return request.Headers.TryGetValue("X-Controller-Pin", out actual) && actual == _pin;
        }

        private static object LoadMacros()
        {
            try
            {
                if (!File.Exists(_macroPath)) return new object[0];
                return Json.DeserializeObject(File.ReadAllText(_macroPath, Encoding.UTF8));
            }
            catch (Exception ex)
            {
                Log("macro load failed: " + ex.Message);
                return new object[0];
            }
        }

        private static void HandleEvent(NetworkStream stream, string body)
        {
            Dictionary<string, object> payload;
            try
            {
                payload = Json.Deserialize<Dictionary<string, object>>(body ?? "{}");
            }
            catch
            {
                WriteJson(stream, 400, Error("bad json"));
                return;
            }

            try
            {
                WriteJson(stream, 200, ExecuteSinglePayload(payload));
            }
            catch (Exception ex)
            {
                string type = GetString(payload, "type");
                Log("event failed type=" + type + " error=" + ex.Message);
                WriteJson(stream, 400, ErrorWithId(payload, ex.Message));
            }
        }

        private static void HandleEvents(NetworkStream stream, string body)
        {
            Dictionary<string, object> payload;
            try
            {
                payload = Json.Deserialize<Dictionary<string, object>>(body ?? "{}");
            }
            catch
            {
                WriteJson(stream, 400, Error("bad json"));
                return;
            }

            try
            {
                WriteJson(stream, 200, ExecuteBatchPayload(payload));
            }
            catch (Exception ex)
            {
                Log("batch event failed error=" + ex.Message);
                WriteJson(stream, 400, ErrorWithId(payload, ex.Message));
            }
        }

        private static Dictionary<string, object> ExecuteSinglePayload(Dictionary<string, object> payload)
        {
            string type = GetString(payload, "type");
            string message = ExecuteEvent(type, payload);
            if (type == "run_macro")
            {
                Log("event ok type=run_macro id=" + GetString(payload, "id") + " message=" + message);
            }
            Dictionary<string, object> result = new Dictionary<string, object> {
                { "ok", true },
                { "message", message },
            };
            AddPayloadId(result, payload);
            return result;
        }

        private static Dictionary<string, object> ExecuteBatchPayload(Dictionary<string, object> payload)
        {
            List<object> events = GetObjectList(payload, "events");
            List<string> messages = new List<string>();
            for (int index = 0; index < events.Count; index++)
            {
                Dictionary<string, object> item = ToPayload(events[index]);
                string type = GetString(item, "type");
                string message = ExecuteEvent(type, item);
                messages.Add(message);
                if (type == "run_macro")
                {
                    Log("event ok type=run_macro id=" + GetString(item, "id") + " message=" + message);
                }
            }

            Dictionary<string, object> result = new Dictionary<string, object> {
                { "ok", true },
                { "count", events.Count },
                { "messages", messages },
            };
            AddPayloadId(result, payload);
            return result;
        }

        private static string ExecuteEvent(string type, Dictionary<string, object> payload)
        {
            if (type == "mouse_move")
            {
                int dx = GetInt(payload, "dx");
                int dy = GetInt(payload, "dy");
                ApplyScreenRotation(ref dx, ref dy);
                SendMouseMove(dx, dy);
                NotifyCursorOverlay(false, false);
                return "mouse move";
            }
            if (type == "mouse_scroll")
            {
                double dy = GetDouble(payload, "dy");
                SendMouseWheel((int)Math.Round(dy * 24));
                NotifyCursorOverlay(false, false);
                return "mouse scroll";
            }
            if (type == "mouse_click")
            {
                string button = NormalizeButton(GetString(payload, "button"));
                SendMouseButton(button, true);
                NotifyCursorOverlay(true, true);
                Thread.Sleep(45);
                SendMouseButton(button, false);
                NotifyCursorOverlay(true, false);
                return button + " click";
            }
            if (type == "mouse_down")
            {
                string button = NormalizeButton(GetString(payload, "button"));
                SendMouseButton(button, true);
                NotifyCursorOverlay(true, true);
                return button + " down";
            }
            if (type == "mouse_up")
            {
                string button = NormalizeButton(GetString(payload, "button"));
                SendMouseButton(button, false);
                NotifyCursorOverlay(true, false);
                return button + " up";
            }
            if (type == "key_press")
            {
                string key = GetString(payload, "key");
                SendKeyPress(key);
                return "key " + key;
            }
            if (type == "modifier_down")
            {
                string key = GetString(payload, "key");
                SendKeyDown(KeyToVk(key));
                return key + " down";
            }
            if (type == "modifier_up")
            {
                string key = GetString(payload, "key");
                SendKeyUp(KeyToVk(key));
                return key + " up";
            }
            if (type == "hotkey")
            {
                List<string> keys = GetStringList(payload, "keys");
                SendHotkey(keys);
                return "hotkey " + String.Join("+", keys.ToArray());
            }
            if (type == "paste_text")
            {
                string text = GetString(payload, "text");
                SendUnicodeText(text);
                return "text sent";
            }
            if (type == "run_macro")
            {
                string id = GetString(payload, "id");
                bool confirmed = GetBool(payload, "confirmed");
                return RunMacro(id, confirmed);
            }
            if (type == "cursor_overlay")
            {
                bool enabled = GetBool(payload, "enabled");
                _cursorOverlayEnabled = enabled;
                SetCursorOverlayEnabled(enabled);
                return "cursor overlay " + (enabled ? "enabled" : "disabled");
            }
            if (type == "cursor_center")
            {
                CenterCursor();
                NotifyCursorOverlay(true, false);
                return "cursor centered";
            }
            if (type == "screen_rotation")
            {
                _screenRotation = NormalizeRotation(GetString(payload, "value"));
                return "screen rotation " + _screenRotation;
            }
            throw new InvalidOperationException("unknown event type: " + type);
        }

        private static string RunMacro(string id, bool confirmed)
        {
            if (!_dangerMacrosEnabled)
            {
                return "danger macros disabled";
            }
            if (!confirmed && (id == "restart_ticket_app" || id == "close_ticket_app" || id == "restart_machine" || id == "shutdown_machine"))
            {
                throw new InvalidOperationException("confirmation required");
            }
            if (id == "enter_admin")
            {
                SendHotkey(new List<string> { "ctrl", "shift", "a" });
                return "enter admin hotkey sent";
            }
            if (id == "return_backend")
            {
                ReturnToBackend();
                return "return backend requested";
            }
            if (id == "close_ticket_app")
            {
                KillProcessByName("Kiosk.Standard.App");
                return "ticket app close requested";
            }
            if (id == "restart_ticket_app")
            {
                KillProcessByName("Kiosk.Standard.App");
                Thread.Sleep(1000);
                StartTicketApp();
                return "ticket app restart requested";
            }
            if (id == "restart_machine")
            {
                RunDesktopShortcut("TICKET_PAD_RESTART_SHORTCUT", "\u91cd\u65b0\u555f\u52d5.lnk");
                return "machine restart shortcut launched";
            }
            if (id == "shutdown_machine")
            {
                RunDesktopShortcut("TICKET_PAD_SHUTDOWN_SHORTCUT", "\u95dc\u6a5f.lnk");
                return "machine shutdown shortcut launched";
            }
            return "macro not implemented: " + id;
        }

        private static void StartTicketApp()
        {
            string shortcut = Environment.GetEnvironmentVariable("TICKET_APP_SHORTCUT");
            if (!String.IsNullOrWhiteSpace(shortcut))
            {
                StartShellPath(Environment.ExpandEnvironmentVariables(shortcut.Trim().Trim('"')));
                return;
            }

            try
            {
                RunDesktopShortcut("TICKET_APP_SHORTCUT", "Suit.Kiosk.exe.lnk");
                return;
            }
            catch
            {
            }

            string exe = Environment.GetEnvironmentVariable("TICKET_APP_EXE");
            if (String.IsNullOrWhiteSpace(exe)) exe = @"C:\Protech\Suit.Kiosk\Kiosk.Standard.App.exe";
            StartShellPath(exe);
        }

        private static void RunDesktopShortcut(string envName, string shortcutName)
        {
            string configured = Environment.GetEnvironmentVariable(envName);
            List<string> candidates = new List<string>();
            if (!String.IsNullOrWhiteSpace(configured)) candidates.Add(configured);

            string userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            if (!String.IsNullOrWhiteSpace(userProfile))
            {
                candidates.Add(Path.Combine(userProfile, "Desktop", shortcutName));
            }

            string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            if (!String.IsNullOrWhiteSpace(desktop))
            {
                candidates.Add(Path.Combine(desktop, shortcutName));
            }

            string publicDesktop = Environment.GetEnvironmentVariable("PUBLIC");
            if (!String.IsNullOrWhiteSpace(publicDesktop))
            {
                candidates.Add(Path.Combine(publicDesktop, "Desktop", shortcutName));
            }

            foreach (string candidate in candidates)
            {
                if (String.IsNullOrWhiteSpace(candidate) || !File.Exists(candidate)) continue;
                StartShellPath(candidate);
                return;
            }

            throw new InvalidOperationException("desktop shortcut not found: " + shortcutName);
        }

        private static void StartShellPath(string path)
        {
            if (String.IsNullOrWhiteSpace(path) || !File.Exists(path))
            {
                throw new FileNotFoundException("launch target not found", path ?? "");
            }

            ProcessStartInfo startInfo = new ProcessStartInfo();
            startInfo.FileName = path;
            startInfo.WorkingDirectory = Path.GetDirectoryName(path);
            startInfo.UseShellExecute = true;
            Process.Start(startInfo);
        }

        private static void ReturnToBackend()
        {
            int x = ReadInt("TICKET_PAD_BACKEND_HOTSPOT_X", 1030);
            int y = ReadInt("TICKET_PAD_BACKEND_HOTSPOT_Y", 60);
            int holdMs = Math.Max(1200, ReadInt("TICKET_PAD_BACKEND_HOLD_MS", 2800));

            SetCursorPos(x, y);
            NotifyCursorOverlay(true, true);
            InjectTouchHold(x, y, holdMs);
            NotifyCursorOverlay(true, false);
            Thread.Sleep(1200);

            SendKeyPress("2");
            Thread.Sleep(140);
            SendKeyPress("2");
            Thread.Sleep(140);
            SendKeyPress("0");
            Thread.Sleep(140);
            SendKeyPress("enter");
        }

        private static void KillProcessByName(string name)
        {
            foreach (Process process in Process.GetProcessesByName(name))
            {
                try { process.Kill(); } catch { }
            }
        }

        private static string NormalizeButton(string button)
        {
            button = (button ?? "").Trim().ToLowerInvariant();
            if (button == "right") return "right";
            if (button == "middle") return "middle";
            return "left";
        }

        private static string NormalizeRotation(string value)
        {
            value = (value ?? "").Trim().ToLowerInvariant();
            if (value == "left" || value == "ccw" || value == "ccw90" || value == "270") return "ccw90";
            if (value == "right" || value == "cw" || value == "cw90" || value == "90") return "cw90";
            if (value == "180" || value == "flip") return "180";
            return "none";
        }

        private static void ApplyScreenRotation(ref int dx, ref int dy)
        {
            int x = dx;
            int y = dy;
            if (_screenRotation == "ccw90")
            {
                dx = -y;
                dy = x;
            }
            else if (_screenRotation == "cw90")
            {
                dx = y;
                dy = -x;
            }
            else if (_screenRotation == "180")
            {
                dx = -x;
                dy = -y;
            }
        }

        private static object CurrentCursorPosition()
        {
            POINT point;
            if (!GetCursorPos(out point))
            {
                return new Dictionary<string, object> {
                    { "ok", false },
                };
            }
            return new Dictionary<string, object> {
                { "ok", true },
                { "x", point.X },
                { "y", point.Y },
            };
        }

        private static object CurrentVirtualScreen()
        {
            Rectangle bounds = SystemInformation.VirtualScreen;
            return new Dictionary<string, object> {
                { "x", bounds.Left },
                { "y", bounds.Top },
                { "width", bounds.Width },
                { "height", bounds.Height },
            };
        }

        private static void WriteScreenshot(NetworkStream stream)
        {
            try
            {
                Rectangle bounds = SystemInformation.VirtualScreen;
                using (Bitmap bitmap = new Bitmap(bounds.Width, bounds.Height))
                using (Graphics graphics = Graphics.FromImage(bitmap))
                using (MemoryStream ms = new MemoryStream())
                {
                    graphics.CopyFromScreen(bounds.Left, bounds.Top, 0, 0, bounds.Size);
                    bitmap.Save(ms, System.Drawing.Imaging.ImageFormat.Png);
                    WriteResponse(stream, 200, "image/png", ms.ToArray(), "Cache-Control: no-store\r\n");
                }
            }
            catch (Exception ex)
            {
                Log("screenshot failed: " + ex.Message);
                WriteJson(stream, 500, Error("screenshot failed: " + ex.Message));
            }
        }

        private static void CenterCursor()
        {
            Rectangle bounds = SystemInformation.VirtualScreen;
            int x = bounds.Left + bounds.Width / 2;
            int y = bounds.Top + bounds.Height / 2;
            SetCursorPos(x, y);
        }

        private static string GetString(Dictionary<string, object> payload, string key)
        {
            object value;
            if (!payload.TryGetValue(key, out value) || value == null) return "";
            return Convert.ToString(value);
        }

        private static List<object> GetObjectList(Dictionary<string, object> payload, string key)
        {
            List<object> result = new List<object>();
            object value;
            if (!payload.TryGetValue(key, out value) || value == null) return result;
            object[] array = value as object[];
            if (array != null)
            {
                result.AddRange(array);
                return result;
            }
            IEnumerable enumerable = value as IEnumerable;
            if (enumerable != null && !(value is string))
            {
                foreach (object item in enumerable) result.Add(item);
            }
            return result;
        }

        private static Dictionary<string, object> ToPayload(object value)
        {
            Dictionary<string, object> dict = value as Dictionary<string, object>;
            if (dict != null) return dict;

            IDictionary map = value as IDictionary;
            if (map != null)
            {
                Dictionary<string, object> result = new Dictionary<string, object>();
                foreach (DictionaryEntry entry in map)
                {
                    result[Convert.ToString(entry.Key)] = entry.Value;
                }
                return result;
            }

            throw new InvalidOperationException("bad batch event");
        }

        private static int GetInt(Dictionary<string, object> payload, string key)
        {
            object value;
            if (!payload.TryGetValue(key, out value) || value == null) return 0;
            return Convert.ToInt32(value);
        }

        private static double GetDouble(Dictionary<string, object> payload, string key)
        {
            object value;
            if (!payload.TryGetValue(key, out value) || value == null) return 0;
            return Convert.ToDouble(value);
        }

        private static bool GetBool(Dictionary<string, object> payload, string key)
        {
            object value;
            if (!payload.TryGetValue(key, out value) || value == null) return false;
            try { return Convert.ToBoolean(value); } catch { return false; }
        }

        private static List<string> GetStringList(Dictionary<string, object> payload, string key)
        {
            List<string> result = new List<string>();
            object value;
            if (!payload.TryGetValue(key, out value) || value == null) return result;
            object[] array = value as object[];
            if (array != null)
            {
                foreach (object item in array) result.Add(Convert.ToString(item));
                return result;
            }
            IEnumerable enumerable = value as IEnumerable;
            if (enumerable != null && !(value is string))
            {
                foreach (object item in enumerable) result.Add(Convert.ToString(item));
                return result;
            }
            foreach (string part in Convert.ToString(value).Split('+'))
            {
                if (!String.IsNullOrWhiteSpace(part)) result.Add(part.Trim());
            }
            return result;
        }

        private static void SendHotkey(List<string> keys)
        {
            if (keys.Count == 0) return;
            if (String.Join("+", keys.ToArray()).Equals("ctrl+alt+del", StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("ctrl+alt+del is not supported by user-mode input");
            }

            List<ushort> vks = new List<ushort>();
            foreach (string key in keys) vks.Add(KeyToVk(key));
            foreach (ushort vk in vks) SendKeyDown(vk);
            Thread.Sleep(45);
            for (int i = vks.Count - 1; i >= 0; i--) SendKeyUp(vks[i]);
        }

        private static void SendKeyPress(string key)
        {
            ushort vk = KeyToVk(key);
            SendKeyDown(vk);
            Thread.Sleep(35);
            SendKeyUp(vk);
        }

        private static ushort KeyToVk(string key)
        {
            key = (key ?? "").Trim().ToLowerInvariant();
            if (key.Length == 1)
            {
                char ch = key[0];
                if (ch >= 'a' && ch <= 'z') return (ushort)Char.ToUpperInvariant(ch);
                if (ch >= '0' && ch <= '9') return (ushort)ch;
            }
            if (key.StartsWith("f", StringComparison.Ordinal) && key.Length <= 3)
            {
                int n;
                if (Int32.TryParse(key.Substring(1), out n) && n >= 1 && n <= 24) return (ushort)(0x70 + n - 1);
            }
            switch (key)
            {
                case "ctrl":
                case "control": return 0x11;
                case "shift": return 0x10;
                case "alt": return 0x12;
                case "win":
                case "windows": return 0x5B;
                case "enter":
                case "return": return 0x0D;
                case "esc":
                case "escape": return 0x1B;
                case "tab": return 0x09;
                case "space": return 0x20;
                case "backspace":
                case "back": return 0x08;
                case "del":
                case "delete": return 0x2E;
                case "insert":
                case "ins": return 0x2D;
                case "home": return 0x24;
                case "end": return 0x23;
                case "pageup":
                case "pgup": return 0x21;
                case "pagedown":
                case "pgdn": return 0x22;
                case "up": return 0x26;
                case "down": return 0x28;
                case "left": return 0x25;
                case "right": return 0x27;
                case "-": return 0xBD;
                case "=":
                case "+": return 0xBB;
                case ",":
                case "<": return 0xBC;
                case ".":
                case ">": return 0xBE;
                case "/":
                case "?": return 0xBF;
                case "\\":
                case "|": return 0xDC;
                case ";":
                case ":": return 0xBA;
                case "'":
                case "\"": return 0xDE;
                case "[":
                case "{": return 0xDB;
                case "]":
                case "}": return 0xDD;
                case "`":
                case "~": return 0xC0;
            }
            throw new InvalidOperationException("unsupported key: " + key);
        }

        private static void SendUnicodeText(string text)
        {
            if (String.IsNullOrEmpty(text)) return;
            List<INPUT> inputs = new List<INPUT>(text.Length * 2);
            foreach (char ch in text)
            {
                INPUT down = KeyboardInput(0, ch, KEYEVENTF_UNICODE);
                INPUT up = KeyboardInput(0, ch, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP);
                inputs.Add(down);
                inputs.Add(up);
            }
            SendInputs(inputs.ToArray());
        }

        private static void SendKeyDown(ushort vk)
        {
            SendInputs(new INPUT[] { KeyboardInput(vk, '\0', 0) });
        }

        private static void SendKeyUp(ushort vk)
        {
            SendInputs(new INPUT[] { KeyboardInput(vk, '\0', KEYEVENTF_KEYUP) });
        }

        private static void SendMouseMove(int dx, int dy)
        {
            POINT point;
            if (!GetCursorPos(out point))
            {
                throw new InvalidOperationException("GetCursorPos failed: " + Marshal.GetLastWin32Error());
            }

            Rectangle bounds = SystemInformation.VirtualScreen;
            int x = Clamp(point.X + dx, bounds.Left, bounds.Right - 1);
            int y = Clamp(point.Y + dy, bounds.Top, bounds.Bottom - 1);
            if (!SetCursorPos(x, y))
            {
                throw new InvalidOperationException("SetCursorPos failed: " + Marshal.GetLastWin32Error());
            }
        }

        private static int Clamp(int value, int min, int max)
        {
            if (value < min) return min;
            if (value > max) return max;
            return value;
        }

        private static void SendMouseWheel(int delta)
        {
            INPUT input = new INPUT();
            input.type = INPUT_MOUSE;
            input.union.mi.mouseData = delta;
            input.union.mi.dwFlags = MOUSEEVENTF_WHEEL;
            SendInputs(new INPUT[] { input });
        }

        private static void SendMouseButton(string button, bool down)
        {
            uint flag;
            if (button == "right") flag = down ? MOUSEEVENTF_RIGHTDOWN : MOUSEEVENTF_RIGHTUP;
            else if (button == "middle") flag = down ? MOUSEEVENTF_MIDDLEDOWN : MOUSEEVENTF_MIDDLEUP;
            else flag = down ? MOUSEEVENTF_LEFTDOWN : MOUSEEVENTF_LEFTUP;
            INPUT input = new INPUT();
            input.type = INPUT_MOUSE;
            input.union.mi.dwFlags = flag;
            SendInputs(new INPUT[] { input });
        }

        private static void InjectTouchHold(int x, int y, int holdMs)
        {
            if (!InitializeTouchInjection(1, TOUCH_FEEDBACK_DEFAULT))
            {
                throw new InvalidOperationException("InitializeTouchInjection failed: " + Marshal.GetLastWin32Error());
            }

            uint downFlags = POINTER_FLAG_NEW | POINTER_FLAG_DOWN | POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT | POINTER_FLAG_PRIMARY;
            InjectTouch(NewTouchContact(x, y, downFlags));

            DateTime endAt = DateTime.UtcNow.AddMilliseconds(holdMs);
            while (DateTime.UtcNow < endAt)
            {
                Thread.Sleep(90);
                uint updateFlags = POINTER_FLAG_UPDATE | POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT | POINTER_FLAG_PRIMARY;
                InjectTouch(NewTouchContact(x, y, updateFlags));
            }

            uint upFlags = POINTER_FLAG_UP | POINTER_FLAG_INRANGE | POINTER_FLAG_PRIMARY;
            InjectTouch(NewTouchContact(x, y, upFlags));
        }

        private static POINTER_TOUCH_INFO NewTouchContact(int x, int y, uint flags)
        {
            POINTER_TOUCH_INFO contact = new POINTER_TOUCH_INFO();
            contact.pointerInfo.pointerType = PT_TOUCH;
            contact.pointerInfo.pointerId = 1;
            contact.pointerInfo.pointerFlags = flags;
            contact.pointerInfo.ptPixelLocation.X = x;
            contact.pointerInfo.ptPixelLocation.Y = y;
            contact.pointerInfo.ptPixelLocationRaw.X = x;
            contact.pointerInfo.ptPixelLocationRaw.Y = y;
            contact.touchMask = TOUCH_MASK_CONTACTAREA | TOUCH_MASK_ORIENTATION | TOUCH_MASK_PRESSURE;
            contact.rcContact.left = x - 4;
            contact.rcContact.top = y - 4;
            contact.rcContact.right = x + 4;
            contact.rcContact.bottom = y + 4;
            contact.rcContactRaw = contact.rcContact;
            contact.orientation = 90;
            contact.pressure = 32000;
            return contact;
        }

        private static void InjectTouch(POINTER_TOUCH_INFO contact)
        {
            POINTER_TOUCH_INFO[] contacts = new POINTER_TOUCH_INFO[] { contact };
            if (!InjectTouchInput(1, contacts))
            {
                throw new InvalidOperationException("InjectTouchInput failed: " + Marshal.GetLastWin32Error());
            }
        }

        private static INPUT KeyboardInput(ushort vk, char scan, uint flags)
        {
            INPUT input = new INPUT();
            input.type = INPUT_KEYBOARD;
            input.union.ki.wVk = vk;
            input.union.ki.wScan = scan;
            input.union.ki.dwFlags = flags;
            return input;
        }

        private static void SendInputs(INPUT[] inputs)
        {
            uint sent = SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(INPUT)));
            if (sent != inputs.Length)
            {
                throw new InvalidOperationException("SendInput failed: " + Marshal.GetLastWin32Error());
            }
        }

        private static void NotifyCursorOverlay(bool pulse, bool down)
        {
            if (!_cursorOverlayEnabled) return;

            POINT point;
            if (!GetCursorPos(out point)) return;

            CursorOverlayForm overlay = _cursorOverlay;
            if (overlay == null || overlay.IsDisposed) return;

            try
            {
                if (overlay.IsHandleCreated)
                {
                    overlay.BeginInvoke(new Action(() => overlay.UpdateCursor(point.X, point.Y, pulse, down)));
                }
            }
            catch
            {
            }
        }

        private static void SetCursorOverlayEnabled(bool enabled)
        {
            CursorOverlayForm overlay = _cursorOverlay;
            if (overlay == null || overlay.IsDisposed) return;

            try
            {
                if (overlay.IsHandleCreated)
                {
                    overlay.BeginInvoke(new Action(() => overlay.SetOverlayEnabled(enabled)));
                }
            }
            catch
            {
            }
        }

        private static void HandleWebSocket(NetworkStream stream, HttpRequest request)
        {
            if (!ValidateWebSocketPin(request))
            {
                WriteJson(stream, 401, Error("invalid pin"));
                return;
            }

            string key;
            if (!request.Headers.TryGetValue("Sec-WebSocket-Key", out key) || String.IsNullOrWhiteSpace(key))
            {
                WriteJson(stream, 400, Error("bad websocket handshake"));
                return;
            }

            string upgrade;
            if (!request.Headers.TryGetValue("Upgrade", out upgrade) || !upgrade.Equals("websocket", StringComparison.OrdinalIgnoreCase))
            {
                WriteJson(stream, 400, Error("bad websocket upgrade"));
                return;
            }

            stream.ReadTimeout = Timeout.Infinite;
            string accept = WebSocketAccept(key);
            string header = "HTTP/1.1 101 Switching Protocols\r\n" +
                "Upgrade: websocket\r\n" +
                "Connection: Upgrade\r\n" +
                "Sec-WebSocket-Accept: " + accept + "\r\n\r\n";
            byte[] headerBytes = Encoding.ASCII.GetBytes(header);
            stream.Write(headerBytes, 0, headerBytes.Length);
            Log("websocket connected");

            try
            {
                while (true)
                {
                    WebSocketFrame frame = ReadWebSocketFrame(stream);
                    if (frame == null) break;
                    if (frame.Opcode == 0x8)
                    {
                        WriteWebSocketFrame(stream, 0x8, frame.Payload);
                        break;
                    }
                    if (frame.Opcode == 0x9)
                    {
                        WriteWebSocketFrame(stream, 0xA, frame.Payload);
                        continue;
                    }
                    if (frame.Opcode == 0xA)
                    {
                        continue;
                    }
                    if (frame.Opcode != 0x1)
                    {
                        WriteWebSocketJson(stream, Error("unsupported websocket frame"));
                        continue;
                    }

                    string text = Utf8NoBom.GetString(frame.Payload);
                    HandleWebSocketMessage(stream, text);
                }
            }
            finally
            {
                Log("websocket disconnected");
            }
        }

        private static bool ValidateWebSocketPin(HttpRequest request)
        {
            if (String.IsNullOrEmpty(_pin)) return true;
            string pin = GetQueryValue(request.Path, "pin");
            return pin == _pin;
        }

        private static string GetQueryValue(string path, string key)
        {
            int queryIndex = path.IndexOf('?');
            if (queryIndex < 0) return "";
            string query = path.Substring(queryIndex + 1);
            string[] parts = query.Split('&');
            for (int i = 0; i < parts.Length; i++)
            {
                string part = parts[i];
                int equals = part.IndexOf('=');
                string name = equals >= 0 ? part.Substring(0, equals) : part;
                string value = equals >= 0 ? part.Substring(equals + 1) : "";
                name = Uri.UnescapeDataString(name.Replace("+", " "));
                if (!name.Equals(key, StringComparison.OrdinalIgnoreCase)) continue;
                return Uri.UnescapeDataString(value.Replace("+", " "));
            }
            return "";
        }

        private static string WebSocketAccept(string key)
        {
            using (SHA1 sha1 = SHA1.Create())
            {
                byte[] input = Encoding.ASCII.GetBytes(key.Trim() + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11");
                return Convert.ToBase64String(sha1.ComputeHash(input));
            }
        }

        private static void HandleWebSocketMessage(NetworkStream stream, string text)
        {
            Dictionary<string, object> payload;
            try
            {
                payload = Json.Deserialize<Dictionary<string, object>>(text ?? "{}");
            }
            catch
            {
                WriteWebSocketJson(stream, Error("bad json"));
                return;
            }

            try
            {
                bool noAck = GetBool(payload, "no_ack");
                if (payload.ContainsKey("events"))
                {
                    Dictionary<string, object> result = ExecuteBatchPayload(payload);
                    if (!noAck) WriteWebSocketJson(stream, result);
                    return;
                }
                Dictionary<string, object> singleResult = ExecuteSinglePayload(payload);
                if (!noAck) WriteWebSocketJson(stream, singleResult);
            }
            catch (Exception ex)
            {
                string type = GetString(payload, "type");
                if (payload.ContainsKey("events"))
                {
                    Log("websocket batch event failed error=" + ex.Message);
                }
                else
                {
                    Log("websocket event failed type=" + type + " error=" + ex.Message);
                }
                if (!GetBool(payload, "no_ack"))
                {
                    WriteWebSocketJson(stream, ErrorWithId(payload, ex.Message));
                }
            }
        }

        private static WebSocketFrame ReadWebSocketFrame(NetworkStream stream)
        {
            int first = stream.ReadByte();
            if (first < 0) return null;
            int second = stream.ReadByte();
            if (second < 0) return null;

            int opcode = first & 0x0F;
            bool masked = (second & 0x80) != 0;
            ulong length = (ulong)(second & 0x7F);
            if (length == 126)
            {
                byte[] bytes = ReadExact(stream, 2);
                length = ((ulong)bytes[0] << 8) | bytes[1];
            }
            else if (length == 127)
            {
                byte[] bytes = ReadExact(stream, 8);
                length = 0;
                for (int i = 0; i < bytes.Length; i++)
                {
                    length = (length << 8) | bytes[i];
                }
            }

            if (length > 1024 * 1024)
            {
                throw new InvalidOperationException("websocket payload too large");
            }

            byte[] mask = masked ? ReadExact(stream, 4) : null;
            byte[] payload = ReadExact(stream, (int)length);
            if (masked && mask != null)
            {
                for (int i = 0; i < payload.Length; i++)
                {
                    payload[i] = (byte)(payload[i] ^ mask[i % 4]);
                }
            }

            return new WebSocketFrame {
                Opcode = opcode,
                Payload = payload,
            };
        }

        private static byte[] ReadExact(NetworkStream stream, int length)
        {
            byte[] buffer = new byte[length];
            int offset = 0;
            while (offset < length)
            {
                int read = stream.Read(buffer, offset, length - offset);
                if (read <= 0) throw new IOException("websocket disconnected");
                offset += read;
            }
            return buffer;
        }

        private static void WriteWebSocketJson(NetworkStream stream, object payload)
        {
            WriteWebSocketFrame(stream, 0x1, Utf8NoBom.GetBytes(Json.Serialize(payload)));
        }

        private static void WriteWebSocketFrame(NetworkStream stream, int opcode, byte[] payload)
        {
            if (payload == null) payload = new byte[0];
            List<byte> header = new List<byte>();
            header.Add((byte)(0x80 | (opcode & 0x0F)));
            if (payload.Length <= 125)
            {
                header.Add((byte)payload.Length);
            }
            else if (payload.Length <= UInt16.MaxValue)
            {
                header.Add(126);
                header.Add((byte)((payload.Length >> 8) & 0xFF));
                header.Add((byte)(payload.Length & 0xFF));
            }
            else
            {
                header.Add(127);
                ulong length = (ulong)payload.Length;
                for (int shift = 56; shift >= 0; shift -= 8)
                {
                    header.Add((byte)((length >> shift) & 0xFF));
                }
            }

            byte[] headerBytes = header.ToArray();
            stream.Write(headerBytes, 0, headerBytes.Length);
            if (payload.Length > 0)
            {
                stream.Write(payload, 0, payload.Length);
            }
        }

        private static Dictionary<string, object> Error(string message)
        {
            return new Dictionary<string, object> {
                { "ok", false },
                { "message", message },
            };
        }

        private static Dictionary<string, object> ErrorWithId(Dictionary<string, object> payload, string message)
        {
            Dictionary<string, object> result = Error(message);
            AddPayloadId(result, payload);
            return result;
        }

        private static void AddPayloadId(Dictionary<string, object> result, Dictionary<string, object> payload)
        {
            object requestId;
            if (payload != null && payload.TryGetValue("request_id", out requestId) && requestId != null)
            {
                result["id"] = requestId;
                return;
            }

            object id;
            if (payload != null && payload.TryGetValue("id", out id) && id != null)
            {
                result["id"] = id;
            }
        }

        private static void WriteJson(NetworkStream stream, int status, object payload)
        {
            byte[] body = Utf8NoBom.GetBytes(Json.Serialize(payload));
            WriteResponse(stream, status, "application/json; charset=utf-8", body);
        }

        private static void WriteFile(NetworkStream stream, string filePath, string contentType)
        {
            if (!File.Exists(filePath))
            {
                WriteJson(stream, 404, Error("not found"));
                return;
            }
            WriteResponse(stream, 200, contentType, File.ReadAllBytes(filePath));
        }

        private static string MimeType(string filePath)
        {
            string ext = Path.GetExtension(filePath).ToLowerInvariant();
            if (ext == ".html") return "text/html; charset=utf-8";
            if (ext == ".js") return "application/javascript; charset=utf-8";
            if (ext == ".css") return "text/css; charset=utf-8";
            if (ext == ".json") return "application/json; charset=utf-8";
            if (ext == ".png") return "image/png";
            if (ext == ".svg") return "image/svg+xml";
            return "application/octet-stream";
        }

        private static void WriteResponse(NetworkStream stream, int status, string contentType, byte[] body)
        {
            WriteResponse(stream, status, contentType, body, "");
        }

        private static void WriteResponse(NetworkStream stream, int status, string contentType, byte[] body, string extraHeaders)
        {
            string statusText = status == 200 ? "OK" : status == 400 ? "Bad Request" : status == 401 ? "Unauthorized" : status == 404 ? "Not Found" : "Error";
            string header = "HTTP/1.1 " + status + " " + statusText + "\r\n" +
                "Content-Type: " + contentType + "\r\n" +
                "Content-Length: " + body.Length + "\r\n" +
                "Access-Control-Allow-Origin: *\r\n" +
                (extraHeaders ?? "") +
                "Connection: close\r\n\r\n";
            byte[] headerBytes = Encoding.ASCII.GetBytes(header);
            stream.Write(headerBytes, 0, headerBytes.Length);
            stream.Write(body, 0, body.Length);
        }

        private static void Log(string message)
        {
            try
            {
                File.AppendAllText(_logPath, "[" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "] " + message + Environment.NewLine, Utf8NoBom);
            }
            catch
            {
            }
        }

        private sealed class HttpRequest
        {
            public string Method;
            public string Path;
            public Dictionary<string, string> Headers;
            public string Body;
        }

        private sealed class WebSocketFrame
        {
            public int Opcode;
            public byte[] Payload;
        }

        private sealed class CursorOverlayForm : Form
        {
            private const int WS_EX_TRANSPARENT = 0x00000020;
            private const int WS_EX_LAYERED = 0x00080000;
            private const int WS_EX_TOOLWINDOW = 0x00000080;
            private const int WS_EX_NOACTIVATE = 0x08000000;
            private const int WS_EX_TOPMOST = 0x00000008;
            private static readonly IntPtr HWND_TOPMOST = new IntPtr(-1);
            private const uint SWP_NOSIZE = 0x0001;
            private const uint SWP_NOMOVE = 0x0002;
            private const uint SWP_NOACTIVATE = 0x0010;
            private const uint SWP_SHOWWINDOW = 0x0040;
            private const uint SWP_NOOWNERZORDER = 0x0200;
            private readonly System.Windows.Forms.Timer _timer;
            private readonly int _idleMs;
            private int _x;
            private int _y;
            private bool _hasCursor;
            private bool _isDown;
            private bool _enabled = true;
            private DateTime _hideAfterUtc = DateTime.MinValue;
            private DateTime _pulseUntilUtc = DateTime.MinValue;
            private DateTime _lastTopMostUtc = DateTime.MinValue;

            public CursorOverlayForm(int idleMs, bool enabled)
            {
                _idleMs = Math.Max(250, idleMs);
                _enabled = enabled;
                FormBorderStyle = FormBorderStyle.None;
                ShowInTaskbar = false;
                StartPosition = FormStartPosition.Manual;
                Bounds = SystemInformation.VirtualScreen;
                BackColor = Color.Fuchsia;
                TransparencyKey = Color.Fuchsia;
                TopMost = true;
                DoubleBuffered = true;

                _timer = new System.Windows.Forms.Timer();
                _timer.Interval = 33;
                _timer.Tick += delegate
                {
                    Rectangle virtualScreen = SystemInformation.VirtualScreen;
                    if (Bounds != virtualScreen)
                    {
                        Bounds = virtualScreen;
                    }
                    if (_hasCursor)
                    {
                        EnsureTopMost(false);
                    }
                    if (_hasCursor && DateTime.UtcNow >= _hideAfterUtc)
                    {
                        _hasCursor = false;
                        _isDown = false;
                        Invalidate();
                    }
                    else if (_hasCursor)
                    {
                        Invalidate();
                    }
                };
                _timer.Start();
            }

            protected override bool ShowWithoutActivation
            {
                get { return true; }
            }

            protected override void OnShown(EventArgs e)
            {
                base.OnShown(e);
                EnsureTopMost(true);
            }

            protected override CreateParams CreateParams
            {
                get
                {
                    CreateParams cp = base.CreateParams;
                    cp.ExStyle |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST;
                    return cp;
                }
            }

            public void UpdateCursor(int screenX, int screenY, bool pulse, bool down)
            {
                if (!_enabled) return;
                _x = screenX;
                _y = screenY;
                _hasCursor = true;
                _isDown = down;
                _hideAfterUtc = DateTime.UtcNow.AddMilliseconds(_idleMs);
                if (pulse)
                {
                    _pulseUntilUtc = DateTime.UtcNow.AddMilliseconds(320);
                }
                EnsureTopMost(true);
                Invalidate();
            }

            public void SetOverlayEnabled(bool enabled)
            {
                _enabled = enabled;
                if (enabled)
                {
                    EnsureTopMost(true);
                }
                if (!enabled)
                {
                    _hasCursor = false;
                    _isDown = false;
                    Invalidate();
                }
            }

            private void EnsureTopMost(bool force)
            {
                if (!IsHandleCreated) return;
                DateTime now = DateTime.UtcNow;
                if (!force && (now - _lastTopMostUtc).TotalMilliseconds < 100) return;
                _lastTopMostUtc = now;
                if (!TopMost) TopMost = true;
                SetWindowPos(Handle, HWND_TOPMOST, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOOWNERZORDER);
            }

            protected override void OnPaint(PaintEventArgs e)
            {
                base.OnPaint(e);
                if (!_hasCursor) return;

                int x = _x - Bounds.Left;
                int y = _y - Bounds.Top;
                e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;

                float pulse = 0f;
                if (_pulseUntilUtc > DateTime.UtcNow)
                {
                    pulse = (float)Math.Max(0, (_pulseUntilUtc - DateTime.UtcNow).TotalMilliseconds / 320.0);
                }

                if (pulse > 0)
                {
                    float radius = 18f + (1f - pulse) * 30f;
                    using (Pen outline = new Pen(Color.FromArgb((int)(190 * pulse), 0, 0, 0), 5f))
                    using (Pen ring = new Pen(Color.FromArgb((int)(240 * pulse), 255, 214, 80), 3f))
                    {
                        e.Graphics.DrawEllipse(outline, x - radius, y - radius, radius * 2f, radius * 2f);
                        e.Graphics.DrawEllipse(ring, x - radius, y - radius, radius * 2f, radius * 2f);
                    }
                }

                PointF[] arrowPoints = new PointF[]
                {
                    new PointF(x, y),
                    new PointF(x, y + 35),
                    new PointF(x + 9, y + 27),
                    new PointF(x + 15, y + 43),
                    new PointF(x + 23, y + 40),
                    new PointF(x + 17, y + 24),
                    new PointF(x + 31, y + 24),
                };

                using (GraphicsPath arrow = new GraphicsPath())
                using (Pen shadow = new Pen(Color.FromArgb(230, 0, 0, 0), 5f))
                using (Pen edge = new Pen(Color.FromArgb(255, 255, 245, 245), 1.5f))
                using (Brush fill = new SolidBrush(_isDown ? Color.FromArgb(255, 180, 0, 0) : Color.FromArgb(255, 230, 18, 18)))
                {
                    arrow.AddPolygon(arrowPoints);
                    shadow.LineJoin = LineJoin.Round;
                    edge.LineJoin = LineJoin.Round;
                    e.Graphics.DrawPath(shadow, arrow);
                    e.Graphics.FillPath(fill, arrow);
                    e.Graphics.DrawPath(edge, arrow);
                }
            }

            [DllImport("user32.dll", SetLastError = true)]
            private static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int x, int y, int cx, int cy, uint flags);
        }

        private const uint INPUT_MOUSE = 0;
        private const uint INPUT_KEYBOARD = 1;
        private const uint KEYEVENTF_KEYUP = 0x0002;
        private const uint KEYEVENTF_UNICODE = 0x0004;
        private const uint MOUSEEVENTF_MOVE = 0x0001;
        private const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
        private const uint MOUSEEVENTF_LEFTUP = 0x0004;
        private const uint MOUSEEVENTF_RIGHTDOWN = 0x0008;
        private const uint MOUSEEVENTF_RIGHTUP = 0x0010;
        private const uint MOUSEEVENTF_MIDDLEDOWN = 0x0020;
        private const uint MOUSEEVENTF_MIDDLEUP = 0x0040;
        private const uint MOUSEEVENTF_WHEEL = 0x0800;
        private const uint PT_TOUCH = 2;
        private const uint TOUCH_FEEDBACK_DEFAULT = 1;
        private const uint TOUCH_MASK_CONTACTAREA = 0x00000001;
        private const uint TOUCH_MASK_ORIENTATION = 0x00000002;
        private const uint TOUCH_MASK_PRESSURE = 0x00000004;
        private const uint POINTER_FLAG_NEW = 0x00000001;
        private const uint POINTER_FLAG_INRANGE = 0x00000002;
        private const uint POINTER_FLAG_INCONTACT = 0x00000004;
        private const uint POINTER_FLAG_PRIMARY = 0x00002000;
        private const uint POINTER_FLAG_DOWN = 0x00010000;
        private const uint POINTER_FLAG_UPDATE = 0x00020000;
        private const uint POINTER_FLAG_UP = 0x00040000;

        [StructLayout(LayoutKind.Sequential)]
        private struct INPUT
        {
            public uint type;
            public MOUSEKEYBDHARDWAREINPUT union;
        }

        [StructLayout(LayoutKind.Explicit)]
        private struct MOUSEKEYBDHARDWAREINPUT
        {
            [FieldOffset(0)] public MOUSEINPUT mi;
            [FieldOffset(0)] public KEYBDINPUT ki;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct MOUSEINPUT
        {
            public int dx;
            public int dy;
            public int mouseData;
            public uint dwFlags;
            public uint time;
            public IntPtr dwExtraInfo;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct KEYBDINPUT
        {
            public ushort wVk;
            public char wScan;
            public uint dwFlags;
            public uint time;
            public IntPtr dwExtraInfo;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct RECT
        {
            public int left;
            public int top;
            public int right;
            public int bottom;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct POINTER_INFO
        {
            public uint pointerType;
            public uint pointerId;
            public uint frameId;
            public uint pointerFlags;
            public IntPtr sourceDevice;
            public IntPtr hwndTarget;
            public POINT ptPixelLocation;
            public POINT ptHimetricLocation;
            public POINT ptPixelLocationRaw;
            public POINT ptHimetricLocationRaw;
            public uint dwTime;
            public uint historyCount;
            public int InputData;
            public uint dwKeyStates;
            public ulong PerformanceCount;
            public uint ButtonChangeType;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct POINTER_TOUCH_INFO
        {
            public POINTER_INFO pointerInfo;
            public uint touchFlags;
            public uint touchMask;
            public RECT rcContact;
            public RECT rcContactRaw;
            public uint orientation;
            public uint pressure;
        }

        [DllImport("user32.dll", SetLastError = true)]
        private static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool InitializeTouchInjection(uint maxCount, uint dwMode);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool InjectTouchInput(uint count, POINTER_TOUCH_INFO[] contacts);

        [DllImport("user32.dll")]
        private static extern bool SetProcessDPIAware();

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool GetCursorPos(out POINT lpPoint);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool SetCursorPos(int x, int y);

        [StructLayout(LayoutKind.Sequential)]
        private struct POINT
        {
            public int X;
            public int Y;
        }
    }
}
