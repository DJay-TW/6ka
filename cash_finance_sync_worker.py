import argparse
import datetime as dt
import hashlib
import json
import msvcrt
import os
import shutil
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import cash_finance_audit
import cash_diff_cloudflare_push


BASE_DIR = Path(__file__).resolve().parent
INTERVAL_SECONDS = int(os.getenv("CASH_FINANCE_SYNC_INTERVAL_SECONDS", "10"))
INCREMENTAL_LIMIT = int(os.getenv("CASH_FINANCE_INCREMENTAL_LIMIT", "5000"))
INCREMENTAL_URL = os.getenv(
    "CASH_FINANCE_INCREMENTAL_URL",
    "http://100.113.224.68:3012/api/finance/incremental",
)
REQUEST_TIMEOUT_SECONDS = int(os.getenv("CASH_FINANCE_REQUEST_TIMEOUT_SECONDS", "20"))
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "cash-finance-sync.log"
DATA_DIR = BASE_DIR / "data" / "finance_cache"
STATE_PATH = DATA_DIR / "sync_state.json"
CACHE_DB = Path(os.getenv("CASH_FINANCE_CACHE_DB", DATA_DIR / "cash_finance_cache.sqlite"))
CLOUD_PUSH_STATE_PATH = DATA_DIR / "cloudflare_push_state.json"
LOCK_PATH = DATA_DIR / "cash_finance_sync_worker.lock"
CASH_FINANCE_OVERLAP_ROWS = int(os.getenv("CASH_FINANCE_OVERLAP_ROWS", "50"))
AUTO_REBUILD_ON_CACHE_MISMATCH = os.getenv("CASH_FINANCE_AUTO_REBUILD", "1").lower() not in {"0", "false", "no"}
CORRUPT_BACKUP_DIR = Path(os.getenv("CASH_FINANCE_CORRUPT_BACKUP_DIR", DATA_DIR / "corrupt_backups"))
LOCK_HANDLE = None


