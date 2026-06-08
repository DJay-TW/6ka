import argparse
import datetime as dt
import importlib.util
import json
import os
import sys
import time
from pathlib import Path


# -------------------------------
# Basic settings
# -------------------------------

VERSION = "5.0.2"


def force_utf8_stdio():
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


force_utf8_stdio()


def load_dotenv(path):
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv(Path(__file__).with_name(".env"))

SALES_CACHE_QUERY_PATH = Path(os.getenv("SALES_CACHE_QUERY_PATH", r"C:\6KAweb\query_sales_cache.py"))

WEBHOOK_URL = os.getenv(
    "RP_DISCORD_WEBHOOK",
    "",
)

LOG_DIR = Path(os.getenv("RP_LOG_DIR", r"C:\RP_log"))
STATE_FILE = LOG_DIR / "suit_repository_sales_state.json"
HEARTBEAT_FILE = LOG_DIR / "rp5_heartbeat.json"
CONSOLE_LOG_FILE = LOG_DIR / "rp5_console.log"
CONSOLE_LOG_MAX_BYTES = int(os.getenv("RP_CONSOLE_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
CONSOLE_LOG_BACKUP_COUNT = int(os.getenv("RP_CONSOLE_LOG_BACKUP_COUNT", "2"))

POLL_INTERVAL_SECONDS = int(os.getenv("RP_POLL_INTERVAL", "10"))
CONNECT_ERROR_NOTIFY_THRESHOLD = int(os.getenv("RP_CONNECT_ERROR_NOTIFY_THRESHOLD", "3"))
CONNECT_RETRY_SECONDS = int(os.getenv("RP_CONNECT_RETRY_SECONDS", "30"))
STATUS_INTERVAL_SECONDS = int(os.getenv("RP_STATUS_INTERVAL", "300"))
OFFLINE_LOG_INTERVAL_SECONDS = int(os.getenv("RP_OFFLINE_LOG_INTERVAL", "300"))
SYNC_STALE_SECONDS = int(os.getenv("RP_SYNC_STALE_SECONDS", "120"))

BOWL_CATEGORY_NAME = os.getenv("RP_BOWL_CATEGORY_NAME", "拉麵類")


class WindowsMutex:
    def __init__(self, name):
        import ctypes

        self.mutex = ctypes.windll.kernel32.CreateMutexW(None, False, name)
        self.last_error = ctypes.windll.kernel32.GetLastError()
        if self.last_error == 183:
            print("已有另一個營業額監控程式正在執行，結束本次啟動。")
            sys.exit(0)


_mutex = None
_limited_console_logs = {}


def rotate_file_if_needed(path, max_bytes, backup_count):
    try:
        if max_bytes <= 0 or not path.exists():
            return
        if path.stat().st_size < max_bytes:
            return
        oldest = path.with_name(f"{path.name}.{backup_count}")
        if oldest.exists():
            oldest.unlink()
        for index in range(backup_count - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            target = path.with_name(f"{path.name}.{index + 1}")
            if source.exists():
                source.replace(target)
        path.replace(path.with_name(f"{path.name}.1"))
    except Exception:
        pass


def discord_notify(message):
    if not WEBHOOK_URL:
        return

    try:
        import requests

        response = requests.post(
            WEBHOOK_URL,
            json={"content": message},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if response.status_code not in (200, 204):
            console_log(f"Discord notification failed: {response.status_code} {response.text[:200]}")
    except Exception as exc:
        console_log(f"Discord notification error: {exc}")


def console_log(message):
    line = "[{time}] {message}".format(time=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message=message)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        rotate_file_if_needed(CONSOLE_LOG_FILE, CONSOLE_LOG_MAX_BYTES, CONSOLE_LOG_BACKUP_COUNT)
        with CONSOLE_LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def console_log_limited(key, message, repeat_seconds):
    now_ts = time.time()
    previous_ts = float(_limited_console_logs.get(key) or 0)
    if previous_ts and now_ts - previous_ts < repeat_seconds:
        return False
    _limited_console_logs[key] = now_ts
    console_log(message)
    return True


def atomic_write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def atomic_write_json(path, payload):
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def write_notification_to_file(message):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{dt.date.today().isoformat()}.txt"
    atomic_write_text(path, message)
    console_log(f"通知內容已寫入: {path}")


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    atomic_write_json(STATE_FILE, state)


def summarize_error(exc):
    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first_line = lines[0] if lines else text
    error_id = next((line for line in lines if "FullyQualifiedErrorId" in line), "")
    if error_id:
        return f"{first_line} ({error_id})"
    return first_line


def parse_timestamp(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("T", " ").replace("Z", "")
    if "+" in text:
        text = text.split("+", 1)[0].strip()
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            return dt.datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def assess_sync_health(result):
    sync_state = result.get("sync_state") or {}
    if not sync_state:
        return False, "沒有同步狀態"

    last_success = parse_timestamp(sync_state.get("last_success_at"))
    last_error = parse_timestamp(sync_state.get("last_error_at"))
    last_error_text = sync_state.get("last_error") or ""

    if last_error and (not last_success or last_error >= last_success):
        reason = "同步錯誤"
        if last_error_text:
            reason += f"：{last_error_text}"
        return False, reason

    if not last_success:
        return False, "尚無同步成功紀錄"

    age_seconds = (dt.datetime.now() - last_success).total_seconds()
    if age_seconds > SYNC_STALE_SECONDS:
        return False, f"同步逾時 {int(age_seconds)} 秒"

    return True, "同步正常"


def query_sales_cache_snapshot(business_date):
    if not SALES_CACHE_QUERY_PATH.exists():
        raise FileNotFoundError(f"Sales cache query helper not found: {SALES_CACHE_QUERY_PATH}")

    spec = importlib.util.spec_from_file_location("query_sales_cache", SALES_CACHE_QUERY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.query_snapshot(business_date)
    result["host"] = os.environ.get("COMPUTERNAME")
    result["monitor_version"] = VERSION
    return result


def run_remote_sql_query(business_date):
    return query_sales_cache_snapshot(business_date)


def format_amount(value):
    return str(int(round(float(value or 0))))


def format_money(value):
    return f"{int(round(float(value or 0))):,}"


def format_decimal(value):
    return f"{float(value or 0):.2f}"


def net_amount(row, amount_key="amount", change_key="change_amount", net_key="net_amount"):
    if row.get(net_key) is not None:
        return row.get(net_key)
    return float(row.get(amount_key) or 0) - float(row.get(change_key) or 0)


def summary_revenue(summary):
    if summary.get("net_sales_amount") is not None:
        return summary.get("net_sales_amount")
    return float(summary.get("payment_amount") or 0) - float(summary.get("change_amount") or 0)


def make_sales_message(result):
    summary = result["summary"]
    latest_orders = result.get("latest_orders", [])
    now_hm = dt.datetime.now().strftime("%H:%M")
    revenue = format_amount(summary_revenue(summary))
    bowls = int(summary["bowl_count"])
    latest_payment_type = "-"
    latest_amount = "0"

    if latest_orders:
        latest_order = latest_orders[0]
        latest_payment_type = latest_order.get("payment_type") or "-"
        latest_amount = format_amount(net_amount(latest_order))

    return f"{bowls} ($ {revenue}) {latest_payment_type} ${latest_amount} @ {now_hm}"


def make_settlement_message(result):
    month = result.get("month_summary", {})
    if not month:
        return "月份結算資料不足"

    return "\n".join(
        [
            f"月份：{month.get('month')}，日數：{month.get('calendar_days', 0)}",
            f"累計碗數：{month.get('month_bowls', 0)}",
            f"日均碗數：{format_decimal(month.get('avg_bowls_calendar'))}",
            f"累計營業額：$ {format_money(month.get('month_revenue'))}",
            f"日均營業額：$ {format_money(month.get('avg_revenue_calendar'))}",
        ]
    )


def make_disconnect_message(result, reason):
    lines = ["連線中斷：無法連接售票機"]
    if reason:
        lines.append(f"原因：{reason}")
    if result:
        lines.append(f"目前結算：{make_sales_message(result)}")
        settlement = make_settlement_message(result)
        if settlement:
            lines.append(settlement)
    return "\n".join(lines)


def make_console_report(result):
    summary = result["summary"]
    month = result.get("month_summary", {})
    payments = result.get("payments", [])
    latest_orders = result.get("latest_orders", [])
    revenue = format_amount(summary_revenue(summary))
    payment_amount = format_amount(summary["payment_amount"])
    product_revenue = format_amount(summary["product_revenue"])
    change_amount = format_amount(summary["change_amount"])
    latest_order_time = summary.get("latest_order_time") or "-"
    if latest_order_time != "-":
        latest_order_time = str(latest_order_time)[11:19]

    lines = [
        f"{summary['business_date']}",
        "",
        f"單數: {summary['order_count']}",
        f"本日碗數: {int(summary['bowl_count'])}",
        f"實收金額: ${revenue}",
        f"付款金額: ${payment_amount}",
        f"品項金額: ${product_revenue}",
        f"找零金額: ${change_amount}",
        f"最後訂單: {latest_order_time}",
    ]

    if month:
        lines.extend(
            [
                "",
                f"月份: {month.get('month')}",
                f"本月累計碗數: {month.get('month_bowls', 0)}",
                f"本月日均碗數: {format_decimal(month.get('avg_bowls_calendar'))}",
                f"本月累計營業額: $ {format_money(month.get('month_revenue'))}",
                f"本月日均營業額: $ {format_money(month.get('avg_revenue_calendar'))}",
            ]
        )

    lines.extend(["", "付款方式:"])

    if payments:
        for payment in payments:
            lines.append(
                "- {kind}: ${amount} / {count} 筆".format(
                    kind=payment["payment_type"],
                    amount=format_amount(net_amount(payment)),
                    count=payment["count"],
                )
            )
    else:
        lines.append("- 無")

    if latest_orders:
        lines.extend(["", "最近訂單:"])
        for order in latest_orders[:5]:
            lines.append(
                "- {time} {display_id} {kind} ${amount}".format(
                    time=str(order["timestamp"])[11:16],
                    display_id=order["display_id"] or order["id"],
                    kind=order["payment_type"],
                    amount=format_amount(net_amount(order)),
                )
            )

    return "\n".join(lines)


def make_console_status_line(result):
    summary = result["summary"]
    latest_order_time = summary.get("latest_order_time") or "-"
    if latest_order_time != "-":
        latest_order_time = str(latest_order_time)[11:19]
    return (
        "狀態 ok date={date} orders={orders} bowls={bowls} revenue=${revenue} latest={latest}"
        .format(
            date=summary["business_date"],
            orders=summary["order_count"],
            bowls=int(summary["bowl_count"]),
            revenue=format_amount(summary_revenue(summary)),
            latest=latest_order_time,
        )
    )


def print_snapshot(result):
    console_log(make_console_status_line(result))


def write_heartbeat(status, result=None, reason=None, errors=0):
    summary = (result or {}).get("summary") or {}
    sync_state = (result or {}).get("sync_state") or {}
    atomic_write_json(
        HEARTBEAT_FILE,
        {
            "program": "RP5.0",
            "version": VERSION,
            "pid": os.getpid(),
            "status": status,
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "business_date": summary.get("business_date"),
            "order_count": summary.get("order_count"),
            "bowl_count": summary.get("bowl_count"),
            "net_sales_amount": summary_revenue(summary) if summary else None,
            "latest_order_time": summary.get("latest_order_time"),
            "sync_last_success_at": sync_state.get("last_success_at"),
            "sync_last_error_at": sync_state.get("last_error_at"),
            "reason": reason,
            "errors": errors,
        },
    )


def has_sales_changed(previous, result):
    if not previous:
        return False

    old_summary = previous.get("summary", {})
    new_summary = result.get("summary", {})
    keys = ("order_count", "bowl_count", "net_sales_amount", "payment_amount", "change_amount", "latest_order_time")
    return any(str(old_summary.get(key)) != str(new_summary.get(key)) for key in keys)


def monitor_once(
    state,
    business_date,
    notify_on_first_run=False,
    notify_enabled=True,
    save_enabled=True,
    print_message=False,
    force_print=False,
):
    result = run_remote_sql_query(business_date)

    if print_message:
        console_log(f"Discord preview: {make_sales_message(result)}")

    previous = state.get("last_result")
    current_date = result["summary"]["business_date"]
    previous_date = state.get("business_date")
    now_ts = time.time()

    if previous_date != current_date:
        previous = None
        state["business_date"] = current_date

    if previous is None:
        print_snapshot(result)

        state["last_result"] = result
        state["last_status_printed_at"] = now_ts
        if save_enabled:
            save_state(state)
        if notify_enabled and notify_on_first_run:
            message = make_sales_message(result)
            discord_notify(message)
            write_notification_to_file(message)
        return result

    if has_sales_changed(previous, result):
        message = make_sales_message(result)
        print_snapshot(result)
        if notify_enabled:
            discord_notify(message)
            write_notification_to_file(message)
        state["last_result"] = result
        state["last_status_printed_at"] = now_ts
        if save_enabled:
            save_state(state)
    else:
        last_status_printed_at = float(state.get("last_status_printed_at") or 0)
        if force_print or now_ts - last_status_printed_at >= STATUS_INTERVAL_SECONDS:
            console_log(make_console_status_line(result))
            state["last_status_printed_at"] = now_ts
        state["last_result"] = result
        if save_enabled:
            save_state(state)

    return result


def main():
    global _mutex
    parser = argparse.ArgumentParser(
        description=f"Monitor daily sales from the local SQLite sales cache. RP5.0 {VERSION}."
    )
    parser.add_argument("--once", action="store_true", help="Run one read and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Read and print only; do not notify or save state.")
    parser.add_argument("--print-message", action="store_true", help="Print the notification body for preview.")
    parser.add_argument("--date", default=None, help="Business date, yyyy-mm-dd. Defaults to today's date on each poll.")
    parser.add_argument("--notify-first-run", action="store_true", help="Send notification for the first snapshot.")
    parser.add_argument("--version", action="version", version=f"RP5.0 {VERSION}")
    args = parser.parse_args()

    if os.name == "nt":
        _mutex = WindowsMutex("Ramen_SuitRepository_Sales_Monitor_Unique_Mutex")

    console_log(f"RP5 monitor started. command={sys.executable} {' '.join(sys.argv)}")

    state = load_state()
    errors = 0
    first_loop = True
    disconnect_notified = False
    had_sync_problem = False
    last_result = state.get("last_result")
    last_sync_log_key = None
    last_runtime_error_key = None

    while True:
        try:
            result = monitor_once(
                state,
                args.date or dt.date.today().isoformat(),
                notify_on_first_run=args.notify_first_run,
                notify_enabled=not args.dry_run,
                save_enabled=not args.dry_run,
                print_message=args.print_message,
                force_print=first_loop or args.once or args.dry_run,
            )
            last_result = result
            sync_ok, sync_reason = assess_sync_health(result)
            if not sync_ok:
                errors += 1
                had_sync_problem = True
                write_heartbeat("sync_error", result=result, reason=sync_reason, errors=errors)
                if sync_reason != last_sync_log_key:
                    console_log(
                        "狀態 sync_error reason={reason} count={count}".format(
                            reason=sync_reason,
                            count=errors,
                        )
                    )
                    last_sync_log_key = sync_reason
                if errors >= CONNECT_ERROR_NOTIFY_THRESHOLD and not args.dry_run and not disconnect_notified:
                    discord_notify(make_disconnect_message(result, sync_reason))
                    console_log(f"通知 sent kiosk_offline count={errors} reason={sync_reason}")
                    disconnect_notified = True
                first_loop = False
                if args.once:
                    return 1
                time.sleep(CONNECT_RETRY_SECONDS if disconnect_notified else POLL_INTERVAL_SECONDS)
                continue

            if had_sync_problem:
                if disconnect_notified and not args.dry_run:
                    discord_notify("售票機同步恢復")
                console_log("狀態 recovered 售票機同步恢復")
                _limited_console_logs.clear()
            disconnect_notified = False
            had_sync_problem = False
            errors = 0
            last_sync_log_key = None
            last_runtime_error_key = None
            write_heartbeat("ok", result=result)
            first_loop = False
        except Exception as exc:
            errors += 1
            had_sync_problem = True
            error = summarize_error(exc)
            write_heartbeat("error", result=last_result, reason=error, errors=errors)
            if error != last_runtime_error_key:
                console_log(
                    "狀態 error count={count} reason={error}".format(
                        count=errors,
                        error=error,
                    )
                )
                last_runtime_error_key = error
            if errors < CONNECT_ERROR_NOTIFY_THRESHOLD:
                if args.once:
                    return 1
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            if errors >= CONNECT_ERROR_NOTIFY_THRESHOLD and not args.dry_run and not disconnect_notified:
                discord_notify(make_disconnect_message(last_result, error))
                console_log(f"通知 sent kiosk_offline count={errors} reason={error}")
                disconnect_notified = True
                time.sleep(CONNECT_RETRY_SECONDS)
                continue

        if args.once:
            return 0 if errors == 0 else 1

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
