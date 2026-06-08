using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Automation;
using System.Windows.Forms;

namespace SixKa.InputRecorder
{
    internal static class Program
    {
        private const string Version = "0.1.0";
        private const int WH_MOUSE_LL = 14;
        private const int WH_KEYBOARD_LL = 13;
        private const int WM_LBUTTONDOWN = 0x0201;
        private const int WM_LBUTTONUP = 0x0202;
        private const int WM_RBUTTONDOWN = 0x0204;
        private const int WM_RBUTTONUP = 0x0205;
        private const int WM_MBUTTONDOWN = 0x0207;
        private const int WM_MBUTTONUP = 0x0208;
        private const int WM_MOUSEWHEEL = 0x020A;
        private const int WM_KEYDOWN = 0x0100;
        private const int WM_SYSKEYDOWN = 0x0104;
        private const int VK_SHIFT = 0x10;
        private const int VK_CONTROL = 0x11;
        private const int VK_MENU = 0x12;
        private const int VK_LWIN = 0x5B;
        private const int VK_RWIN = 0x5C;

        private static readonly Encoding Utf8NoBom = new UTF8Encoding(false);
        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
        private static readonly object QueueLock = new object();
        private static readonly object PathLock = new object();
        private static readonly object WriteLock = new object();
        private static readonly AutoResetEvent QueueSignal = new AutoResetEvent(false);
        private static readonly Queue<InputEvent> Events = new Queue<InputEvent>();

        private static readonly DateTimeOffset StartedAt = DateTimeOffset.Now;
        private static HookProc _mouseProc = MouseHookCallback;
        private static HookProc _keyboardProc = KeyboardHookCallback;
        private static IntPtr _mouseHook = IntPtr.Zero;
        private static IntPtr _keyboardHook = IntPtr.Zero;
        private static Mutex _mutex;
        private static Thread _workerThread;
        private static System.Threading.Timer _cleanupTimer;
        private static volatile bool _running;
        private static string _baseDir;
        private static string _logDir;
        private static string _screenshotRoot;
        private static string _statePath;
        private static string _currentDay;
        private static string _todayLogPath;
        private static string _todayScreenshotDir;
        private static bool _logTextKeys;
        private static long _eventCount;
        private static long _clickCount;
        private static long _keyCount;
        private static string _lastEventAt;
        private static string _lastClickScreenshot;
        private static string _lastError;

        [STAThread]
        private static int Main(string[] args)
        {
            Json.MaxJsonLength = Int32.MaxValue;
            _baseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            _logDir = Path.Combine(_baseDir, "logs");
            _screenshotRoot = Path.Combine(_baseDir, "screenshots");
            _statePath = Path.Combine(_baseDir, "input_recorder_state.json");
            _logTextKeys = ReadBoolEnv("INPUT_RECORDER_LOG_TEXT_KEYS", false);

            Directory.CreateDirectory(_logDir);
            Directory.CreateDirectory(_screenshotRoot);
            EnsureToday();

            bool created;
            _mutex = new Mutex(true, @"Global\SixKaKioskInputRecorder", out created);
            if (!created)
            {
                WriteImmediateSystemEvent("duplicate_start", "another recorder instance is already running");
                return 0;
            }

            _running = true;
            _workerThread = new Thread(WorkerLoop);
            _workerThread.IsBackground = true;
            _workerThread.Name = "input-recorder-writer";
            _workerThread.Start();
            _cleanupTimer = new System.Threading.Timer(CleanupTimerTick, null, TimeSpan.FromSeconds(10), TimeSpan.FromMinutes(1));

            Application.ApplicationExit += delegate { Shutdown(); };
            Microsoft.Win32.SystemEvents.SessionEnding += delegate { Shutdown(); };

            _mouseHook = SetHook(WH_MOUSE_LL, _mouseProc);
            _keyboardHook = SetHook(WH_KEYBOARD_LL, _keyboardProc);
            if (_mouseHook == IntPtr.Zero || _keyboardHook == IntPtr.Zero)
            {
                int error = Marshal.GetLastWin32Error();
                _lastError = "hook install failed, win32_error=" + error;
                WriteImmediateSystemEvent("startup_error", _lastError);
                Shutdown();
                return 2;
            }

            Enqueue(InputEvent.System("startup"));
            Application.Run(new ApplicationContext());
            Shutdown();
            return 0;
        }