def force_utf8_stdio():
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def now_text():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_log(message):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{now_text()}] {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > 5 * 1024 * 1024:
        archive = LOG_DIR / f"cash-finance-sync-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        LOG_PATH.replace(archive)


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


def write_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def read_state():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def payload_hash(payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_cloud_push_state():
    if not CLOUD_PUSH_STATE_PATH.exists():
        return {}
    try:
        return json.loads(CLOUD_PUSH_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_cloud_push_state(state):
    CLOUD_PUSH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLOUD_PUSH_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def is_cloud_push_enabled():
    value = os.getenv("CASH_DIFF_CLOUD_PUSH_ENABLED")
    if value is not None:
        return value == "1"
    return cash_diff_cloudflare_push.has_push_credentials()


def push_cloud_if_changed(payload):
    current_hash = payload_hash(payload)
    state = read_cloud_push_state()
    if state.get("payload_hash") == current_hash:
        return False

    cash_diff_cloudflare_push.push_payload(payload)
    write_cloud_push_state(
        {
            "ok": True,
            "last_success_at": now_text(),
            "payload_hash": current_hash,
            "latest_id": payload.get("latest_id"),
            "usable_total_amount": payload.get("usable_total_amount"),
        }
    )
    return True


def connect_cache():
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma busy_timeout = 30000")
    return conn


def init_cache(conn):
    conn.executescript(
        """
        pragma journal_mode = wal;

        create table if not exists cash_records (
            id integer primary key,
            total_amount integer not null,
            quantity integer not null,
            value integer not null,
            type integer not null,
            target integer not null,
            flow_mode integer not null,
            timestamp text not null
        );
        """
    )


def get_local_latest_id(conn):
    row = conn.execute("select max(id) from cash_records").fetchone()
    return int(row[0] or 0)


def get_incremental_after_id(conn):
    latest_id = get_local_latest_id(conn)
    if latest_id <= 0:
        return 0
    return max(0, latest_id - max(0, CASH_FINANCE_OVERLAP_ROWS))


def read_cache_counts(conn):
    return conn.execute(
        """
        select
            count(*) as cash_record_rows,
            min(id) as first_id,
            max(id) as latest_id,
            max(timestamp) as latest_cash_record_time
        from cash_records
        """
    ).fetchone()


def inspect_cache(conn):
    reasons = []
    integrity_rows = [row[0] for row in conn.execute("pragma integrity_check(20)")]
    bad_integrity = [item for item in integrity_rows if str(item).lower() != "ok"]
    if bad_integrity:
        reasons.append("integrity_check=" + " | ".join(str(item) for item in bad_integrity[:3]))

    counts = read_cache_counts(conn)
    state = read_state()
    state_summary = state.get("summary") or {}
    agent_latest_id = int(state.get("agent_latest_id") or 0)
    state_latest_id = int(state_summary.get("latest_id") or 0)
    actual_latest_id = int(counts["latest_id"] or 0)
    state_rows = int(state_summary.get("cash_record_rows") or 0)
    actual_rows = int(counts["cash_record_rows"] or 0)
    if max(agent_latest_id, state_latest_id) > actual_latest_id:
        reasons.append(
            f"latest_id recorded={max(agent_latest_id, state_latest_id)} actual={actual_latest_id}"
        )
    if state_rows > actual_rows:
        reasons.append(f"cash_record_rows recorded={state_rows} actual={actual_rows}")

    return {"healthy": not reasons, "reasons": reasons, "counts": counts}


def import_initial_from_snapshot(conn):
    if get_local_latest_id(conn):
        return 0

    source = cash_finance_audit.CURRENT_DB
    if not source.exists():
        write_log("initial cache empty and current snapshot missing; fetching one full snapshot")
        source, _latency_ms = cash_finance_audit.fetch_snapshot()

    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    source_conn.row_factory = sqlite3.Row
    try:
        rows = source_conn.execute(
            """
            select Id, TotalAmount, Quantity, Value, Type, Target, FlowMode, Timestamp
            from CashRecord
            order by Id
            """
        ).fetchall()
        upsert_cash_records(conn, rows)
        return len(rows)
    finally:
        source_conn.close()


def fetch_incremental(after_id):
    params = urllib.parse.urlencode({"after_id": str(after_id), "limit": str(INCREMENTAL_LIMIT)})
    url = f"{INCREMENTAL_URL}?{params}"
    started_at = time.time()
    with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "Cash finance incremental API returned ok=false")
    return payload, int((time.time() - started_at) * 1000)


def normalize_row(row):
    return {
        "id": row["Id"],
        "total_amount": row["TotalAmount"],
        "quantity": row["Quantity"],
        "value": row["Value"],
        "type": row["Type"],
        "target": row["Target"],
        "flow_mode": row["FlowMode"],
        "timestamp": row["Timestamp"],
    }


def upsert_cash_records(conn, rows):
    normalized = [normalize_row(dict(row)) for row in rows]
    conn.executemany(
        """
        insert into cash_records (
            id, total_amount, quantity, value, type, target, flow_mode, timestamp
        ) values (
            :id, :total_amount, :quantity, :value, :type, :target, :flow_mode, :timestamp
        )
        on conflict(id) do update set
            total_amount=excluded.total_amount,
            quantity=excluded.quantity,
            value=excluded.value,
            type=excluded.type,
            target=excluded.target,
            flow_mode=excluded.flow_mode,
            timestamp=excluded.timestamp
        """,
        normalized,
    )
    return len(normalized)


def archive_cache_files(reason):
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    CORRUPT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    archived = []
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(CACHE_DB) + suffix)
        if not source.exists():
            continue
        target = CORRUPT_BACKUP_DIR / f"{source.name}.{timestamp}.corrupt"
        for attempt in range(5):
            try:
                shutil.move(str(source), str(target))
                archived.append(str(target))
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.5)
    write_log(f"cache archive reason={reason} files={archived}")
    return archived


