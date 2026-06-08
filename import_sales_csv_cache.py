import argparse
import csv
import datetime as dt
import json
import sqlite3
import time
from pathlib import Path

from sync_sales_cache import DB_PATH, init_db


BASE_DIR = Path(__file__).resolve().parent


TABLE_COLUMNS = {
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
    "product_categories": ["id", "name", "enabled", "sequence", "store_guid", "timestamp"],
    "product_category_items": ["product_category_id", "product_id", "sequence", "store_guid", "timestamp"],
    "payment_types": ["id", "name", "type", "timestamp"],
}


CSV_FILES = {
    "orders": "orders.csv",
    "order_products": "order_products.csv",
    "order_payments": "order_payments.csv",
    "product_categories": "product_categories.csv",
    "product_category_items": "product_category_items.csv",
    "payment_types": "payment_types.csv",
}


def latest_export_dir():
    exports_dir = BASE_DIR / "exports"
    candidates = [path for path in exports_dir.glob("sales-*") if path.is_dir()]
    if not candidates:
        raise FileNotFoundError(f"No sales export directories found under {exports_dir}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def read_manifest(export_dir):
    manifest_path = export_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8-sig"))


def row_values(row, columns):
    return [None if row.get(column) == "" else row.get(column) for column in columns]


def import_table(conn, export_dir, table, batch_size=2000):
    columns = TABLE_COLUMNS[table]
    csv_path = export_dir / CSV_FILES[table]
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    placeholders = ", ".join(["?"] * len(columns))
    quoted = ", ".join(columns)
    sql = f"insert into {table} ({quoted}) values ({placeholders})"

    conn.execute(f"delete from {table}")
    count = 0
    batch = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            batch.append(row_values(row, columns))
            if len(batch) >= batch_size:
                conn.executemany(sql, batch)
                count += len(batch)
                batch = []
        if batch:
            conn.executemany(sql, batch)
            count += len(batch)
    print(f"{table}: {count} rows")
    return count


def update_sync_state(conn, manifest, started_at):
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    latency_ms = int((time.time() - started_at) * 1000)
    counts = conn.execute(
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
    source_range = (((manifest or {}).get("remote_export") or {}).get("range") or {})
    source_latest = source_range.get("latest_order_time") or counts["local_latest_order_time"]

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
            now,
            latency_ms,
            counts["source_min_business_date"],
            counts["source_max_business_date"],
            source_latest,
            counts["local_latest_order_time"],
            counts["order_rows"],
            counts["order_product_rows"],
            counts["order_payment_rows"],
        ),
    )


def import_export(export_dir):
    started_at = time.time()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    manifest = read_manifest(export_dir)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        init_db(conn)
        with conn:
            conn.execute("delete from order_payments")
            conn.execute("delete from order_products")
            conn.execute("delete from orders")
            for table in (
                "orders",
                "order_products",
                "order_payments",
                "product_categories",
                "product_category_items",
                "payment_types",
            ):
                import_table(conn, export_dir, table)
            update_sync_state(conn, manifest, started_at)
    finally:
        conn.close()

    print(f"imported: {DB_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Import kiosk sales CSV export into local SQLite cache.")
    parser.add_argument("export_dir", nargs="?", help="Export directory. Defaults to latest exports/sales-* directory.")
    args = parser.parse_args()
    export_dir = Path(args.export_dir).resolve() if args.export_dir else latest_export_dir()
    import_export(export_dir)


if __name__ == "__main__":
    main()
