import argparse
import datetime as dt
import json
import msvcrt
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "finance_cache"
STATE_PATH = DATA_DIR / "cash_exception_monitor_state.json"
HEARTBEAT_PATH = DATA_DIR / "cash_exception_monitor_heartbeat.json"
LOCK_PATH = DATA_DIR / "cash_exception_monitor.lock"
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "cash-exception-monitor.log"
LOG_MAX_BYTES = int(os.getenv("CASH_EXCEPTION_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("CASH_EXCEPTION_LOG_BACKUP_COUNT", "2"))
DEFAULT_ROOT = r"C:\ProtechFile"
DEFAULT_6KAK_DISCORD_WEBHOOK_URL = (
    "https://discord.com/api/webhooks/1338245248098500759/"
    "uXuI-K9tJR1DVZXas-8vFsnqaupMxySXEOqRQ15tlzzj7VjNaWZLNodyJzgNhH98gL1J"
)
DEFAULT_AUDIO_URL = "http://100.114.61.65:3000/api/control/pi-audio"
DEFAULT_AUDIO_FALLBACK_URL = "http://100.114.19.115:3011/api/audio/test"
DEFAULT_KIOSK_AGENT_STATUS_URL = "http://100.113.224.68:3010/api/status"
DEFAULT_CASH_EXCEPTION_AGENT_URL = "http://100.113.224.68:3010/api/cash-exception"
DEFAULT_WARNING_AUDIO_TYPE = "wrong"
DEFAULT_CRITICAL_AUDIO_TYPE = "error"
DEFAULT_BUSINESS_START = "10:30"
DEFAULT_BUSINESS_END = "21:30"
NOTIFICATION_HISTORY_LIMIT = 200
LOCK_HANDLE = None
LIMITED_LOG_TIMES = {}
EVENT_RE = re.compile(r"^\[(?P<timestamp>[^\]]+)\]\s*Error:\s*\((?P<code>\d+)\)(?P<message>.*)$")


CODE_RULES = {
    "1000001": ("warning", "capacity", "錢箱超出水位"),
    "1000002": ("warning", "capacity", "Recycler低於水位"),
    "1000009": ("warning", "transaction", "退款金額超出最大限額"),
    "1001001": ("critical", "banknote", "紙鈔現金裝置故障"),
    "1001003": ("warning", "cashbox", "紙鈔錢箱被打開或未就定位"),
    "1001005": ("critical", "banknote", "紙鈔錢箱有異物卡住"),
    "1001007": ("warning", "banknote", "紙鈔Recycler空了"),
    "1001009": ("critical", "banknote", "紙鈔Recycler卡住"),
    "1001012": ("warning", "banknote", "紙鈔Recycler未就定位"),
    "1001016": ("warning", "banknote", "紙鈔現金裝置電源被重啟"),
    "1001900": ("critical", "banknote", "紙鈔現金裝置 API 錯誤"),
    "1002003": ("critical", "coin", "退幣桿卡住"),
    "1002006": ("critical", "coin", "受幣器卡幣"),
    "1002007": ("critical", "coin", "退幣器卡住"),
    "1002008": ("warning", "recycler", "Recycler錢筒盒配置錯誤"),
    "1002009": ("warning", "coin", "手動補幣模式"),
    "1002900": ("warning", "coin", "現金裝置異常"),
    "47": ("warning", "unknown", "未定義錯誤"),
    "48": ("warning", "unknown", "未定義錯誤"),
}

SHORT_MESSAGES = {
    "錢箱超出水位": "錢箱水位過高",
    "現金裝置Recycler低於水位": "找零機水位偏低",
    "退款金額超出最大限額": "退款金額超過上限",
    "紙鈔現金裝置故障 - (0x4A)Recycler故障": "紙鈔Recycler故障",
    "紙鈔現金裝置故障 - (0xA2)錢箱馬達故障": "紙鈔錢箱馬達故障",
    "紙鈔錢箱被打開或未就定位": "紙鈔錢箱未就定位",
    "紙鈔錢箱有異物卡住": "紙鈔錢箱異物卡住",
    "紙鈔Recycler空了": "紙鈔Recycler已空",
    "紙鈔Recycler單元有紙鈔或異物卡住": "紙鈔Recycler卡住",
    "紙鈔Recycler未就定位": "紙鈔Recycler未就定位",
    "紙鈔現金裝置電源被重啟": "紙鈔裝置電源重啟",
    "紙鈔現金裝置 API ERROR - (0x80000301)裝置無回應": "紙鈔裝置無回應",
    "紙鈔現金裝置 API ERROR - (0x80000303)收到無效的裝置回應": "紙鈔裝置回應無效",
    "退幣桿卡住 (超過1分鐘)": "退幣桿卡住",
    "受幣器異常 - (0x1130)受幣器卡幣": "受幣器卡幣",
    "退幣器卡住": "退幣器卡住",
    "硬幣機正在進行手動補幣操作模式": "硬幣機手動補幣中",
    "硬幣現金裝置 API ERROR - (0x0001)CF7000硬幣裝置 API Time Out": "硬幣機逾時",
    "硬幣現金裝置 API ERROR - (0x0003)CF7000硬幣裝置連線失敗": "硬幣機連線失敗",
    "硬幣現金裝置 API ERROR - (0xFFFF)拒絕存取通訊埠。": "通訊埠拒絕存取",
    "硬幣現金裝置 API ERROR - (0xFFFF)通訊埠已經關閉。": "通訊埠已關閉",
    "此錯誤尚未被定義": "未定義錯誤",
}

CRITICAL_MESSAGE_PATTERNS = [
    "卡",
    "無回應",
    "連線失敗",
    "通訊埠已經關閉",
    "拒絕存取通訊埠",
    "馬達故障",
    "Recycler故障",
]


def force_utf8_stdio():
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def now_text():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def rotate_log_if_needed():
    try:
        if LOG_MAX_BYTES <= 0 or not LOG_PATH.exists():
            return
        if LOG_PATH.stat().st_size < LOG_MAX_BYTES:
            return
        oldest = LOG_PATH.with_name(f"{LOG_PATH.name}.{LOG_BACKUP_COUNT}")
        if oldest.exists():
            oldest.unlink()
        for index in range(LOG_BACKUP_COUNT - 1, 0, -1):
            source = LOG_PATH.with_name(f"{LOG_PATH.name}.{index}")
            target = LOG_PATH.with_name(f"{LOG_PATH.name}.{index + 1}")
            if source.exists():
                source.replace(target)
        LOG_PATH.replace(LOG_PATH.with_name(f"{LOG_PATH.name}.1"))
    except Exception:
        pass


def parse_hhmm(value):
    try:
        hour_text, minute_text = str(value).split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except Exception as exc:
        raise ValueError(f"invalid HH:MM time: {value}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid HH:MM time: {value}")
    return dt.time(hour, minute)


def is_business_time(now, start_text, end_text):
    start = parse_hhmm(start_text)
    end = parse_hhmm(end_text)
    current = now.time()
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def write_log(message):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{now_text()}] {message}"
    print(line, flush=True)
    rotate_log_if_needed()
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def write_log_limited(key, message, repeat_seconds=900):
    now_ts = time.time()
    previous_ts = float(LIMITED_LOG_TIMES.get(key) or 0)
    if previous_ts and now_ts - previous_ts < repeat_seconds:
        return False
    LIMITED_LOG_TIMES[key] = now_ts
    write_log(message)
    return True


def acquire_single_instance_lock():
    global LOCK_HANDLE
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+")
    handle.seek(0)
    if not handle.read(1):
        handle.write("1")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return False
    LOCK_HANDLE = handle
    return True


def atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def summarize_error(error):
    text = str(error).strip()
    if "Cannot find path" in text or "does not exist" in text:
        return "kiosk log path unavailable"
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first_line[:240] if first_line else error.__class__.__name__


def is_kiosk_connection_error(error):
    text = str(error)
    return any(
        marker in text
        for marker in (
            "urlopen error",
            "timed out",
            "Connection refused",
            "No route to host",
            "Cannot find path",
            "does not exist",
            "Access is denied",
        )
    )


def fetch_kiosk_agent_status(args):
    request = urllib.request.Request(
        args.kiosk_agent_status_url,
        headers={"User-Agent": "6ka-cash-exception-monitor"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=args.kiosk_agent_timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        if response.status >= 300:
            raise RuntimeError(f"Kiosk Agent HTTP {response.status}: {body[:200]}")
        return json.loads(body) if body else {}


def kiosk_agent_is_online(payload):
    agent = payload.get("agent") if isinstance(payload, dict) else None
    database = payload.get("database") if isinstance(payload, dict) else None
    agent_online = bool(agent and agent.get("online"))
    database_ok = True if not isinstance(database, dict) else bool(database.get("ok", True))
    return agent_online and database_ok


def should_poll_kiosk(args):
    if args.skip_agent_check:
        return True
    try:
        payload = fetch_kiosk_agent_status(args)
        online = kiosk_agent_is_online(payload)
        if online:
            if getattr(args, "_last_kiosk_agent_online", None) is not True:
                write_log("狀態 online source=kiosk_agent action=resume_polling")
            args._last_kiosk_agent_online = True
            return True
        reason = "agent reported offline"
    except Exception as exc:
        online = False
        reason = f"agent unreachable: {summarize_error(exc)}"

    if getattr(args, "_last_kiosk_agent_online", None) is not False:
        write_log(f"狀態 kiosk_unavailable reason={reason} action=skip_polling")
    args._last_kiosk_agent_online = False
    return False


def load_dotenv(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


def read_state():
    if not STATE_PATH.exists():
        return {"notified": {}, "daily_counts": {}, "notifications": []}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        state.setdefault("notified", {})
        state.setdefault("daily_counts", {})
        state.setdefault("notifications", [])
        if not state["notifications"] and isinstance(state.get("notified"), dict):
            for key, value in state["notified"].items():
                if not isinstance(value, dict):
                    continue
                parts = str(key).split("|", 2)
                if len(parts) != 3:
                    continue
                timestamp, code, message = parts
                sent_at = value.get("sent_at")
                state["notifications"].append(
                    {
                        "date": timestamp[:10],
                        "timestamp": timestamp,
                        "code": code,
                        "message": message,
                        "short_message": SHORT_MESSAGES.get(message, message),
                        "severity": None,
                        "category": None,
                        "reason": value.get("reason"),
                        "notify_message": None,
                        "sent_at": sent_at,
                    }
                )
        return state
    except Exception:
        return {"notified": {}, "daily_counts": {}, "notifications": []}


def write_state(state):
    atomic_write_json(STATE_PATH, state)


def compact_event(event, notify_message=None, reason=None, sent_at=None):
    return {
        "date": event.get("date"),
        "timestamp": event.get("timestamp"),
        "code": event.get("code"),
        "message": event.get("message"),
        "short_message": event.get("short_message"),
        "severity": event.get("severity"),
        "category": event.get("category"),
        "reason": reason,
        "notify_message": notify_message,
        "sent_at": sent_at,
    }


def append_notification(state, event, notify_message, reason):
    sent_at = dt.datetime.now().isoformat(timespec="seconds")
    record = compact_event(event, notify_message=notify_message, reason=reason, sent_at=sent_at)
    state["notified"][event["key"]] = record
    notifications = [item for item in state.get("notifications", []) if isinstance(item, dict)]
    notifications.append(record)
    state["notifications"] = notifications[-NOTIFICATION_HISTORY_LIMIT:]
    return record


def notifications_for_date(state, target_date):
    rows = []
    for item in state.get("notifications", []):
        if not isinstance(item, dict):
            continue
        item_date = item.get("date") or str(item.get("sent_at") or "")[:10]
        if item_date == target_date:
            rows.append(item)
    return sorted(rows, key=lambda item: item.get("sent_at") or item.get("timestamp") or "")


def summarize_notifications(rows):
    summary = {}
    for item in rows:
        key = (item.get("code") or "-", item.get("short_message") or item.get("message") or "-")
        current = summary.setdefault(
            key,
            {
                "code": key[0],
                "short_message": key[1],
                "count": 0,
                "last_sent_at": None,
            },
        )
        current["count"] += 1
        sent_at = item.get("sent_at")
        if sent_at and (not current["last_sent_at"] or sent_at > current["last_sent_at"]):
            current["last_sent_at"] = sent_at
    return sorted(summary.values(), key=lambda item: (item["last_sent_at"] or "", item["code"]), reverse=True)


def write_heartbeat(status, target_date=None, events=None, notifications=None, error=None):
    target_date = target_date or dt.date.today().isoformat()
    state = read_state()
    today_notifications = notifications_for_date(state, target_date)
    heartbeat = {
        "ok": status not in ("error", "kiosk_unavailable"),
        "program": "CashException",
        "pid": os.getpid(),
        "status": status,
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "date": target_date,
        "source": "agent",
        "agent_url": DEFAULT_CASH_EXCEPTION_AGENT_URL,
        "event_count": len(events or []),
        "notification_count": len(notifications or []),
        "notifications_today": len(today_notifications),
        "last_notification": today_notifications[-1] if today_notifications else None,
        "last_error": error,
    }
    atomic_write_json(HEARTBEAT_PATH, heartbeat)
    return heartbeat


def parse_event(line, source):
    match = EVENT_RE.match(line.strip())
    if not match:
        return None
    event = {
        "timestamp": match.group("timestamp").strip(),
        "code": match.group("code").strip(),
        "message": match.group("message").strip(),
        "source": source,
    }
    event["date"] = event["timestamp"][:10]
    severity, category, reason = classify_event(event)
    event["severity"] = severity
    event["category"] = category
    event["reason"] = reason
    event["short_message"] = SHORT_MESSAGES.get(event["message"], event["message"])
    event["key"] = f"{event['timestamp']}|{event['code']}|{event['message']}"
    return event


def classify_event(event):
    severity, category, reason = CODE_RULES.get(event["code"], ("warning", "unknown", "未分類故障碼"))
    message = event["message"]
    if any(pattern in message for pattern in CRITICAL_MESSAGE_PATTERNS):
        severity = "critical"
    return severity, category, reason


def read_local_lines(root, target_date):
    path = Path(root) / target_date / "CashException" / f"{target_date}-log.txt"
    if not path.exists():
        return [], str(path)
    return path.read_text(encoding="mbcs", errors="replace").splitlines(), str(path)


def read_agent_lines(target_date):
    base_url = os.getenv("CASH_EXCEPTION_AGENT_URL", DEFAULT_CASH_EXCEPTION_AGENT_URL)
    separator = "&" if "?" in base_url else "?"
    url = base_url + separator + urllib.parse.urlencode({"date": target_date})
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "6ka-cash-exception-monitor"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=float(os.getenv("CASH_EXCEPTION_AGENT_TIMEOUT_SECONDS", "5"))) as response:
        body = response.read().decode("utf-8", errors="replace")
        if response.status >= 300:
            raise RuntimeError(f"Kiosk Agent HTTP {response.status}: {body[:200]}")
        payload = json.loads(body) if body else {}
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "Kiosk Agent cash exception request failed")
    lines = payload.get("lines") or []
    source = payload.get("source") or url
    return [str(line) for line in lines], source


def read_events(target_date, source_mode, root):
    if source_mode != "agent":
        raise RuntimeError("CashException monitor only supports Tailscale HTTP agent source")
    lines, source = read_agent_lines(target_date)
    events = [parse_event(line, source) for line in lines]
    return [event for event in events if event]


def event_score(event):
    return 2 if event["severity"] == "critical" else 1


def should_notify(event, state, repeated_threshold):
    if event["key"] in state["notified"]:
        return False, "already_notified"
    return True, "new_event"


def format_event_message(event, daily_count, reason):
    return f"# 找零機：{event['short_message']}"


def send_discord(message):
    webhook = (
        os.getenv("CASH_EXCEPTION_DISCORD_WEBHOOK_URL")
        or os.getenv("KITCHEN_DISCORD_WEBHOOK_URL")
        or os.getenv("DISCORD_WEBHOOK_URL")
        or os.getenv("DC_WEBHOOK_URL")
        or DEFAULT_6KAK_DISCORD_WEBHOOK_URL
    )
    if not webhook:
        raise RuntimeError("missing CASH_EXCEPTION_DISCORD_WEBHOOK_URL / DISCORD_WEBHOOK_URL / DC_WEBHOOK_URL")
    payload = json.dumps({"content": message}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "6ka-cash-exception-monitor"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status >= 300:
            raise RuntimeError(f"Discord HTTP {response.status}")


def post_json(url, payload, timeout=10):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "6ka-cash-exception-monitor"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        if response.status >= 300:
            raise RuntimeError(f"HTTP {response.status}: {body[:200]}")
        return body


def play_warning_audio(event, args):
    if event["severity"] == "critical":
        audio_type = args.critical_audio_type
    else:
        audio_type = args.warning_audio_type
    payload = {"type": audio_type, "source": "cash_exception_monitor"}
    urls = [
        os.getenv("CASH_EXCEPTION_AUDIO_URL", DEFAULT_AUDIO_URL),
        os.getenv("CASH_EXCEPTION_AUDIO_FALLBACK_URL", DEFAULT_AUDIO_FALLBACK_URL),
    ]
    errors = []
    for url in [item for item in urls if item]:
        try:
            post_json(url, payload, timeout=8)
            return url
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError(" | ".join(errors))


def process_once(args):
    target_date = args.date or dt.date.today().isoformat()
    if not args.ignore_business_hours and not is_business_time(dt.datetime.now(), args.business_start, args.business_end):
        write_log_limited(
            "outside_business_hours",
            f"狀態 outside_business_hours business={args.business_start}-{args.business_end} action=skip_polling",
            3600,
        )
        write_heartbeat("outside_business_hours", target_date=target_date)
        return [], [], []

    if not should_poll_kiosk(args):
        write_heartbeat("kiosk_unavailable", target_date=target_date, error="kiosk agent unavailable")
        return [], [], []

    events = read_events(target_date, args.source, args.root)
    state = read_state()
    notifications = []
    if args.mark_existing:
        for event in events:
            day_key = f"{event['date']}|{event['code']}|{event['message']}"
            state["daily_counts"][day_key] = int(state["daily_counts"].get(day_key, 0)) + 1
            state["notified"][event["key"]] = {"sent_at": now_text(), "reason": "marked_existing"}
        if not args.dry_run:
            write_state(state)
        write_log(f"marked existing CashException events date={target_date} count={len(events)} dry_run={args.dry_run}")
        return events, notifications, summarize_events(events)

    for event in events:
        day_key = f"{event['date']}|{event['code']}|{event['message']}"
        notify, reason = should_notify(event, state, args.repeated_threshold)
        current_count = int(state["daily_counts"].get(day_key, 0)) + 1
        state["daily_counts"][day_key] = current_count
        if not notify:
            continue
        message = format_event_message(event, current_count, reason)
        notifications.append((event, message, reason))
        if args.dry_run:
            write_log(f"dry-run notify {event['code']} {event['severity']} reason={reason} message={event['message']}")
        else:
            send_discord(message)
            write_log(f"通知 sent code={event['code']} severity={event['severity']} reason={reason} message={event['short_message']}")
            if not args.no_audio:
                try:
                    audio_url = play_warning_audio(event, args)
                    write_log(f"播音 requested type={event['severity']} via={audio_url}")
                except Exception as exc:
                    write_log(f"播音 failed type={event['severity']} reason={summarize_error(exc)}")
            append_notification(state, event, message, reason)
    if not args.dry_run:
        write_state(state)
    summary = summarize_events(events)
    write_heartbeat("ok", target_date=target_date, events=events, notifications=notifications)
    return events, notifications, summary


def summarize_events(events):
    summary = {}
    for event in events:
        key = (event["code"], event["message"], event["severity"], event["category"])
        summary.setdefault(key, 0)
        summary[key] += 1
    rows = [
        {
            "code": code,
            "message": message,
            "severity": severity,
            "category": category,
            "count": count,
        }
        for (code, message, severity, category), count in summary.items()
    ]
    return sorted(rows, key=lambda row: (-event_score(row), -row["count"], row["code"]))


def print_report(target_date, events, notifications, summary):
    print(f"CashException date={target_date} events={len(events)} notifications={len(notifications)}")
    if summary:
        print("Summary:")
        for row in summary:
            print(
                f"- {row['severity']} {row['category']} {row['code']} x{row['count']}: {row['message']}"
            )
    if notifications:
        print("Notifications:")
        for _event, message, _reason in notifications:
            print("---")
            print(message)


def main():
    force_utf8_stdio()
    load_dotenv(BASE_DIR / ".env")
    parser = argparse.ArgumentParser(description="Monitor kiosk CashException fault codes and optionally notify Discord.")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD. Default: today.")
    parser.add_argument("--root", default=os.getenv("CASH_EXCEPTION_ROOT", DEFAULT_ROOT))
    parser.add_argument("--source", choices=("agent",), default="agent")
    parser.add_argument("--interval", type=int, default=int(os.getenv("CASH_EXCEPTION_MONITOR_INTERVAL", "30")))
    parser.add_argument("--business-start", default=os.getenv("CASH_EXCEPTION_BUSINESS_START", DEFAULT_BUSINESS_START))
    parser.add_argument("--business-end", default=os.getenv("CASH_EXCEPTION_BUSINESS_END", DEFAULT_BUSINESS_END))
    parser.add_argument("--ignore-business-hours", action="store_true")
    parser.add_argument(
        "--connection-retry-interval",
        type=int,
        default=int(os.getenv("CASH_EXCEPTION_CONNECTION_RETRY_INTERVAL", "300")),
    )
    parser.add_argument(
        "--kiosk-agent-status-url",
        default=os.getenv("KIOSK_AGENT_STATUS_URL", DEFAULT_KIOSK_AGENT_STATUS_URL),
    )
    parser.add_argument(
        "--kiosk-agent-timeout",
        type=float,
        default=float(os.getenv("KIOSK_AGENT_TIMEOUT_SECONDS", "2")),
    )
    parser.add_argument("--skip-agent-check", action="store_true")
    parser.add_argument("--repeated-threshold", type=int, default=int(os.getenv("CASH_EXCEPTION_REPEATED_THRESHOLD", "3")))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-audio", action="store_true", help="Send Discord only; do not request wrong.wav playback.")
    parser.add_argument(
        "--warning-audio-type",
        default=os.getenv("CASH_EXCEPTION_WARNING_AUDIO_TYPE", os.getenv("CASH_EXCEPTION_AUDIO_TYPE", DEFAULT_WARNING_AUDIO_TYPE)),
        choices=("wrong", "error", "ding", "beep"),
    )
    parser.add_argument(
        "--critical-audio-type",
        default=os.getenv("CASH_EXCEPTION_CRITICAL_AUDIO_TYPE", DEFAULT_CRITICAL_AUDIO_TYPE),
        choices=("wrong", "error", "ding", "beep"),
    )
    parser.add_argument(
        "--mark-existing",
        action="store_true",
        help="Record current file contents as already handled without sending notifications.",
    )
    args = parser.parse_args()

    if args.once:
        events, notifications, summary = process_once(args)
        print_report(args.date or dt.date.today().isoformat(), events, notifications, summary)
        return

    if not acquire_single_instance_lock():
        write_log("another cash exception monitor is already running; exiting")
        return

    write_log(f"啟動 CashException monitor source={args.source} interval={args.interval}s dry_run={args.dry_run}")
    write_heartbeat("starting", target_date=args.date or dt.date.today().isoformat())
    while True:
        try:
            process_once(args)
        except Exception as exc:
            if is_kiosk_connection_error(exc):
                write_heartbeat("kiosk_unavailable", target_date=args.date or dt.date.today().isoformat(), error=summarize_error(exc))
                write_log_limited(
                    f"kiosk_unavailable:{summarize_error(exc)}",
                    f"狀態 kiosk_unavailable reason={summarize_error(exc)} next_check={args.connection_retry_interval}s",
                    900,
                )
                time.sleep(args.connection_retry_interval)
                continue
            write_heartbeat("error", target_date=args.date or dt.date.today().isoformat(), error=summarize_error(exc))
            write_log_limited(
                f"monitor_error:{summarize_error(exc)}",
                f"狀態 error reason={summarize_error(exc)}",
                300,
            )
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
