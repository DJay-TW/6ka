import argparse
import datetime as dt
import json
import msvcrt
import os
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("SALES_CACHE_DB", BASE_DIR / "data" / "sales_cache.sqlite"))
AGENT_URL = os.getenv("KIOSK_AGENT_INCREMENTAL_URL", "http://100.113.224.68:3010/api/sales/incremental")
AGENT_STATUS_URL = os.getenv("KIOSK_AGENT_STATUS_URL", AGENT_URL.rsplit("/", 2)[0] + "/status")
INTERVAL_SECONDS = int(os.getenv("SALES_SYNC_INTERVAL_SECONDS", "10"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("SALES_SYNC_REQUEST_TIMEOUT_SECONDS", "8"))
MAX_INCREMENTAL_DAYS = int(os.getenv("SALES_SYNC_MAX_INCREMENTAL_DAYS", "31"))
MIN_INCREMENTAL_DAYS = int(os.getenv("SALES_SYNC_MIN_INCREMENTAL_DAYS", "2"))
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "sales-cache-sync.log"
AUTO_REBUILD_ON_CACHE_MISMATCH = os.getenv("SALES_SYNC_AUTO_REBUILD", "1").lower() not in {"0", "false", "no"}
CORRUPT_BACKUP_DIR = Path(os.getenv("SALES_CACHE_CORRUPT_BACKUP_DIR", DB_PATH.parent / "corrupt_backups"))
LOCK_PATH = Path(os.getenv("SALES_CACHE_SYNC_LOCK_PATH", DB_PATH.parent / "sales_cache_sync_worker.lock"))
INTEGRITY_CHECK_INTERVAL_SECONDS = int(os.getenv("SALES_CACHE_INTEGRITY_CHECK_INTERVAL_SECONDS", "300"))
LOCK_HANDLE = None
LAST_INTEGRITY_CHECK_MONOTONIC = 0.0


TABLE_COLUMNS = {
    "product_categories": [
        "id",
        "name",
        "enabled",
        "sequence",
        "store_guid",
        "timestamp",
    ],
    "product_category_items": [
        "product_category_id",
        "product_id",
        "sequence",
        "store_guid",
        "timestamp",
    ],
    "payment_types": [
        "id",
        "name",
        "type",
        "timestamp",
    ],
    "orders": [
        "guid",
        "id",
        "provider",
        "type",
        "status",
        "total_amount",
        "kiosk_guid",
        "business_date",
        "store_guid",
        "order_time",
        "void_time",
        "timestamp",
        "display_id",
    ],
    "order_products": [
        "order_guid",
        "parent",
        "guid",
        "id",
        "name",
        "type",
        "tax",
        "unit_price",
        "additional_price",
        "quantity",
        "total_price",
        "sequence",
        "store_guid",
        "timestamp",
    ],
    "order_payments": [
        "order_guid",
        "guid",
        "payment_type_id",
        "amount",
        "redeem_amount",
        "change_amount",
        "kiosk_guid",
        "store_guid",
        "timestamp",
    ],
}


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
        archive = LOG_DIR / f"sales-cache-sync-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        LOG_PATH.replace(archive)


def acquire_single_instance_lock():
    global LOCK_HANDLE
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
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


def connect_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma busy_timeout = 30000")
    return conn


def init_db(conn):
    conn.executescript(
        """
        pragma journal_mode = wal;

        create table if not exists sync_state (
            source text primary key,
            last_success_at text,
            last_error_at text,
            last_error text,
            latency_ms integer,
            source_min_business_date text,
            source_max_business_date text,
            source_latest_order_time text,
            local_latest_order_time text,
            order_rows integer,
            order_product_rows integer,
            order_payment_rows integer,
            cache_rebuild_at text,
            cache_rebuild_reason text
        );

        create table if not exists product_categories (
            id text not null,
            name text not null,
            enabled integer not null,
            sequence integer not null,
            store_guid text not null,
            timestamp text not null,
            primary key (id, store_guid)
        );

        create table if not exists product_category_items (
            product_category_id text not null,
            product_id text not null,
            sequence integer not null,
            store_guid text not null,
            timestamp text not null,
            primary key (product_category_id, product_id, store_guid)
        );

        create table if not exists payment_types (
            id text primary key,
            name text not null,
            type integer not null,
            timestamp text not null
        );

        create table if not exists orders (
            guid text not null,
            id text not null,
            provider integer not null,
            type integer not null,
            status integer not null,
            total_amount real not null,
            kiosk_guid text not null,
            business_date text not null,
            store_guid text not null,
            order_time text not null,
            void_time text,
            timestamp text not null,
            display_id text,
            primary key (guid, id, business_date, store_guid)
        );

        create index if not exists idx_orders_business_date on orders (business_date);
        create index if not exists idx_orders_timestamp on orders (timestamp);
        create index if not exists idx_orders_guid on orders (guid);

        create table if not exists order_products (
            order_guid text not null,
            parent text,
            guid text not null,
            id text not null,
            name text not null,
            type integer not null,
            tax integer not null,
            unit_price real not null,
            additional_price real not null,
            quantity integer not null,
            total_price real not null,
            sequence integer not null,
            store_guid text not null,
            timestamp text not null,
            primary key (order_guid, guid, store_guid)
        );

        create index if not exists idx_order_products_order_guid on order_products (order_guid);

        create table if not exists order_payments (
            order_guid text not null,
            guid text not null,
            payment_type_id text not null,
            amount real not null,
            redeem_amount real not null,
            change_amount real not null,
            kiosk_guid text not null,
            store_guid text not null,
            timestamp text not null,
            primary key (order_guid, guid, store_guid)
        );

        create index if not exists idx_order_payments_order_guid on order_payments (order_guid);
        """
    )
    ensure_column(conn, "sync_state", "cache_rebuild_at", "text")
    ensure_column(conn, "sync_state", "cache_rebuild_reason", "text")


def ensure_column(conn, table, column, declaration):
    columns = {row["name"] for row in conn.execute(f"pragma table_info({table})")}
    if column not in columns:
        conn.execute(f"alter table {table} add column {column} {declaration}")


def get_local_latest(conn):
    row = conn.execute("select max(timestamp) from orders").fetchone()
    return row[0] if row else None


def read_cache_counts(conn):
    return conn.execute(
        """
        select
            (select count(*) from orders) as order_rows,
            (select count(*) from order_products) as order_product_rows,
            (select count(*) from order_payments) as order_payment_rows,
            (select min(business_date) from orders) as source_min_business_date,
            (select max(business_date) from orders) as source_max_business_date,
            (select max(timestamp) from orders) as local_latest_order_time
        """
    ).fetchone()


def inspect_cache():
    global LAST_INTEGRITY_CHECK_MONOTONIC
    if not DB_PATH.exists():
        return {"healthy": True, "counts": None, "local_latest": None, "reasons": []}

    reasons = []
    conn = connect_db()
    try:
        init_db(conn)
        counts = read_cache_counts(conn)
        now = time.monotonic()
        if (
            LAST_INTEGRITY_CHECK_MONOTONIC <= 0
            or now - LAST_INTEGRITY_CHECK_MONOTONIC >= INTEGRITY_CHECK_INTERVAL_SECONDS
        ):
            integrity_check = [row[0] for row in conn.execute("pragma integrity_check(50)")]
            LAST_INTEGRITY_CHECK_MONOTONIC = now
            bad_checks = [item for item in integrity_check if str(item).lower() != "ok"]
            if bad_checks:
                reasons.append("integrity_check=" + " | ".join(str(item) for item in bad_checks[:3]))

        state = conn.execute("select * from sync_state where source = 'kiosk'").fetchone()
        if state:
            for key in ("order_rows", "order_product_rows", "order_payment_rows"):
                recorded = int(state[key] or 0)
                actual = int(counts[key] or 0)
                if recorded > actual:
                    reasons.append(f"{key} recorded={recorded} actual={actual}")
            state_latest = parse_datetime(state["local_latest_order_time"])
            actual_latest = parse_datetime(counts["local_latest_order_time"])
            if state_latest and (not actual_latest or state_latest > actual_latest):
                reasons.append(
                    "local_latest_order_time recorded={recorded} actual={actual}".format(
                        recorded=state["local_latest_order_time"],
                        actual=counts["local_latest_order_time"] or "-",
                    )
                )

        return {
            "healthy": not reasons,
            "counts": counts,
            "local_latest": counts["local_latest_order_time"] if counts else None,
            "reasons": reasons,
        }
    except sqlite3.DatabaseError as exc:
        return {"healthy": False, "counts": None, "local_latest": None, "reasons": [str(exc)]}
    finally:
        conn.close()


def parse_datetime(value):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(text[:19], fmt)
        except ValueError:
            pass
    return None


def fetch_agent_status():
    started_at = time.time()
    with urllib.request.urlopen(AGENT_STATUS_URL, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        body = response.read().decode("utf-8")
    payload = json.loads(body)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "Agent status API returned ok=false")
    return payload, int((time.time() - started_at) * 1000)


def get_source_latest(status_payload):
    database = status_payload.get("database") or {}
    state = status_payload.get("state") or {}
    return (
        database.get("latest_order_time")
        or status_payload.get("source_latest_order_time")
        or state.get("source_latest_order_time")
    )


def calculate_sync_days(local_latest, source_latest):
    local_dt = parse_datetime(local_latest)
    source_dt = parse_datetime(source_latest)
    if not local_dt:
        return MAX_INCREMENTAL_DAYS
    if not source_dt or source_dt <= local_dt:
        return max(1, MIN_INCREMENTAL_DAYS)
    lag_days = (source_dt.date() - local_dt.date()).days + 1
    return max(1, min(MAX_INCREMENTAL_DAYS, max(MIN_INCREMENTAL_DAYS, lag_days)))


def fetch_incremental(since, days):
    params = {"days": str(days)}
    if since:
        params["since"] = since
    url = AGENT_URL + "?" + urllib.parse.urlencode(params)
    started_at = time.time()
    with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        body = response.read().decode("utf-8")
    payload = json.loads(body)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "Agent incremental API returned ok=false")
    return payload, int((time.time() - started_at) * 1000)