def rebuild_cache_from_snapshot(reason):
    source, latency_ms = cash_finance_audit.fetch_snapshot()
    archive_cache_files(reason)
    conn = connect_cache()
    try:
        init_cache(conn)
        source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        source_conn.row_factory = sqlite3.Row
        try:
            rows = source_conn.execute(
                """
                select Id, TotalAmount, Quantity, Value, Type, Target, FlowMode, Timestamp
                from CashRecord
                order by Id
                """
            ).fetchall()
        finally:
            source_conn.close()
        with conn:
            imported = upsert_cash_records(conn, rows)
        write_log(f"cache rebuild completed reason={reason} imported={imported} snapshot_latency_ms={latency_ms}")
        return imported
    finally:
        conn.close()


def summarize_cache(conn):
    row = conn.execute(
        """
        select
            count(*) as cash_record_rows,
            max(id) as latest_id,
            max(timestamp) as latest_cash_record_time
        from cash_records
        """
    ).fetchone()

    usable_rows = conn.execute(
        """
        select type, target, value,
               sum(total_amount) as total_amount,
               sum(quantity) as quantity,
               max(timestamp) as latest_time
        from cash_records
        where target = 0
          and ((type = 0 and value in (1, 5, 10, 50))
            or (type = 1 and value in (100, 200, 500, 1000, 2000)))
        group by type, target, value
        order by value
        """
    ).fetchall()

    running_totals = conn.execute(
        """
        select type, target,
               sum(total_amount) as total_amount,
               sum(quantity) as quantity,
               count(*) as rows,
               max(timestamp) as latest_time
        from cash_records
        group by type, target
        order by type, target
        """
    ).fetchall()

    cashbox_event = conn.execute(
        """
        select id, total_amount, quantity, value, type, target, flow_mode, timestamp
        from cash_records
        where target = 1
          and flow_mode = 4
        order by id desc
        limit 1
        """
    ).fetchone()

    usable_by_denomination = {
        str(item["value"]): {
            "type": item["type"],
            "target": item["target"],
            "quantity": item["quantity"],
            "total_amount": item["total_amount"],
            "latest_time": item["latest_time"],
        }
        for item in usable_rows
    }

    return {
        "cash_record_rows": row["cash_record_rows"],
        "latest_id": row["latest_id"],
        "latest_cash_record_time": row["latest_cash_record_time"],
        "usable_total_amount": sum(int(item["total_amount"] or 0) for item in usable_rows),
        "usable_by_denomination": usable_by_denomination,
        "running_totals": [dict(item) for item in running_totals],
        "cashbox": {
            "system_amount": abs(int(cashbox_event["total_amount"] or 0)) if cashbox_event else 0,
            "event_id": cashbox_event["id"] if cashbox_event else None,
            "event_time": cashbox_event["timestamp"] if cashbox_event else None,
            "raw": dict(cashbox_event) if cashbox_event else None,
        },
    }


