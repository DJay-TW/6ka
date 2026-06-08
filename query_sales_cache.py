import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("SALES_CACHE_DB", BASE_DIR / "data" / "sales_cache.sqlite"))
BOWL_CATEGORY_NAME = os.getenv("RP_BOWL_CATEGORY_NAME", "拉麵類")


def force_utf8_stdio():
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def row_to_dict(row):
    return dict(row) if row else {}


def as_number(value):
    return float(value or 0)


def fill_sales_buckets(rows, business_date):
    by_start = {row["bucket_start"]: dict(row) for row in rows}
    if not by_start:
        return []

    first_hour, first_minute = map(int, min(by_start).split(":"))
    last_hour, last_minute = map(int, max(by_start).split(":"))
    start = dt.datetime.combine(dt.date.fromisoformat(business_date), dt.time(first_hour, first_minute))
    last = dt.datetime.combine(dt.date.fromisoformat(business_date), dt.time(last_hour, last_minute))
    now = dt.datetime.now()
    if now.date().isoformat() == business_date:
        current_minute = (now.minute // 30) * 30
        current_bucket = now.replace(minute=current_minute, second=0, microsecond=0)
        last = max(last, current_bucket)

    buckets = []
    cursor = start
    while cursor <= last:
        key = cursor.strftime("%H:%M")
        row = by_start.get(key)
        if row:
            buckets.append(row)
        else:
            buckets.append(
                {
                    "bucket_start": key,
                    "bucket_end": (cursor + dt.timedelta(minutes=30)).strftime("%H:%M:%S"),
                    "order_count": 0,
                    "payment_amount": 0,
                    "change_amount": 0,
                    "net_sales_amount": 0,
                    "bowl_count": 0,
                }
            )
        cursor += dt.timedelta(minutes=30)
    return buckets


def query_snapshot(business_date):
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Sales cache not found: {DB_PATH}")

    target_date = dt.date.fromisoformat(business_date)
    month_start = target_date.replace(day=1).isoformat()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        summary = row_to_dict(
            conn.execute(
                """
                with valid_orders as (
                    select guid, id, display_id, status, business_date, timestamp
                    from orders
                    where business_date = ?
                      and status = 4
                ),
                bowl_products as (
                    select distinct pci.product_id
                    from product_category_items pci
                    join product_categories pc
                      on pc.id = pci.product_category_id
                     and pc.store_guid = pci.store_guid
                    where pc.name = ?
                )
                select
                    ? as business_date,
                    (select count(*) from valid_orders) as order_count,
                    coalesce((select sum(total_price) from order_products op join valid_orders vo on vo.guid = op.order_guid), 0) as product_revenue,
                    coalesce((select sum(quantity) from order_products where order_guid in (select guid from valid_orders) and id in (select product_id from bowl_products)), 0) as bowl_count,
                    coalesce((select sum(amount) from order_payments op join valid_orders vo on vo.guid = op.order_guid), 0) as payment_amount,
                    coalesce((select sum(change_amount) from order_payments op join valid_orders vo on vo.guid = op.order_guid), 0) as change_amount,
                    coalesce((select sum(amount - change_amount) from order_payments op join valid_orders vo on vo.guid = op.order_guid), 0) as net_sales_amount,
                    (select max(timestamp) from valid_orders) as latest_order_time
                """,
                (business_date, BOWL_CATEGORY_NAME, business_date),
            ).fetchone()
        )

        payments = [
            dict(row)
            for row in conn.execute(
                """
                select
                    op.payment_type_id as payment_type,
                    count(*) as count,
                    coalesce(sum(op.amount), 0) as amount,
                    coalesce(sum(op.change_amount), 0) as change_amount,
                    coalesce(sum(op.amount - op.change_amount), 0) as net_amount
                from order_payments op
                join orders o on o.guid = op.order_guid
                where o.business_date = ?
                  and o.status = 4
                group by op.payment_type_id
                order by op.payment_type_id
                """,
                (business_date,),
            )
        ]

        latest_orders = [
            dict(row)
            for row in conn.execute(
                """
                select
                    o.timestamp,
                    o.id,
                    o.display_id,
                    op.payment_type_id as payment_type,
                    op.amount,
                    op.change_amount,
                    op.amount - op.change_amount as net_amount
                from order_payments op
                join orders o on o.guid = op.order_guid
                where o.business_date = ?
                  and o.status = 4
                order by o.timestamp desc
                limit 10
                """,
                (business_date,),
            )
        ]

        sales_buckets = fill_sales_buckets([
            dict(row)
            for row in conn.execute(
                """
                with valid_orders as (
                    select guid, business_date, timestamp
                    from orders
                    where business_date = ?
                      and status = 4
                ),
                bucket_orders as (
                    select
                        vo.guid,
                        printf(
                            '%02d:%02d',
                            cast(strftime('%H', vo.timestamp) as integer),
                            (cast(strftime('%M', vo.timestamp) as integer) / 30) * 30
                        ) as bucket_start
                    from valid_orders vo
                ),
                bowl_products as (
                    select distinct pci.product_id
                    from product_category_items pci
                    join product_categories pc
                      on pc.id = pci.product_category_id
                     and pc.store_guid = pci.store_guid
                    where pc.name = ?
                )
                select
                    bo.bucket_start,
                    time(bo.bucket_start || ':00', '+30 minutes') as bucket_end,
                    count(distinct bo.guid) as order_count,
                    coalesce(sum(distinct_payments.payment_amount), 0) as payment_amount,
                    coalesce(sum(distinct_payments.change_amount), 0) as change_amount,
                    coalesce(sum(distinct_payments.net_sales_amount), 0) as net_sales_amount,
                    coalesce(sum(bowl_counts.bowl_count), 0) as bowl_count
                from bucket_orders bo
                left join (
                    select
                        order_guid,
                        sum(amount) as payment_amount,
                        sum(change_amount) as change_amount,
                        sum(amount - change_amount) as net_sales_amount
                    from order_payments
                    group by order_guid
                ) distinct_payments on distinct_payments.order_guid = bo.guid
                left join (
                    select order_guid, sum(quantity) as bowl_count
                    from order_products
                    where id in (select product_id from bowl_products)
                    group by order_guid
                ) bowl_counts on bowl_counts.order_guid = bo.guid
                group by bo.bucket_start
                order by bo.bucket_start
                """,
                (business_date, BOWL_CATEGORY_NAME),
            )
        ], business_date)

        month_summary = row_to_dict(
            conn.execute(
                """
                with month_orders as (
                    select guid, business_date
                    from orders
                    where business_date >= ?
                      and business_date <= ?
                      and status = 4
                ),
                bowl_products as (
                    select distinct pci.product_id
                    from product_category_items pci
                    join product_categories pc
                      on pc.id = pci.product_category_id
                     and pc.store_guid = pci.store_guid
                    where pc.name = ?
                ),
                month_numbers as (
                    select
                        coalesce((select count(distinct business_date) from month_orders), 0) as business_days,
                        coalesce((select sum(quantity) from order_products where order_guid in (select guid from month_orders) and id in (select product_id from bowl_products)), 0) as month_bowls,
                        coalesce((select sum(amount) from order_payments op join month_orders mo on mo.guid = op.order_guid), 0) as month_payment_amount,
                        coalesce((select sum(change_amount) from order_payments op join month_orders mo on mo.guid = op.order_guid), 0) as month_change_amount,
                        coalesce((select sum(amount - change_amount) from order_payments op join month_orders mo on mo.guid = op.order_guid), 0) as month_revenue
                )
                select
                    substr(?, 1, 7) as month,
                    cast(julianday(?) - julianday(?) + 1 as integer) as calendar_days,
                    business_days,
                    month_bowls,
                    month_revenue
                from month_numbers
                """,
                (month_start, business_date, BOWL_CATEGORY_NAME, business_date, business_date, month_start),
            ).fetchone()
        )

        calendar_days = int(month_summary.get("calendar_days") or 0)
        business_days = int(month_summary.get("business_days") or 0)
        month_bowls = as_number(month_summary.get("month_bowls"))
        month_revenue = as_number(month_summary.get("month_revenue"))
        month_summary.update(
            {
                "avg_bowls_calendar": round(month_bowls / calendar_days, 2) if calendar_days else 0,
                "avg_revenue_calendar": round(month_revenue / calendar_days, 0) if calendar_days else 0,
                "avg_bowls_business": round(month_bowls / business_days, 2) if business_days else 0,
                "avg_revenue_business": round(month_revenue / business_days, 0) if business_days else 0,
            }
        )

        sync_state = row_to_dict(conn.execute("select * from sync_state where source = 'kiosk'").fetchone())
        generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "generated_at": generated_at,
            "source": "sqlite_cache",
            "database": str(DB_PATH),
            "summary": summary,
            "month_summary": month_summary,
            "payments": payments,
            "latest_orders": latest_orders,
            "sales_buckets": sales_buckets,
            "sync_state": sync_state,
        }
    finally:
        conn.close()


def query_cache_status():
    if not DB_PATH.exists():
        return {
            "ok": False,
            "error": f"Sales cache not found: {DB_PATH}",
            "database": str(DB_PATH),
        }

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        sync_state = row_to_dict(conn.execute("select * from sync_state where source = 'kiosk'").fetchone())
        counts = row_to_dict(
            conn.execute(
                """
                select
                    (select count(*) from orders) as order_rows,
                    (select count(*) from order_products) as order_product_rows,
                    (select count(*) from order_payments) as order_payment_rows,
                    (select max(timestamp) from orders) as local_latest_order_time
                """
            ).fetchone()
        )
        return {
            "ok": True,
            "database": str(DB_PATH),
            "sync_state": sync_state,
            "counts": counts,
        }
    finally:
        conn.close()


def main():
    force_utf8_stdio()
    parser = argparse.ArgumentParser(description="Query local sales SQLite cache.")
    parser.add_argument("date", nargs="?", help="Business date, yyyy-mm-dd.")
    parser.add_argument("--status", action="store_true", help="Return cache sync status.")
    args = parser.parse_args()
    if args.status:
        result = query_cache_status()
    else:
        if not args.date:
            parser.error("date is required unless --status is used")
        result = query_snapshot(args.date)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