        private static bool ReadBoolEnv(string name, bool defaultValue)
        {
            string value = Environment.GetEnvironmentVariable(name);
            if (String.IsNullOrWhiteSpace(value))
            {
                return defaultValue;
            }
            value = value.Trim().ToLowerInvariant();
            return value == "1" || value == "true" || value == "yes" || value == "on";
        }

        private static IntPtr SetHook(int hookId, HookProc proc)
        {
            using (Process currentProcess = Process.GetCurrentProcess())
            using (ProcessModule currentModule = currentProcess.MainModule)
            {
                return SetWindowsHookEx(hookId, proc, GetModuleHandle(currentModule.ModuleName), 0);
            }
        }

        private static void Shutdown()
        {
            if (!_running)
            {
                return;
            }

            _running = false;
            try
            {
                if (_mouseHook != IntPtr.Zero)
                {
                    UnhookWindowsHookEx(_mouseHook);
                    _mouseHook = IntPtr.Zero;
                }
                if (_keyboardHook != IntPtr.Zero)
                {
                    UnhookWindowsHookEx(_keyboardHook);
                    _keyboardHook = IntPtr.Zero;
                }
            }
            catch
            {
            }
            try
            {
                if (_cleanupTimer != null)
                {
                    _cleanupTimer.Dispose();
                }
            }
            catch
            {
            }
            try
            {
                Enqueue(InputEvent.System("shutdown"));
                QueueSignal.Set();
            }
            catch
            {
            }
            try
            {
                if (_mutex != null)
                {
                    _mutex.ReleaseMutex();
                    _mutex.Dispose();
                }
            }
            catch
            {
            }
        }

        private static void CleanupTimerTick(object state)
        {
            try
            {
                EnsureToday();
                WriteState();
            }
            catch (Exception ex)
            {
                _lastError = "cleanup failed: " + ex.Message;
            }
        }

        private static void EnsureToday()
        {
            lock (PathLock)
            {
                string day = DateTime.Now.ToString("yyyy-MM-dd");
                if (_currentDay == day && !String.IsNullOrEmpty(_todayLogPath))
                {
                    return;
                }

                _currentDay = day;
                _todayLogPath = Path.Combine(_logDir, "input-" + day + ".jsonl");
                _todayScreenshotDir = Path.Combine(_screenshotRoot, day);
                Directory.CreateDirectory(_logDir);
                Directory.CreateDirectory(_todayScreenshotDir);
                CleanupOldDays(day);
            }
        }

        private static void CleanupOldDays(string today)
        {
            try
            {
                foreach (string file in Directory.GetFiles(_logDir, "input-*.jsonl"))
                {
                    string expected = "input-" + today + ".jsonl";
                    if (!String.Equals(Path.GetFileName(file), expected, StringComparison.OrdinalIgnoreCase))
                    {
                        TryDeleteFile(file);
                    }
                }

                foreach (string dir in Directory.GetDirectories(_screenshotRoot))
                {
                    if (!String.Equals(Path.GetFileName(dir), today, StringComparison.OrdinalIgnoreCase))
                    {
                        TryDeleteDirectory(dir);
                    }
                }
            }
            catch (Exception ex)
            {
                _lastError = "cleanup old days failed: " + ex.Message;
            }
        }

        private static void TryDeleteFile(string path)
        {
            try
            {
                File.Delete(path);
            }
            catch
            {
            }
        }

        private static void TryDeleteDirectory(string path)
        {
            try
            {
                Directory.Delete(path, true);
            }
            catch
            {
            }
        }

        private static void Enqueue(InputEvent ev)
        {
            lock (QueueLock)
            {
                Events.Enqueue(ev);
            }
            QueueSignal.Set();
        }

