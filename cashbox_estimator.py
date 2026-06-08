import argparse
import datetime as dt
import json
import msvcrt
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "finance_cache"
DEFAULT_SALES_DB = r"C:\6KAweb\data\sales_cache.sqlite"
DEFAULT_CASH_DB = r"C:\6KAweb\data\finance_cache\cash_finance_cache.sqlite"
DEFAULT_LATEST_JSON = r"C:\6KAweb\data\finance_cache\cashbox_estimate_latest.json"
DEFAULT_RP_ENV_PATH = r"C:\RP\.env"
STATE_PATH = DATA_DIR / "cashbox_estimator_state.json"
HEARTBEAT_PATH = DATA_DIR / "cashbox_estimator_heartbeat.json"
LOCK_PATH = DATA_DIR / "cashbox_estimator.lock"
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "cashbox-estimator.log"
LOG_MAX_BYTES = int(os.getenv("CASHBOX_ESTIMATOR_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("CASHBOX_ESTIMATOR_LOG_BACKUP_COUNT", "2"))

DENOMINATIONS = (1, 5, 10, 50, 100, 200, 500, 1000, 2000)
LOCK_HANDLE = None
LAST_STATUS_LOG_AT = 0
LAST_STATUS_LOG_KEY = None
STATUS_LOG_INTERVAL = int(os.getenv("CASHBOX_ESTIMATOR_STATUS_LOG_INTERVAL", "600"))


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def as_int(value):
    return int(round(float(value or 0)))


def money(value):
    return f"{as_int(value):,}"


def force_utf8_stdio():
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def load_dotenv(path):
    path = Path(path)
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


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


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


def write_log(message):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{now_iso()}] {message}"
    print(line, flush=True)
    rotate_log_if_needed()
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def maybe_log_status(report):
    global LAST_STATUS_LOG_AT, LAST_STATUS_LOG_KEY
    now_ts = time.time()
    estimate = (report or {}).get("estimate") or {}
    clear_check = estimate.get("cashbox_clear_check") or {}
    sales = (report or {}).get("cash_sales") or {}
    machine = (report or {}).get("cash_machine") or {}
    operation = (report or {}).get("cash_operations") or {}
    key = (
        report.get("business_date"),
        clear_check.get("event_id"),
        clear_check.get("mismatch"),
        clear_check.get("difference"),
        operation.get("last_cash_operation_id"),
    )
    if key == LAST_STATUS_LOG_KEY:
        return
    LAST_STATUS_LOG_KEY = key
    LAST_STATUS_LOG_AT = now_ts
    clear_status = "mismatch" if clear_check.get("mismatch") else "ok"
    write_log(
        "狀態 ok date={date} cash_net={cash_net} clear={clear_status} diff={diff} usable={usable} latest_operation_id={operation_id}".format(
            date=report.get("business_date"),
            cash_net=money(sales.get("net_amount")),
            clear_status=clear_status,
            diff=money(clear_check.get("difference")),
            usable=money(machine.get("usable_change_total")),
            operation_id=operation.get("last_cash_operation_id"),
        )
    )


def atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def read_json_file(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


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


def event_date(value):
    if not value:
        return None
    return str(value).replace("T", " ")[:10]


def event_time_key(value):
    if not value:
        return ""
    return str(value).replace("T", " ")


def current_time_key():
    return dt.datetime.now().isoformat(sep=" ", timespec="seconds")


def format_money(value):
    return f"${money(value)}"


def send_rp5_discord(message):
    webhook = (
        os.getenv("CASHBOX_ESTIMATOR_DISCORD_WEBHOOK_URL")
        or os.getenv("RP_DISCORD_WEBHOOK")
        or os.getenv("DISCORD_WEBHOOK_URL")
        or os.getenv("DC_WEBHOOK_URL")
    )
    if not webhook:
        raise RuntimeError("missing CASHBOX_ESTIMATOR_DISCORD_WEBHOOK_URL / RP_DISCORD_WEBHOOK")
    payload = json.dumps({"content": message}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "6ka-cashbox-estimator"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status >= 300:
            raise RuntimeError(f"Discord HTTP {response.status}")


def write_heartbeat(status, args=None, report=None, error=None):
    args = args or argparse.Namespace()
    estimate = (report or {}).get("estimate") or {}
    cash_sales = (report or {}).get("cash_sales") or {}
    cash_machine = (report or {}).get("cash_machine") or {}
    clear_check = estimate.get("cashbox_clear_check") or {}
    cash_operations = (report or {}).get("cash_operations") or {}
    payload = {
        "ok": status != "error",
        "status": status,
        "updated_at": now_iso(),
        "pid": os.getpid(),
        "business_date": getattr(args, "date", None),
        "sales_db": getattr(args, "sales_db", None),
        "cash_db": getattr(args, "cash_db", None),
        "output_json": getattr(args, "output_json", None),
        "watch": bool(getattr(args, "watch", False)),
        "interval": getattr(args, "interval", None),
        "latest_generated_at": (report or {}).get("generated_at"),
        "cash_sales_net": cash_sales.get("net_amount"),
        "today_cashbox_flow_amount": cash_machine.get("today_cashbox_flow_amount"),
        "usable_change_total": cash_machine.get("usable_change_total"),
        "today_usable_net_change": cash_machine.get("today_usable_net_change"),
        "pos_vs_cash_db_variance": estimate.get("pos_vs_cash_db_variance"),
        "cashbox_clear_mismatch": bool(clear_check.get("mismatch")),
        "cashbox_clear_event_id": clear_check.get("event_id"),
        "cashbox_clear_difference": clear_check.get("difference"),
        "cash_operation_status": cash_operations.get("status"),
        "cash_operation_notified_count": len(cash_operations.get("notified") or []),
        "last_cash_operation_id": cash_operations.get("last_cash_operation_id"),
        "error": str(error)[:500] if error else None,
    }
    atomic_write_json(HEARTBEAT_PATH, payload)
    return payload


def get_cash_sales(sales_db, business_date):
    conn = connect(sales_db)
    rows = conn.execute(
        """
        select
            o.id as order_id,
            o.display_id,
            o.order_time,
            p.guid as payment_guid,
            p.amount,
            p.change_amount,
            p.amount - p.change_amount as net_amount
        from order_payments p
        join orders o
          on o.guid = p.order_guid
         and o.store_guid = p.store_guid
        where o.business_date = ?
          and p.payment_type_id = 'Cash'
        order by o.order_time, o.id
        """,
        (business_date,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_cash_sales_between(sales_db, start_exclusive, end_inclusive):
    conn = connect(sales_db)
    rows = conn.execute(
        """
        select
            o.id as order_id,
            o.display_id,
            o.order_time,
            p.guid as payment_guid,
            p.amount,
            p.change_amount,
            p.amount - p.change_amount as net_amount
        from order_payments p
        join orders o
          on o.guid = p.order_guid
         and o.store_guid = p.store_guid
        where p.payment_type_id = 'Cash'
          and o.order_time > ?
          and o.order_time <= ?
        order by o.order_time, o.id
        """,
        (event_time_key(start_exclusive), event_time_key(end_inclusive)),
    ).fetchall()
    return [dict(row) for row in rows]


def summarize_cash_sales(rows):
    return {
        "order_count": len(rows),
        "gross_amount": sum(as_int(row["amount"]) for row in rows),
        "change_amount": sum(as_int(row["change_amount"]) for row in rows),
        "net_amount": sum(as_int(row["net_amount"]) for row in rows),
        "first_order_time": rows[0]["order_time"] if rows else None,
        "last_order_time": rows[-1]["order_time"] if rows else None,
    }


def get_cash_interval_summary(cash_db, start_exclusive, end_inclusive):
    conn = connect(cash_db)
    rows = conn.execute(
        """
        select type, target, flow_mode, value,
               count(*) as rows,
               sum(total_amount) as total_amount,
               sum(quantity) as quantity,
               min(timestamp) as first_time,
               max(timestamp) as latest_time
        from cash_records
        where timestamp > ?
          and timestamp <= ?
        group by type, target, flow_mode, value
        order by target, flow_mode, type, value
        """,
        (event_time_key(start_exclusive), event_time_key(end_inclusive)),
    ).fetchall()
    records = [dict(row) for row in rows]
    target0_all_net = sum(as_int(row["total_amount"]) for row in records if as_int(row["target"]) == 0)
    target0_manual_net = sum(
        as_int(row["total_amount"])
        for row in records
        if as_int(row["target"]) == 0 and as_int(row["flow_mode"]) in (3, 4)
    )
    target0_transaction_net = target0_all_net - target0_manual_net
    cashbox_flow = sum(
        as_int(row["total_amount"])
        for row in records
        if as_int(row["target"]) == 1 and as_int(row["flow_mode"]) != 4
    )
    return {
        "start_exclusive": event_time_key(start_exclusive),
        "end_inclusive": event_time_key(end_inclusive),
        "target0_net_change": target0_transaction_net,
        "target0_transaction_net_change": target0_transaction_net,
        "target0_manual_net_change": target0_manual_net,
        "target0_all_net_change": target0_all_net,
        "cashbox_flow_amount": cashbox_flow,
        "records": records,
    }


def is_manual_cash_operation(row):
    target = as_int(row["target"])
    flow_mode = as_int(row["flow_mode"])
    return (target == 1 and flow_mode == 4) or (target == 0 and flow_mode in (3, 4))


def classify_cash_operation(target, flow_mode, cash_type, total_amount):
    if target == 1 and flow_mode == 4:
        return "清鈔"
    if target != 0 or total_amount == 0:
        return None
    kind = "幣" if cash_type == 0 else "鈔"
    verb = "補" if total_amount > 0 else "退"
    return f"{verb}{kind}"


def format_breakdown(rows):
    parts = []
    for row in sorted(rows, key=lambda item: as_int(item["value"])):
        quantity = as_int(row["quantity"])
        total = as_int(row["total_amount"])
        if quantity == 0 and total == 0:
            continue
        parts.append(f"${as_int(row['value'])}x{abs(quantity)}")
    return "、".join(parts)


def build_cash_operation(rows):
    first = rows[0]
    target = as_int(first["target"])
    flow_mode = as_int(first["flow_mode"])
    cash_type = as_int(first["type"])
    total = sum(as_int(row["total_amount"]) for row in rows)
    action = classify_cash_operation(target, flow_mode, cash_type, total)
    if not action:
        return None
    return {
        "operation_id": max(as_int(row["id"]) for row in rows),
        "row_ids": [as_int(row["id"]) for row in rows],
        "timestamp": first["timestamp"],
        "action": action,
        "amount": abs(total),
        "signed_amount": total,
        "type": cash_type,
        "target": target,
        "flow_mode": flow_mode,
        "breakdown": format_breakdown(rows),
        "rows": [dict(row) for row in rows],
    }


def get_latest_cash_operation_id(cash_db):
    conn = connect(cash_db)
    row = conn.execute(
        """
        select max(id) as latest_id
        from cash_records
        where (target = 1 and flow_mode = 4)
           or (target = 0 and flow_mode in (3, 4))
        """
    ).fetchone()
    return as_int(row["latest_id"]) if row and row["latest_id"] is not None else 0


def get_cash_operations_after(cash_db, after_id, limit=200):
    conn = connect(cash_db)
    rows = conn.execute(
        """
        select id,total_amount,quantity,value,type,target,flow_mode,timestamp
        from cash_records
        where id > ?
          and ((target = 1 and flow_mode = 4)
            or (target = 0 and flow_mode in (3, 4)))
        order by id
        limit ?
        """,
        (after_id, limit),
    ).fetchall()
    groups = {}
    for row in rows:
        if not is_manual_cash_operation(row):
            continue
        key = (row["timestamp"], as_int(row["type"]), as_int(row["target"]), as_int(row["flow_mode"]))
        groups.setdefault(key, []).append(row)
    operations = []
    for group_rows in groups.values():
        operation = build_cash_operation(group_rows)
        if operation:
            operations.append(operation)
    return sorted(operations, key=lambda item: item["operation_id"])


def get_cash_summary(cash_db, business_date):
    conn = connect(cash_db)

    usable_rows = conn.execute(
        f"""
        select type, target, value,
               sum(total_amount) as total_amount,
               sum(quantity) as quantity,
               max(timestamp) as latest_time
        from cash_records
        where target = 0
          and value in ({",".join("?" for _ in DENOMINATIONS)})
        group by type, target, value
        order by value
        """,
        DENOMINATIONS,
    ).fetchall()

    today_flow_rows = conn.execute(
        """
        select type, target, flow_mode, value,
               count(*) as rows,
               sum(total_amount) as total_amount,
               sum(quantity) as quantity,
               min(timestamp) as first_time,
               max(timestamp) as latest_time
        from cash_records
        where timestamp >= ?
          and timestamp < ?
        group by type, target, flow_mode, value
        order by target, flow_mode, type, value
        """,
        (business_date, next_date(business_date)),
    ).fetchall()

    cashbox_today_rows = conn.execute(
        """
        select type, target, flow_mode, value,
               count(*) as rows,
               sum(total_amount) as total_amount,
               sum(quantity) as quantity,
               min(timestamp) as first_time,
               max(timestamp) as latest_time
        from cash_records
        where timestamp >= ?
          and timestamp < ?
          and target = 1
          and flow_mode <> 4
        group by type, target, flow_mode, value
        order by type, value
        """,
        (business_date, next_date(business_date)),
    ).fetchall()

    usable_today_rows = conn.execute(
        """
        select type, target, flow_mode, value,
               count(*) as rows,
               sum(total_amount) as total_amount,
               sum(quantity) as quantity,
               min(timestamp) as first_time,
               max(timestamp) as latest_time
        from cash_records
        where timestamp >= ?
          and timestamp < ?
          and target = 0
        group by type, target, flow_mode, value
        order by type, value, flow_mode
        """,
        (business_date, next_date(business_date)),
    ).fetchall()

    cashbox_event_rows = conn.execute(
        """
        select id, total_amount, quantity, value, type, target, flow_mode, timestamp
        from cash_records
        where target = 1
          and flow_mode = 4
        order by id desc
        limit 2
        """
    ).fetchall()
    latest_cashbox_event = cashbox_event_rows[0] if cashbox_event_rows else None
    previous_cashbox_event = cashbox_event_rows[1] if len(cashbox_event_rows) > 1 else None

    latest_record = conn.execute(
        """
        select id, timestamp
        from cash_records
        order by id desc
        limit 1
        """
    ).fetchone()

    usable = [dict(row) for row in usable_rows]
    today_flows = [dict(row) for row in today_flow_rows]
    cashbox_today = [dict(row) for row in cashbox_today_rows]
    usable_today = [dict(row) for row in usable_today_rows]

    return {
        "usable_by_denomination": usable,
        "usable_total_amount": sum(as_int(row["total_amount"]) for row in usable),
        "today_flows": today_flows,
        "today_cashbox_flows": cashbox_today,
        "today_cashbox_flow_amount": sum(as_int(row["total_amount"]) for row in cashbox_today),
        "today_usable_flows": usable_today,
        "today_usable_net_change": sum(as_int(row["total_amount"]) for row in usable_today),
        "latest_cashbox_event": dict(latest_cashbox_event) if latest_cashbox_event else None,
        "previous_cashbox_event": dict(previous_cashbox_event) if previous_cashbox_event else None,
        "latest_record": dict(latest_record) if latest_record else None,
    }


def next_date(date_text):
    return (dt.date.fromisoformat(date_text) + dt.timedelta(days=1)).isoformat()


def build_cashbox_clear_check(sales_db, cash_db, previous_event, current_event, tolerance):
    if not current_event:
        return {
            "applies": False,
            "reason": "no_cashbox_clear_event",
            "tolerance": tolerance,
        }

    if not previous_event:
        return {
            "applies": False,
            "reason": "missing_previous_cashbox_clear_event",
            "event_id": current_event.get("id"),
            "event_time": current_event.get("timestamp"),
            "tolerance": tolerance,
        }

    start_time = previous_event.get("timestamp")
    end_time = current_event.get("timestamp")
    interval_sales = get_cash_sales_between(sales_db, start_time, end_time)
    sales_summary = summarize_cash_sales(interval_sales)
    cash_interval = get_cash_interval_summary(cash_db, start_time, end_time)
    cleared_amount = abs(as_int(current_event.get("total_amount")))
    estimated_amount = sales_summary["net_amount"] - cash_interval["target0_transaction_net_change"]
    difference = cleared_amount - estimated_amount
    mismatch = abs(difference) > tolerance
    return {
        "applies": True,
        "event_id": current_event.get("id"),
        "event_time": current_event.get("timestamp"),
        "previous_event_id": previous_event.get("id"),
        "previous_event_time": previous_event.get("timestamp"),
        "cleared_amount": cleared_amount,
        "estimated_amount": estimated_amount,
        "difference": difference,
        "tolerance": tolerance,
        "mismatch": mismatch,
        "cash_sales_net_since_previous_clear": sales_summary["net_amount"],
        "usable_net_change_since_previous_clear": cash_interval["target0_transaction_net_change"],
        "transaction_usable_net_change_since_previous_clear": cash_interval["target0_transaction_net_change"],
        "manual_usable_net_change_since_previous_clear": cash_interval["target0_manual_net_change"],
        "total_usable_net_change_since_previous_clear": cash_interval["target0_all_net_change"],
        "cashbox_flow_since_previous_clear": cash_interval["cashbox_flow_amount"],
        "cash_order_count_since_previous_clear": sales_summary["order_count"],
    }


def default_state():
    return {
        "baseline_initialized_at": None,
        "seen_clear_events": {},
        "notified_clear_mismatches": {},
        "resolved_clear_mismatches": {},
        "cash_operation_baseline_initialized_at": None,
        "last_cash_operation_id": 0,
        "notified_cash_operations": {},
    }


def load_state():
    state = read_json_file(STATE_PATH, default_state())
    if not isinstance(state, dict):
        state = default_state()
    state.setdefault("baseline_initialized_at", None)
    state.setdefault("seen_clear_events", {})
    state.setdefault("notified_clear_mismatches", {})
    state.setdefault("resolved_clear_mismatches", {})
    state.setdefault("cash_operation_baseline_initialized_at", None)
    state.setdefault("last_cash_operation_id", 0)
    state.setdefault("notified_cash_operations", {})
    return state


def save_state(state):
    atomic_write_json(STATE_PATH, state)


def format_clear_mismatch_message(check):
    return "\n".join([
        "# 錢箱清鈔不符",
        f"清鈔：{format_money(check['cleared_amount'])}",
        f"推算：{format_money(check['estimated_amount'])}",
        f"差異：{format_money(check['difference'])}",
        f"時間：{check.get('event_time') or '-'}",
    ])


def format_cash_operation_message(operation):
    lines = [
        f"# 現金操作：{operation['action']} {format_money(operation['amount'])}",
        f"時間：{operation.get('timestamp') or '-'}",
    ]
    if operation.get("breakdown"):
        lines.append(f"明細：{operation['breakdown']}")
    return "\n".join(lines)


def maybe_notify_cash_operations(cash_db, notify_enabled=True):
    state = load_state()
    if not state.get("cash_operation_baseline_initialized_at"):
        latest_id = get_latest_cash_operation_id(cash_db)
        state["cash_operation_baseline_initialized_at"] = now_iso()
        state["last_cash_operation_id"] = latest_id
        save_state(state)
        return {
            "status": "baseline_initialized",
            "notified": [],
            "last_cash_operation_id": latest_id,
        }

    last_id = as_int(state.get("last_cash_operation_id"))
    operations = get_cash_operations_after(cash_db, last_id)
    notified = []
    status = "no_new_operation"
    max_seen_id = last_id
    for operation in operations:
        operation_id = str(operation["operation_id"])
        if operation_id in state["notified_cash_operations"]:
            max_seen_id = max(max_seen_id, operation["operation_id"])
            continue
        if notify_enabled:
            message = format_cash_operation_message(operation)
            send_rp5_discord(message)
            state["notified_cash_operations"][operation_id] = {
                "sent_at": now_iso(),
                "message": message,
                "operation": operation,
            }
            notified.append(operation)
            status = "notified"
        else:
            status = "notify_disabled"
        max_seen_id = max(max_seen_id, operation["operation_id"])

    state["last_cash_operation_id"] = max_seen_id
    save_state(state)
    return {
        "status": status,
        "notified": notified,
        "last_cash_operation_id": max_seen_id,
    }


def maybe_notify_clear_mismatch(report, notify_enabled=True):
    check = ((report or {}).get("estimate") or {}).get("cashbox_clear_check") or {}
    state = load_state()
    if not state.get("baseline_initialized_at"):
        state["baseline_initialized_at"] = now_iso()
        event_id = check.get("event_id")
        if event_id is not None:
            state["seen_clear_events"][str(event_id)] = {
                "event_time": check.get("event_time"),
                "baseline": True,
                "mismatch": bool(check.get("mismatch")),
                "recorded_at": now_iso(),
            }
        save_state(state)
        return "baseline_initialized"

    if not check.get("applies"):
        return "no_mismatch"

    if not check.get("mismatch"):
        event_id = check.get("event_id")
        if event_id is not None:
            key = str(event_id)
            now = now_iso()
            previous_notification = state["notified_clear_mismatches"].pop(key, None)
            if previous_notification is not None:
                state["resolved_clear_mismatches"][key] = {
                    **previous_notification,
                    "resolved_at": now,
                    "resolved_estimated_amount": check.get("estimated_amount"),
                    "resolved_difference": check.get("difference"),
                }
            state["seen_clear_events"][key] = {
                "event_time": check.get("event_time"),
                "mismatch": False,
                "cleared_amount": check.get("cleared_amount"),
                "estimated_amount": check.get("estimated_amount"),
                "difference": check.get("difference"),
                "recorded_at": now,
            }
            save_state(state)
        return "no_mismatch"

    event_id = check.get("event_id")
    if event_id is None:
        return "missing_event_id"
    key = str(event_id)
    baseline_at = state.get("baseline_initialized_at")
    event_time = check.get("event_time")
    if baseline_at and event_time and event_time_key(event_time) <= event_time_key(baseline_at):
        state["seen_clear_events"].setdefault(key, {
            "event_time": event_time,
            "mismatch": bool(check.get("mismatch")),
            "pre_baseline": True,
            "recorded_at": now_iso(),
        })
        save_state(state)
        return "pre_baseline_event"
    state["seen_clear_events"].setdefault(key, {
        "event_time": event_time,
        "mismatch": True,
        "recorded_at": now_iso(),
    })
    if key in state["notified_clear_mismatches"]:
        save_state(state)
        return "already_notified"
    if not notify_enabled:
        save_state(state)
        return "notify_disabled"

    message = format_clear_mismatch_message(check)
    send_rp5_discord(message)
    state["notified_clear_mismatches"][key] = {
        "event_time": check.get("event_time"),
        "cleared_amount": check.get("cleared_amount"),
        "estimated_amount": check.get("estimated_amount"),
        "difference": check.get("difference"),
        "sent_at": now_iso(),
        "message": message,
    }
    save_state(state)
    return "notified"


def build_estimate(sales_db, cash_db, business_date, clear_diff_tolerance=0):
    cash_sales = get_cash_sales(sales_db, business_date)
    cash_summary = get_cash_summary(cash_db, business_date)

    daily_sales = summarize_cash_sales(cash_sales)
    latest_clear = cash_summary["latest_cashbox_event"]
    previous_clear = cash_summary["previous_cashbox_event"]
    interval_start = latest_clear["timestamp"] if latest_clear else f"{business_date} 00:00:00"
    interval_end = current_time_key()
    cash_sales_since_clear = get_cash_sales_between(sales_db, interval_start, interval_end)
    sales_since_clear = summarize_cash_sales(cash_sales_since_clear)
    cash_interval = get_cash_interval_summary(cash_db, interval_start, interval_end)
    cashbox_flow = cash_interval["cashbox_flow_amount"]
    usable_net_change = cash_interval["target0_transaction_net_change"]
    manual_usable_net_change = cash_interval["target0_manual_net_change"]
    total_usable_net_change = cash_interval["target0_all_net_change"]
    usable_total = cash_summary["usable_total_amount"]
    machine_accounted_cash = cashbox_flow + usable_net_change
    cashbox_estimated_amount = sales_since_clear["net_amount"] - usable_net_change
    cashbox_unposted_difference = cashbox_estimated_amount - cashbox_flow
    cashbox_clear_check = build_cashbox_clear_check(
        sales_db,
        cash_db,
        previous_clear,
        latest_clear,
        clear_diff_tolerance,
    )

    return {
        "business_date": business_date,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": {
            "sales_db": sales_db,
            "cash_db": cash_db,
        },
        "cash_sales": {
            "order_count": daily_sales["order_count"],
            "gross_amount": daily_sales["gross_amount"],
            "change_amount": daily_sales["change_amount"],
            "net_amount": daily_sales["net_amount"],
            "first_order_time": daily_sales["first_order_time"],
            "last_order_time": daily_sales["last_order_time"],
        },
        "cash_machine": {
            "usable_change_total": usable_total,
            "usable_by_denomination": cash_summary["usable_by_denomination"],
            "today_cashbox_flow_amount": cash_summary["today_cashbox_flow_amount"],
            "today_cashbox_flows": cash_summary["today_cashbox_flows"],
            "today_usable_net_change": cash_summary["today_usable_net_change"],
            "today_usable_flows": cash_summary["today_usable_flows"],
            "today_all_flows": cash_summary["today_flows"],
            "latest_cashbox_settlement_event": cash_summary["latest_cashbox_event"],
            "previous_cashbox_settlement_event": cash_summary["previous_cashbox_event"],
            "cashbox_interval_since_clear": {
                "start_exclusive": cash_interval["start_exclusive"],
                "end_inclusive": cash_interval["end_inclusive"],
                "cash_sales": sales_since_clear,
                "cash_records": {
                    "target0_net_change": usable_net_change,
                    "target0_transaction_net_change": usable_net_change,
                    "target0_manual_net_change": manual_usable_net_change,
                    "target0_all_net_change": total_usable_net_change,
                    "cashbox_flow_amount": cashbox_flow,
                },
            },
            "latest_cash_record": cash_summary["latest_record"],
        },
        "estimate": {
            "cashbox_amount_confirmed_by_cash_db_today": cash_summary["today_cashbox_flow_amount"],
            "cashbox_confirmed_amount_since_clear": cashbox_flow,
            "cashbox_estimated_amount": cashbox_estimated_amount,
            "cashbox_unposted_difference": cashbox_unposted_difference,
            "cash_sales_net_since_clear": sales_since_clear["net_amount"],
            "usable_change_net_change_today": cash_summary["today_usable_net_change"],
            "usable_change_net_change_since_clear": usable_net_change,
            "transaction_usable_change_net_change_since_clear": usable_net_change,
            "manual_usable_change_net_change_since_clear": manual_usable_net_change,
            "total_usable_change_net_change_since_clear": total_usable_net_change,
            "machine_accounted_cash_since_clear": machine_accounted_cash,
            "cash_sales_minus_cashbox_flow": sales_since_clear["net_amount"] - cashbox_flow,
            "pos_vs_cash_db_variance": cashbox_unposted_difference,
            "cash_sales_net_plus_current_usable_change": sales_since_clear["net_amount"] + usable_total,
            "cashbox_clear_check": cashbox_clear_check,
            "explanation": (
                "The current cashbox estimate is anchored after the latest target=1 flow_mode=4 cashbox clear event. "
                "For each interval, POS cash net ~= transaction target=0 net change + target=1 non-settlement flow. "
                "Manual target=0 flow_mode=3/4 operations are tracked separately and excluded from cashbox estimation. "
                "target=1 flow_mode=4 is an out-cash/settlement event, not a live cashbox balance. "
                "When a new clear event appears, its cleared amount is compared against the estimate since the previous clear."
            ),
        },
    }


def print_human(report):
    sales = report["cash_sales"]
    machine = report["cash_machine"]
    estimate = report["estimate"]
    event = machine["latest_cashbox_settlement_event"] or {}
    interval = machine.get("cashbox_interval_since_clear") or {}
    interval_sales = interval.get("cash_sales") or {}
    latest = machine["latest_cash_record"] or {}

    print(f"6KA 現金錢箱推估 - {report['business_date']}")
    print(f"產生時間: {report['generated_at']}")
    print("")
    print("[今日現金營業額 / POS 口徑]")
    print(f"  現金單數: {sales['order_count']}")
    print(f"  現金收款 gross: {money(sales['gross_amount'])}")
    print(f"  找零/沖抵: {money(sales['change_amount'])}")
    print(f"  現金淨收 net: {money(sales['net_amount'])}")
    print(f"  第一筆/最後一筆: {sales['first_order_time']} / {sales['last_order_time']}")
    print("")
    print("[現金機 / cash DB 口徑]")
    print(f"  目前可用找零槽總額 target=0: {money(machine['usable_change_total'])}")
    print(f"  今日找零槽淨變化 target=0: {money(machine['today_usable_net_change'])}")
    print(f"  今日已進錢箱 target=1, flow_mode<>4: {money(machine['today_cashbox_flow_amount'])}")
    print(f"  最新 cash record: id={latest.get('id')} time={latest.get('timestamp')}")
    print(
        "  最新錢箱結帳事件 target=1, flow_mode=4: "
        f"id={event.get('id')} time={event.get('timestamp')} value={money(event.get('value') or 0)}"
    )
    print("")
    print("[推估]")
    print(f"  推算區間: {interval.get('start_exclusive')} -> {interval.get('end_inclusive')}")
    print(f"  前次清鈔後現金淨收: {money(interval_sales.get('net_amount'))}")
    print(f"  前次清鈔後交易找零槽淨變化: {money(estimate['usable_change_net_change_since_clear'])}")
    print(f"  前次清鈔後人工補退幣淨變化: {money(estimate['manual_usable_change_net_change_since_clear'])}")
    print(f"  錢箱推算: {money(estimate['cashbox_estimated_amount'])}")
    print(f"  DB 已確認進錢箱: {money(estimate['cashbox_confirmed_amount_since_clear'])}")
    print(f"  未入差額: {money(estimate['cashbox_unposted_difference'])}")
    print(f"  現金淨收 + 目前可用找零槽(高側參考): {money(estimate['cash_sales_net_plus_current_usable_change'])}")
    clear_check = estimate.get("cashbox_clear_check") or {}
    if clear_check.get("applies"):
        status = "不符" if clear_check.get("mismatch") else "相符"
        print(
            "  清鈔檢查: "
            f"{status} 清鈔={money(clear_check.get('cleared_amount'))} "
            f"推算={money(clear_check.get('estimated_amount'))} "
            f"差異={money(clear_check.get('difference'))}"
        )
    print("")
    print("[找零槽 denominations]")
    for row in machine["usable_by_denomination"]:
        print(
            f"  {row['value']:>4}: qty={as_int(row['quantity']):>4} "
            f"amount={money(row['total_amount']):>8} latest={row['latest_time']}"
        )
    print("")
    print("注意: 這是營運推估，不是實體盤點。差額若能被找零槽淨變化解釋，通常不是少錢。")


def write_json_report(path, report):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_screen():
    print("\033[2J\033[H", end="", flush=True)


def render_once(args):
    report = build_estimate(args.sales_db, args.cash_db, args.date, args.clear_diff_tolerance)
    try:
        operation_status = maybe_notify_cash_operations(args.cash_db, notify_enabled=args.notify_cash_operations)
    except Exception as exc:
        operation_status = {
            "status": f"notify_error: {type(exc).__name__}: {exc}",
            "notified": [],
        }
        write_log(operation_status["status"])
    report["cash_operations"] = operation_status
    if operation_status.get("notified"):
        latest = operation_status["notified"][-1]
        write_log(
            "現金操作通知 count={count} latest={action} amount={amount} id={operation_id}".format(
                count=len(operation_status["notified"]),
                action=latest.get("action"),
                amount=money(latest.get("amount")),
                operation_id=latest.get("operation_id"),
            )
        )
    try:
        notify_status = maybe_notify_clear_mismatch(report, notify_enabled=args.notify_clear_mismatch)
    except Exception as exc:
        notify_status = f"notify_error: {type(exc).__name__}: {exc}"
        write_log(notify_status)
    report["estimate"]["cashbox_clear_check"]["notification_status"] = notify_status
    if notify_status == "notified":
        check = report["estimate"]["cashbox_clear_check"]
        write_log(
            "清鈔不符通知 event_id={event_id} cleared={cleared} estimated={estimated} diff={diff}".format(
                event_id=check.get("event_id"),
                cleared=money(check.get("cleared_amount")),
                estimated=money(check.get("estimated_amount")),
                diff=money(check.get("difference")),
            )
        )

    if args.output_json:
        write_json_report(args.output_json, report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)

    return report


def main():
    force_utf8_stdio()
    load_dotenv(BASE_DIR / ".env")
    load_dotenv(os.getenv("RP_ENV_PATH", DEFAULT_RP_ENV_PATH))
    parser = argparse.ArgumentParser(description="Estimate current cashbox amount from sales and cash finance DBs.")
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="Business date, YYYY-MM-DD. Default: today.")
    parser.add_argument("--sales-db", default=DEFAULT_SALES_DB)
    parser.add_argument("--cash-db", default=DEFAULT_CASH_DB)
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    parser.add_argument("--output-json", help="Write JSON report to this file.")
    parser.add_argument("--watch", action="store_true", help="Continuously refresh the report.")
    parser.add_argument("--interval", type=int, default=30, help="Refresh interval in seconds for --watch. Default: 30.")
    parser.add_argument(
        "--clear-diff-tolerance",
        type=int,
        default=int(os.getenv("CASHBOX_CLEAR_DIFF_TOLERANCE", "0")),
        help="Allowed difference between cleared cashbox amount and estimated amount. Default: 0.",
    )
    parser.add_argument(
        "--no-notify-clear-mismatch",
        dest="notify_clear_mismatch",
        action="store_false",
        help="Do not notify RP Discord when a new cashbox clear mismatch is detected.",
    )
    parser.add_argument(
        "--no-notify-cash-operations",
        dest="notify_cash_operations",
        action="store_false",
        help="Do not notify RP Discord for new manual cash operations.",
    )
    parser.set_defaults(notify_clear_mismatch=True)
    parser.set_defaults(notify_cash_operations=True)
    args = parser.parse_args()

    if not acquire_single_instance_lock():
        write_log("another cashbox estimator instance is already running; exiting")
        return

    if not args.watch:
        try:
            report = render_once(args)
            write_heartbeat("ok", args=args, report=report)
        except Exception as exc:
            write_heartbeat("error", args=args, error=exc)
            raise
        return

    if not args.output_json:
        args.output_json = DEFAULT_LATEST_JSON

    write_heartbeat("starting", args=args)
    write_log(f"started watch mode interval={args.interval}s output={args.output_json}")

    try:
        while True:
            if sys.stdout.isatty():
                clear_screen()
            print(f"持續監控模式，每 {args.interval} 秒更新一次。按 Ctrl+C 停止。")
            print(f"最新 JSON: {args.output_json}")
            print("=" * 72)
            try:
                report = render_once(args)
                write_heartbeat("ok", args=args, report=report)
                maybe_log_status(report)
            except Exception as exc:
                write_heartbeat("error", args=args, error=exc)
                write_log(f"render failed: {type(exc).__name__}: {exc}")
                print(f"[錯誤] {type(exc).__name__}: {exc}")
            time.sleep(max(5, args.interval))
    except KeyboardInterrupt:
        write_heartbeat("stopped", args=args)
        write_log("stopped by keyboard interrupt")
        print("")
        print("已停止持續監控。")


if __name__ == "__main__":
    main()
