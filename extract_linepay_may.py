import csv
import json
import sqlite3
from pathlib import Path

DB = r"C:\6KAweb\data\sales_cache.sqlite"
OUT_DIR = Path(r"C:\Users\88698\Documents\6KA系統開發\outputs\linepay_may_2026")
OUT_DIR.mkdir(parents=True, exist_ok=True)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

payment_types = [dict(r) for r in con.execute("select * from payment_types order by name")]

detail_sql = """
select
    o.business_date,
    o.order_time,
    o.timestamp as order_updated_at,
    o.id as order_id,
    o.display_id,
    o.guid as order_guid,
    o.status as order_status,
    o.total_amount as order_total_amount,
    p.guid as payment_guid,
    p.timestamp as payment_timestamp,
    pt.id as payment_type_id,
    pt.name as payment_type,
    pt.type as payment_type_code,
    p.amount as gross_amount,
    p.redeem_amount,
    p.change_amount,
    (p.amount - p.change_amount) as net_amount,
    p.kiosk_guid,
    p.store_guid
from order_payments p
join payment_types pt on pt.id = p.payment_type_id
join orders o on o.guid = p.order_guid and o.store_guid = p.store_guid
where o.business_date >= ? and o.business_date <= ?
  and lower(pt.name) like ?
order by o.business_date, o.order_time, o.id, p.guid
"""

details = [dict(r) for r in con.execute(detail_sql, ("2026-05-01", "2026-05-31", "%line%"))]

daily_sql = """
select
    o.business_date,
    count(*) as transaction_count,
    sum(p.amount) as gross_amount,
    sum(p.redeem_amount) as redeem_amount,
    sum(p.change_amount) as change_amount,
    sum(p.amount - p.change_amount) as net_amount,
    min(o.order_time) as first_order_time,
    max(o.order_time) as last_order_time
from order_payments p
join payment_types pt on pt.id = p.payment_type_id
join orders o on o.guid = p.order_guid and o.store_guid = p.store_guid
where o.business_date >= ? and o.business_date <= ?
  and lower(pt.name) like ?
group by o.business_date
order by o.business_date
"""
daily = [dict(r) for r in con.execute(daily_sql, ("2026-05-01", "2026-05-31", "%line%"))]

total = {
    "period_start": "2026-05-01",
    "period_end": "2026-05-31",
    "linepay_transaction_count": len(details),
    "linepay_gross_amount": sum(float(r["gross_amount"] or 0) for r in details),
    "linepay_redeem_amount": sum(float(r["redeem_amount"] or 0) for r in details),
    "linepay_change_amount": sum(float(r["change_amount"] or 0) for r in details),
    "linepay_net_amount": sum(float(r["net_amount"] or 0) for r in details),
    "first_order_time": details[0]["order_time"] if details else None,
    "last_order_time": details[-1]["order_time"] if details else None,
}

meta = {
    "source_db": DB,
    "payment_types": payment_types,
    "available_connection_fields_note": (
        "sales_cache.sqlite contains order/payment ids, payment type, amounts, kiosk/store guids, "
        "and timestamps. It does not contain LINE Pay gateway request/response code, LINE transaction id, "
        "merchant transaction id, authorization code, settlement id, or network connection logs."
    ),
    "total": total,
}

def write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

write_csv(OUT_DIR / "linepay_2026-05_details.csv", details)
write_csv(OUT_DIR / "linepay_2026-05_daily_summary.csv", daily)
(OUT_DIR / "linepay_2026-05_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

print(json.dumps({"total": total, "daily_rows": len(daily), "detail_rows": len(details), "out_dir": str(OUT_DIR)}, ensure_ascii=False, indent=2))