        private static bool TryDequeue(out InputEvent ev)
        {
            lock (QueueLock)
            {
                if (Events.Count > 0)
                {
                    ev = Events.Dequeue();
                    return true;
                }
            }
            ev = null;
            return false;
        }

        private static void WorkerLoop()
        {
            while (_running)
            {
                QueueSignal.WaitOne(1000);
                InputEvent ev;
                while (TryDequeue(out ev))
                {
                    ProcessEvent(ev);
                }
            }
        }

        private static void ProcessEvent(InputEvent ev)
        {
            try
            {
                EnsureToday();
                Interlocked.Increment(ref _eventCount);
                _lastEventAt = ev.Timestamp.ToString("yyyy-MM-ddTHH:mm:ss.fffzzz");

                WindowSnapshot activeWindow = CaptureActiveWindow();
                ElementSnapshot element = null;
                string screenshotPath = null;
                string screenshotError = null;

                if (ev.Kind == "mouse_down" || ev.Kind == "mouse_up" || ev.Kind == "mouse_wheel")
                {
                    element = CaptureElementAtPoint(ev.X, ev.Y);
                }

                if (ev.Kind == "mouse_down")
                {
                    Interlocked.Increment(ref _clickCount);
                    screenshotPath = CaptureClickScreenshot(ev);
                    if (String.IsNullOrEmpty(screenshotPath))
                    {
                        screenshotError = _lastError;
                    }
                    else
                    {
                        _lastClickScreenshot = screenshotPath;
                    }
                }
                else if (ev.Kind == "key_down")
                {
                    Interlocked.Increment(ref _keyCount);
                }

                Dictionary<string, object> payload = new Dictionary<string, object>();
                payload["time"] = ev.Timestamp.ToString("yyyy-MM-ddTHH:mm:ss.fffzzz");
                payload["type"] = ev.Kind;
                payload["version"] = Version;
                if (!String.IsNullOrEmpty(ev.Action))
                {
                    payload["action"] = ev.Action;
                }
                if (ev.HasPoint)
                {
                    payload["x"] = ev.X;
                    payload["y"] = ev.Y;
                }
                if (!String.IsNullOrEmpty(ev.Button))
                {
                    payload["button"] = ev.Button;
                }
                if (ev.WheelDelta != 0)
                {
                    payload["wheel_delta"] = ev.WheelDelta;
                }
                if (!String.IsNullOrEmpty(ev.KeyName))
                {
                    payload["key"] = ev.KeyName;
                    payload["key_category"] = ev.KeyCategory;
                    payload["text_redacted"] = ev.TextRedacted;
                    if (ev.IncludeKeyCode)
                    {
                        payload["vk_code"] = ev.VirtualKeyCode;
                    }
                }
                if (!String.IsNullOrEmpty(ev.Modifiers))
                {
                    payload["modifiers"] = ev.Modifiers;
                }
                if (activeWindow != null)
                {
                    payload["active_window"] = activeWindow.ToDictionary();
                }
                if (element != null)
                {
                    payload["element"] = element.ToDictionary();
                }
                if (!String.IsNullOrEmpty(screenshotPath))
                {
                    payload["screenshot"] = MakeRelativePath(_baseDir, screenshotPath);
                }
                if (!String.IsNullOrEmpty(screenshotError))
                {
                    payload["screenshot_error"] = screenshotError;
                }

                AppendJsonLine(payload);
                WriteState();
            }
            catch (Exception ex)
            {
                _lastError = "process event failed: " + ex.Message;
                WriteImmediateSystemEvent("event_error", _lastError);
            }
        }

        private static string CaptureClickScreenshot(InputEvent ev)
        {
            try
            {
                Rectangle bounds = SystemInformation.VirtualScreen;
                string fileName = "click-" + ev.Timestamp.ToString("yyyyMMdd-HHmmss-fff") + "-" +
                    SanitizeFilePart(ev.Button) + "-x" + ev.X + "-y" + ev.Y + ".png";
                string path = Path.Combine(_todayScreenshotDir, fileName);

                using (Bitmap bitmap = new Bitmap(bounds.Width, bounds.Height, PixelFormat.Format32bppArgb))
                using (Graphics graphics = Graphics.FromImage(bitmap))
                {
                    graphics.CopyFromScreen(bounds.Left, bounds.Top, 0, 0, bounds.Size, CopyPixelOperation.SourceCopy);
                    int localX = ev.X - bounds.Left;
                    int localY = ev.Y - bounds.Top;
                    DrawClickMarker(graphics, localX, localY, ev);
                    bitmap.Save(path, ImageFormat.Png);
                }

                return path;
            }
            catch (Exception ex)
            {
                _lastError = "screenshot failed: " + ex.Message;
                return null;
            }
        }