def normalize_row(row):
    return {str(k).lower(): v for k, v in row.items()}


def upsert_rows(conn, table, rows):
    columns = TABLE_COLUMNS[table]
    quoted = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns)
    conn.executemany(
        f"""
        insert into {table} ({quoted}) values ({placeholders})
        on conflict do update set {updates}
        """,
        ([normalize_row(row).get(column) for column in columns] for row in rows),
    )


def replace_rows(conn, table, rows):
    columns = TABLE_COLUMNS[table]
    quoted = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    conn.execute(f"delete from {table}")
    conn.executemany(
        f"insert into {table} ({quoted}) values ({placeholders})",
        ([normalize_row(row).get(column) for column in columns] for row in rows),
    )


def archive_database_files(reason):
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    CORRUPT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    archived = []
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(DB_PATH) + suffix)
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


def update_sync_state(conn, payload, latency_ms, error=None):
    counts = read_cache_counts(conn)

    if error:
        conn.execute(
            """
            insert into sync_state (source, last_error_at, last_error, latency_ms)
            values ('kiosk', ?, ?, ?)
            on conflict(source) do update set
                last_error_at=excluded.last_error_at,
                last_error=excluded.last_error,
                latency_ms=excluded.latency_ms
            """,
            (now_text(), str(error), latency_ms),
        )
        return

    conn.execute(
        """
        insert into sync_state (
            source, last_success_at, last_error_at, last_error, latency_ms,
            source_min_business_date, source_max_business_date, source_latest_order_time,
            local_latest_order_time, order_rows, order_product_rows, order_payment_rows
        ) values ('kiosk', ?, null, null, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(source) do update set
            last_success_at=excluded.last_success_at,
            last_error_at=null,
            last_error=null,
            latency_ms=excluded.latency_ms,
            source_min_business_date=excluded.source_min_business_date,
            source_max_business_date=excluded.source_max_business_date,
            source_latest_order_time=excluded.source_latest_order_time,
            local_latest_order_time=excluded.local_latest_order_time,
            order_rows=excluded.order_rows,
            order_product_rows=excluded.order_product_rows,
            order_payment_rows=excluded.order_payment_rows
        """,
        (
            now_text(),
            latency_ms,
            counts["source_min_business_date"],
            counts["source_max_business_date"],
            payload.get("source_latest_order_time") or counts["local_latest_order_time"],
            counts["local_latest_order_time"],
            counts["order_rows"],
            counts["order_product_rows"],
            counts["order_payment_rows"],
        ),
    )