def sync_once(force_rebuild=False):
    started_at = time.time()
    conn = connect_cache()
    imported_rows = 0
    total_new_rows = 0
    latest_payload = None
    total_latency_ms = 0
    previous_state = read_state()
    try:
        init_cache(conn)
        cache = inspect_cache(conn)
        rebuild_reason = None
        if force_rebuild:
            rebuild_reason = "manual rebuild"
        elif not cache["healthy"]:
            rebuild_reason = "; ".join(cache["reasons"])
            if not AUTO_REBUILD_ON_CACHE_MISMATCH:
                raise RuntimeError(f"cash finance cache needs rebuild: {rebuild_reason}")

        if rebuild_reason:
            conn.close()
            imported_rows = rebuild_cache_from_snapshot(rebuild_reason)
            conn = connect_cache()
            init_cache(conn)
        else:
            with conn:
                imported_rows = import_initial_from_snapshot(conn)

        while True:
            after_id = get_incremental_after_id(conn)
            payload, latency_ms = fetch_incremental(after_id)
            total_latency_ms += latency_ms
            latest_payload = payload
            rows = payload.get("cash_records") or []
            if rows:
                with conn:
                    total_new_rows += upsert_cash_records(conn, rows)
            if not payload.get("has_more") or not rows:
                break

        summary = summarize_cache(conn)
        if latest_payload and latest_payload.get("latest_id") and int(latest_payload["latest_id"]) > int(summary["latest_id"] or 0):
            raise RuntimeError(
                "cash finance cache did not catch up: agent_latest_id={agent} local_latest_id={local}".format(
                    agent=latest_payload["latest_id"],
                    local=summary["latest_id"] or 0,
                )
            )
        elapsed_ms = int((time.time() - started_at) * 1000)
        cache_rebuild_at = now_text() if rebuild_reason else previous_state.get("cache_rebuild_at")
        cache_rebuild_reason = rebuild_reason if rebuild_reason else previous_state.get("cache_rebuild_reason")
        write_state(
            state_payload := {
                "ok": True,
                "mode": "incremental",
                "last_success_at": now_text(),
                "last_error_at": None,
                "last_error": None,
                "elapsed_ms": elapsed_ms,
                "agent_latency_ms": total_latency_ms,
                "imported_rows": imported_rows,
                "new_rows": total_new_rows,
                "cache_db": str(CACHE_DB),
                "agent_latest_id": latest_payload.get("latest_id") if latest_payload else None,
                "agent_latest_timestamp": latest_payload.get("latest_timestamp") if latest_payload else None,
                "agent_database": latest_payload.get("database") if latest_payload else None,
                "overlap_rows": CASH_FINANCE_OVERLAP_ROWS,
                "cache_rebuild_at": cache_rebuild_at,
                "cache_rebuild_reason": cache_rebuild_reason,
                "summary": summary,
            }
        )
        cloud_pushed = False
        if is_cloud_push_enabled():
            cloud_payload = cash_diff_cloudflare_push.build_payload(state_payload)
            cloud_pushed = push_cloud_if_changed(cloud_payload)
            state_payload["cloudflare"] = {
                "ok": True,
                "mode": "push_on_change",
                "last_success_at": now_text() if cloud_pushed else read_cloud_push_state().get("last_success_at"),
                "pushed": cloud_pushed,
            }
            write_state(state_payload)
        write_log(
            "sync ok mode=incremental imported={imported} new_rows={new_rows} latest_id={latest_id} usable_total={total} cloud={cloud} elapsed_ms={elapsed}".format(
                imported=imported_rows,
                new_rows=total_new_rows,
                latest_id=summary["latest_id"],
                total=summary["usable_total_amount"],
                cloud="yes" if cloud_pushed else "no",
                elapsed=elapsed_ms,
            )
        )
    finally:
        conn.close()


def record_error(error):
    state = {
        "ok": False,
        "mode": "incremental",
        "last_error_at": now_text(),
        "last_error": str(error),
    }
    if STATE_PATH.exists():
        try:
            existing = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            existing.update(state)
            state = existing
        except Exception:
            pass
    write_state(state)
    write_log(f"sync error: {error}")


def run_loop():
    if not acquire_single_instance_lock():
        write_log("another cash finance incremental sync worker is already running; exiting")
        return
    write_log(
        f"starting cash finance incremental sync worker interval={INTERVAL_SECONDS}s agent={INCREMENTAL_URL}"
    )
    while True:
        try:
            sync_once()
        except Exception as exc:
            record_error(exc)
        time.sleep(INTERVAL_SECONDS)


def main():
    force_utf8_stdio()
    parser = argparse.ArgumentParser(description="Continuously sync kiosk CashRecord rows incrementally.")
    parser.add_argument("--once", action="store_true", help="Run one sync and exit.")
    parser.add_argument("--rebuild", action="store_true", help="Archive the current cache and rebuild from a fresh finance snapshot.")
    args = parser.parse_args()
    if args.once:
        if not acquire_single_instance_lock():
            write_log("another cash finance incremental sync worker is already running; exiting")
            return
        sync_once(force_rebuild=args.rebuild)
    else:
        run_loop()


if __name__ == "__main__":
    main()