        private static void DrawClickMarker(Graphics graphics, int x, int y, InputEvent ev)
        {
            using (Pen pen = new Pen(Color.Red, 4))
            using (SolidBrush fill = new SolidBrush(Color.FromArgb(190, Color.White)))
            using (SolidBrush textBrush = new SolidBrush(Color.Red))
            using (Font font = new Font(FontFamily.GenericMonospace, 14, FontStyle.Bold))
            {
                int radius = 18;
                graphics.DrawEllipse(pen, x - radius, y - radius, radius * 2, radius * 2);
                graphics.DrawLine(pen, x - 28, y, x + 28, y);
                graphics.DrawLine(pen, x, y - 28, x, y + 28);

                string label = ev.Button + " " + ev.X + "," + ev.Y;
                SizeF labelSize = graphics.MeasureString(label, font);
                float labelX = Math.Max(0, x + 14);
                float labelY = Math.Max(0, y + 14);
                graphics.FillRectangle(fill, labelX, labelY, labelSize.Width + 8, labelSize.Height + 6);
                graphics.DrawString(label, font, textBrush, labelX + 4, labelY + 3);
            }
        }

        private static string SanitizeFilePart(string value)
        {
            if (String.IsNullOrEmpty(value))
            {
                return "unknown";
            }
            StringBuilder sb = new StringBuilder();
            foreach (char ch in value)
            {
                if ((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '-' || ch == '_')
                {
                    sb.Append(ch);
                }
            }
            if (sb.Length == 0)
            {
                return "value";
            }
            return sb.ToString();
        }

        private static WindowSnapshot CaptureActiveWindow()
        {
            try
            {
                IntPtr hwnd = GetForegroundWindow();
                if (hwnd == IntPtr.Zero)
                {
                    return null;
                }

                StringBuilder title = new StringBuilder(512);
                GetWindowText(hwnd, title, title.Capacity);

                int pid;
                GetWindowThreadProcessId(hwnd, out pid);
                string processName = "";
                try
                {
                    Process process = Process.GetProcessById(pid);
                    processName = process.ProcessName;
                }
                catch
                {
                }

                WindowSnapshot snapshot = new WindowSnapshot();
                snapshot.Hwnd = hwnd.ToInt64();
                snapshot.Title = Limit(title.ToString(), 160);
                snapshot.ProcessId = pid;
                snapshot.ProcessName = Limit(processName, 80);
                return snapshot;
            }
            catch
            {
                return null;
            }
        }

        private static ElementSnapshot CaptureElementAtPoint(int x, int y)
        {
            try
            {
                AutomationElement element = AutomationElement.FromPoint(new System.Windows.Point(x, y));
                if (element == null)
                {
                    return null;
                }

                ElementSnapshot snapshot = new ElementSnapshot();
                snapshot.Name = SafeElementName(element);
                snapshot.ControlType = SafeElementText(element, AutomationElement.ControlTypeProperty, 80);
                if (!String.IsNullOrEmpty(snapshot.ControlType) && snapshot.ControlType.StartsWith("ControlType.", StringComparison.Ordinal))
                {
                    snapshot.ControlType = snapshot.ControlType.Substring("ControlType.".Length);
                }
                snapshot.AutomationId = SafeElementText(element, AutomationElement.AutomationIdProperty, 100);
                snapshot.ClassName = SafeElementText(element, AutomationElement.ClassNameProperty, 100);
                snapshot.FrameworkId = SafeElementText(element, AutomationElement.FrameworkIdProperty, 50);
                snapshot.ProcessId = SafeElementInt(element, AutomationElement.ProcessIdProperty);

                try
                {
                    System.Windows.Rect rect = element.Current.BoundingRectangle;
                    if (!rect.IsEmpty)
                    {
                        snapshot.Left = (int)Math.Round(rect.Left);
                        snapshot.Top = (int)Math.Round(rect.Top);
                        snapshot.Width = (int)Math.Round(rect.Width);
                        snapshot.Height = (int)Math.Round(rect.Height);
                    }
                }
                catch
                {
                }
                return snapshot;
            }
            catch
            {
                return null;
            }
        }

        private static string SafeElementName(AutomationElement element)
        {
            string type = SafeElementText(element, AutomationElement.ControlTypeProperty, 80);
            string name = SafeElementText(element, AutomationElement.NameProperty, 120);
            if (String.IsNullOrEmpty(name))
            {
                return "";
            }
            if (type != null && (type.IndexOf("Edit", StringComparison.OrdinalIgnoreCase) >= 0 ||
                type.IndexOf("Document", StringComparison.OrdinalIgnoreCase) >= 0))
            {
                return "[redacted:" + type.Replace("ControlType.", "") + "]";
            }
            return name;
        }

        private static string SafeElementText(AutomationElement element, AutomationProperty property, int limit)
        {
            try
            {
                object value = element.GetCurrentPropertyValue(property, true);
                if (value == null || value == AutomationElement.NotSupported)
                {
                    return "";
                }
                ControlType controlType = value as ControlType;
                if (controlType != null)
                {
                    return Limit(controlType.ProgrammaticName, limit);
                }
                return Limit(Convert.ToString(value), limit);
            }
            catch
            {
                return "";
            }
        }

        private static int SafeElementInt(AutomationElement element, AutomationProperty property)
        {
            try
            {
                object value = element.GetCurrentPropertyValue(property, true);
                if (value == null || value == AutomationElement.NotSupported)
                {
                    return 0;
                }
                return Convert.ToInt32(value);
            }
            catch
            {
                return 0;
            }
        }

        private static string Limit(string value, int max)
        {
            if (String.IsNullOrEmpty(value))
            {
                return "";
            }
            value = value.Replace("\r", " ").Replace("\n", " ").Trim();
            if (value.Length <= max)
            {
                return value;
            }
            return value.Substring(0, max);
        }

        private static string MakeRelativePath(string root, string path)
        {
            if (String.IsNullOrEmpty(root) || String.IsNullOrEmpty(path))
            {
                return path;
            }
            string normalizedRoot = root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
            if (path.StartsWith(normalizedRoot, StringComparison.OrdinalIgnoreCase))
            {
                return path.Substring(normalizedRoot.Length);
            }
            return path;
        }

        private static void AppendJsonLine(Dictionary<string, object> payload)
        {
            lock (WriteLock)
            {
                string line = Json.Serialize(payload);
                File.AppendAllText(_todayLogPath, line + Environment.NewLine, Utf8NoBom);
            }
        }

        private static void WriteImmediateSystemEvent(string action, string message)
        {
            try
            {
                EnsureToday();
                Dictionary<string, object> payload = new Dictionary<string, object>();
                payload["time"] = DateTimeOffset.Now.ToString("yyyy-MM-ddTHH:mm:ss.fffzzz");
                payload["type"] = "system";
                payload["action"] = action;
                payload["version"] = Version;
                if (!String.IsNullOrEmpty(message))
                {
                    payload["message"] = message;
                }
                AppendJsonLine(payload);
                _lastEventAt = Convert.ToString(payload["time"]);
                WriteState();
            }
            catch
            {
            }
        }

        private static void WriteState()
        {
            try
            {
                Dictionary<string, object> state = new Dictionary<string, object>();
                state["ok"] = String.IsNullOrEmpty(_lastError);
                state["version"] = Version;
                state["pid"] = Process.GetCurrentProcess().Id;
                state["started_at"] = StartedAt.ToString("yyyy-MM-ddTHH:mm:ss.fffzzz");
                state["updated_at"] = DateTimeOffset.Now.ToString("yyyy-MM-ddTHH:mm:ss.fffzzz");
                state["current_day"] = _currentDay;
                state["base_dir"] = _baseDir;
                state["log_path"] = _todayLogPath;
                state["screenshot_dir"] = _todayScreenshotDir;
                state["event_count"] = Interlocked.Read(ref _eventCount);
                state["click_count"] = Interlocked.Read(ref _clickCount);
                state["key_count"] = Interlocked.Read(ref _keyCount);
                state["last_event_at"] = _lastEventAt;
                state["last_click_screenshot"] = _lastClickScreenshot;
                state["text_key_logging"] = _logTextKeys;
                state["last_error"] = _lastError;

                string content = Json.Serialize(state);
                string temp = _statePath + ".tmp";
                lock (WriteLock)
                {
                    File.WriteAllText(temp, content, Utf8NoBom);
                    if (File.Exists(_statePath))
                    {
                        File.Delete(_statePath);
                    }
                    File.Move(temp, _statePath);
                }
            }
            catch
            {
            }
        }

        private static IntPtr MouseHookCallback(int nCode, IntPtr wParam, IntPtr lParam)
        {
            if (nCode >= 0)
            {
                int message = wParam.ToInt32();
                if (message == WM_LBUTTONDOWN || message == WM_LBUTTONUP ||
                    message == WM_RBUTTONDOWN || message == WM_RBUTTONUP ||
                    message == WM_MBUTTONDOWN || message == WM_MBUTTONUP ||
                    message == WM_MOUSEWHEEL)
                {
                    MSLLHOOKSTRUCT info = (MSLLHOOKSTRUCT)Marshal.PtrToStructure(lParam, typeof(MSLLHOOKSTRUCT));
                    InputEvent ev = new InputEvent();
                    ev.Timestamp = DateTimeOffset.Now;
                    ev.X = info.pt.x;
                    ev.Y = info.pt.y;
                    ev.HasPoint = true;
                    ev.Modifiers = GetModifiers();

                    if (message == WM_MOUSEWHEEL)
                    {
                        ev.Kind = "mouse_wheel";
                        ev.Button = "Wheel";
                        ev.WheelDelta = unchecked((short)((info.mouseData >> 16) & 0xffff));
                    }
                    else
                    {
                        ev.Kind = (message == WM_LBUTTONDOWN || message == WM_RBUTTONDOWN || message == WM_MBUTTONDOWN)
                            ? "mouse_down"
                            : "mouse_up";
                        ev.Button = MouseButtonName(message);
                    }
                    Enqueue(ev);
                }
            }
            return CallNextHookEx(_mouseHook, nCode, wParam, lParam);
        }

        private static string MouseButtonName(int message)
        {
            if (message == WM_LBUTTONDOWN || message == WM_LBUTTONUP)
            {
                return "Left";
            }
            if (message == WM_RBUTTONDOWN || message == WM_RBUTTONUP)
            {
                return "Right";
            }
            if (message == WM_MBUTTONDOWN || message == WM_MBUTTONUP)
            {
                return "Middle";
            }
            return "Unknown";
        }

        private static IntPtr KeyboardHookCallback(int nCode, IntPtr wParam, IntPtr lParam)
        {
            if (nCode >= 0)
            {
                int message = wParam.ToInt32();
                if (message == WM_KEYDOWN || message == WM_SYSKEYDOWN)
                {
                    KBDLLHOOKSTRUCT info = (KBDLLHOOKSTRUCT)Marshal.PtrToStructure(lParam, typeof(KBDLLHOOKSTRUCT));
                    InputEvent ev = CreateKeyEvent((int)info.vkCode);
                    Enqueue(ev);
                }
            }
            return CallNextHookEx(_keyboardHook, nCode, wParam, lParam);
        }

        private static InputEvent CreateKeyEvent(int vkCode)
        {
            Keys key = (Keys)vkCode;
            string modifiers = GetModifiers();
            KeyClassification classification = ClassifyKey(vkCode, key, modifiers);

            InputEvent ev = new InputEvent();
            ev.Timestamp = DateTimeOffset.Now;
            ev.Kind = "key_down";
            ev.VirtualKeyCode = vkCode;
            ev.Modifiers = modifiers;
            ev.KeyCategory = classification.Category;
            ev.TextRedacted = classification.Redacted;
            ev.IncludeKeyCode = classification.IncludeKeyCode;
            ev.KeyName = classification.Name;
            return ev;
        }

        private static KeyClassification ClassifyKey(int vkCode, Keys key, string modifiers)
        {
            bool commandCombo = ContainsModifier(modifiers, "Ctrl") || ContainsModifier(modifiers, "Alt") || ContainsModifier(modifiers, "Win");
            bool isLetter = vkCode >= 0x41 && vkCode <= 0x5A;
            bool isDigit = vkCode >= 0x30 && vkCode <= 0x39;
            bool isNumpadDigit = vkCode >= 0x60 && vkCode <= 0x69;
            bool isSpace = key == Keys.Space;
            bool isTextLike = isLetter || isDigit || isNumpadDigit || isSpace || IsSymbolKey(key);

            KeyClassification result = new KeyClassification();
            result.IncludeKeyCode = true;
            result.Redacted = false;

            if (isTextLike && !_logTextKeys && !commandCombo)
            {
                result.Redacted = true;
                result.IncludeKeyCode = false;
                if (isLetter)
                {
                    result.Name = "[letter]";
                    result.Category = "letter";
                }
                else if (isDigit || isNumpadDigit)
                {
                    result.Name = "[digit]";
                    result.Category = isNumpadDigit ? "numpad_digit" : "digit";
                }
                else if (isSpace)
                {
                    result.Name = "[space]";
                    result.Category = "space";
                }
                else
                {
                    result.Name = "[symbol]";
                    result.Category = "symbol";
                }
                return result;
            }

            if (commandCombo && isTextLike)
            {
                result.Category = "shortcut";
                result.Name = key.ToString();
                return result;
            }

            if (isLetter)
            {
                result.Category = "letter";
            }
            else if (isDigit)
            {
                result.Category = "digit";
            }
            else if (isNumpadDigit)
            {
                result.Category = "numpad_digit";
            }
            else if (IsNavigationKey(key))
            {
                result.Category = "navigation";
            }
            else if (IsFunctionKey(key))
            {
                result.Category = "function";
            }
            else if (IsModifierKey(key))
            {
                result.Category = "modifier";
            }
            else if (IsEditingKey(key))
            {
                result.Category = "editing";
            }
            else
            {
                result.Category = "control";
            }
            result.Name = key.ToString();
            return result;
        }

        private static bool ContainsModifier(string modifiers, string name)
        {
            if (String.IsNullOrEmpty(modifiers))
            {
                return false;
            }
            return modifiers.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static bool IsSymbolKey(Keys key)
        {
            return key == Keys.OemBackslash || key == Keys.OemCloseBrackets || key == Keys.Oemcomma ||
                key == Keys.OemMinus || key == Keys.OemOpenBrackets || key == Keys.OemPeriod ||
                key == Keys.OemPipe || key == Keys.Oemplus || key == Keys.OemQuestion ||
                key == Keys.OemQuotes || key == Keys.OemSemicolon || key == Keys.Oemtilde ||
                key == Keys.Multiply || key == Keys.Add || key == Keys.Subtract ||
                key == Keys.Decimal || key == Keys.Divide;
        }

        private static bool IsNavigationKey(Keys key)
        {
            return key == Keys.Left || key == Keys.Right || key == Keys.Up || key == Keys.Down ||
                key == Keys.Home || key == Keys.End || key == Keys.PageUp || key == Keys.PageDown;
        }

        private static bool IsFunctionKey(Keys key)
        {
            return key >= Keys.F1 && key <= Keys.F24;
        }

        private static bool IsModifierKey(Keys key)
        {
            return key == Keys.ShiftKey || key == Keys.LShiftKey || key == Keys.RShiftKey ||
                key == Keys.ControlKey || key == Keys.LControlKey || key == Keys.RControlKey ||
                key == Keys.Menu || key == Keys.LMenu || key == Keys.RMenu ||
                key == Keys.LWin || key == Keys.RWin;
        }

        private static bool IsEditingKey(Keys key)
        {
            return key == Keys.Back || key == Keys.Delete || key == Keys.Insert ||
                key == Keys.Enter || key == Keys.Escape || key == Keys.Tab;
        }

        private static string GetModifiers()
        {
            List<string> modifiers = new List<string>();
            if (IsKeyDown(VK_SHIFT))
            {
                modifiers.Add("Shift");
            }
            if (IsKeyDown(VK_CONTROL))
            {
                modifiers.Add("Ctrl");
            }
            if (IsKeyDown(VK_MENU))
            {
                modifiers.Add("Alt");
            }
            if (IsKeyDown(VK_LWIN) || IsKeyDown(VK_RWIN))
            {
                modifiers.Add("Win");
            }
            return String.Join("+", modifiers.ToArray());
        }

        private static bool IsKeyDown(int virtualKey)
        {
            return (GetKeyState(virtualKey) & 0x8000) != 0;
        }

        private sealed class InputEvent
        {
            public DateTimeOffset Timestamp;
            public string Kind;
            public string Action;
            public bool HasPoint;
            public int X;
            public int Y;
            public string Button;
            public int WheelDelta;
            public int VirtualKeyCode;
            public string KeyName;
            public string KeyCategory;
            public bool IncludeKeyCode;
            public bool TextRedacted;
            public string Modifiers;

            public static InputEvent System(string action)
            {
                InputEvent ev = new InputEvent();
                ev.Timestamp = DateTimeOffset.Now;
                ev.Kind = "system";
                ev.Action = action;
                return ev;
            }
        }

        private sealed class KeyClassification
        {
            public string Name;
            public string Category;
            public bool IncludeKeyCode;
            public bool Redacted;
        }

        private sealed class WindowSnapshot
        {
            public long Hwnd;
            public string Title;
            public int ProcessId;
            public string ProcessName;

            public Dictionary<string, object> ToDictionary()
            {
                Dictionary<string, object> value = new Dictionary<string, object>();
                value["hwnd"] = Hwnd;
                value["title"] = Title;
                value["pid"] = ProcessId;
                value["process"] = ProcessName;
                return value;
            }
        }

        private sealed class ElementSnapshot
        {
            public string Name;
            public string ControlType;
            public string AutomationId;
            public string ClassName;
            public string FrameworkId;
            public int ProcessId;
            public int Left;
            public int Top;
            public int Width;
            public int Height;

            public Dictionary<string, object> ToDictionary()
            {
                Dictionary<string, object> value = new Dictionary<string, object>();
                value["name"] = Name;
                value["control_type"] = ControlType;
                value["automation_id"] = AutomationId;
                value["class_name"] = ClassName;
                value["framework_id"] = FrameworkId;
                value["pid"] = ProcessId;
                value["left"] = Left;
                value["top"] = Top;
                value["width"] = Width;
                value["height"] = Height;
                return value;
            }
        }

        private delegate IntPtr HookProc(int nCode, IntPtr wParam, IntPtr lParam);

        [StructLayout(LayoutKind.Sequential)]
        private struct POINT
        {
            public int x;
            public int y;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct MSLLHOOKSTRUCT
        {
            public POINT pt;
            public uint mouseData;
            public uint flags;
            public uint time;
            public IntPtr dwExtraInfo;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct KBDLLHOOKSTRUCT
        {
            public uint vkCode;
            public uint scanCode;
            public uint flags;
            public uint time;
            public IntPtr dwExtraInfo;
        }

        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern IntPtr SetWindowsHookEx(int idHook, HookProc lpfn, IntPtr hMod, uint dwThreadId);

        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool UnhookWindowsHookEx(IntPtr hhk);

        [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);

        [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern IntPtr GetModuleHandle(string lpModuleName);

        [DllImport("user32.dll")]
        private static extern short GetKeyState(int nVirtKey);

        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out int lpdwProcessId);
    }
}