def mark_cache_rebuild(conn, reason):
    conn.execute(
        """
        update sync_state
        set cache_rebuild_at = ?,
            cache_rebuild_reason = ?
        where source = 'kiosk'
        """,
        (now_text(), reason),
    )


def sync_once(days_override=None, since_override=None, force_rebuild=False):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache = inspect_cache()
    rebuild_reason = None
    if force_rebuild:
        rebuild_reason = "manual rebuild"
    elif not cache["healthy"]:
        rebuild_reason = "; ".join(cache["reasons"])
        if not AUTO_REBUILD_ON_CACHE_MISMATCH:
            raise RuntimeError(f"sales cache needs rebuild: {rebuild_reason}")

    since = None if rebuild_reason else cache["local_latest"]
    try:
        if since_override is not None:
            since = since_override
        status_payload, status_latency_ms = fetch_agent_status()
        source_latest = get_source_latest(status_payload)
        days = calculate_sync_days(since, source_latest)
        if rebuild_reason and since_override is None:
            days = MAX_INCREMENTAL_DAYS
        if days_override:
            days = max(1, min(MAX_INCREMENTAL_DAYS, int(days_override)))
        payload, latency_ms = fetch_incremental(since, days)
        if rebuild_reason:
            write_log(f"cache rebuild triggered reason={rebuild_reason} days={days}")
            archive_database_files(rebuild_reason)
        conn = connect_db()
        try:
            with conn:
                init_db(conn)
                for table in ("product_categories", "product_category_items", "payment_types"):
                    if table in payload:
                        replace_rows(conn, table, payload.get(table) or [])
                upsert_rows(conn, "orders", payload.get("orders") or [])
                upsert_rows(conn, "order_products", payload.get("order_products") or [])
                upsert_rows(conn, "order_payments", payload.get("order_payments") or [])
                update_sync_state(conn, payload, latency_ms)
                if rebuild_reason:
                    mark_cache_rebuild(conn, rebuild_reason)
        finally:
            conn.close()
        counts = (
            len(payload.get("orders") or []),
            len(payload.get("order_products") or []),
            len(payload.get("order_payments") or []),
        )
        write_log(
            "sync ok since={since} days={days} orders={orders} products={products} payments={payments} source_latest={latest} status_latency_ms={status_latency} latency_ms={latency}".format(
                since=since or "-",
                days=days,
                orders=counts[0],
                products=counts[1],
                payments=counts[2],
                latest=payload.get("source_latest_order_time") or source_latest,
                status_latency=status_latency_ms,
                latency=latency_ms,
            )
        )
        if rebuild_reason:
            write_log("cache rebuild completed")
    except Exception as exc:
        latency_ms = 0
        try:
            conn = connect_db()
            with conn:
                init_db(conn)
                if rebuild_reason:
                    update_sync_state(conn, {}, latency_ms, error=f"cache rebuild pending: {rebuild_reason}; {exc}")
                else:
                    update_sync_state(conn, {}, latency_ms, error=exc)
            conn.close()
        except Exception:
            pass
        write_log(f"sync error: {exc}")
        raise


def run_loop():
    if not acquire_single_instance_lock():
        write_log("another sales cache sync worker is already running; exiting")
        return
    write_log(f"starting sales cache sync worker interval={INTERVAL_SECONDS}s db={DB_PATH} agent={AGENT_URL}")
    while True:
        try:
            sync_once()
        except Exception:
            pass
        time.sleep(INTERVAL_SECONDS)


def main():
    force_utf8_stdio()
    parser = argparse.ArgumentParser(description="Continuously sync kiosk Agent incremental sales data into SQLite.")
    parser.add_argument("--once", action="store_true", help="Run one sync and exit.")
    parser.add_argument("--days", type=int, help="Override Agent incremental days for this run.")
    parser.add_argument("--since", help="Override incremental since timestamp for this run.")
    parser.add_argument("--rebuild", action="store_true", help="Archive the current cache and rebuild from Agent data.")
    args = parser.parse_args()
    if args.once:
        if not acquire_single_instance_lock():
            write_log("another sales cache sync worker is already running; exiting")
            return
        sync_once(days_override=args.days, since_override=args.since, force_rebuild=args.rebuild)
    else:
        run_loop()


if __name__ == "__main__":
    main()
