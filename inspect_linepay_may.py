import json
import sqlite3

DB = r"C:\6KAweb\data\sales_cache.sqlite"

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

tables = [r[0] for r in con.execute("select name from sqlite_master where type=? order by name", ("table",))]
print("tables")
print(json.dumps(tables, ensure_ascii=False, indent=2))

for table in tables:
    schema = [dict(r) for r in con.execute(f"pragma table_info({table})")]
    print(f"schema:{table}")
    print(json.dumps(schema, ensure_ascii=False, indent=2))

print("payment type counts in May")
try:
    rows = [dict(r) for r in con.execute(
        """
        select payment_type, count(*) as rows, sum(amount) as amount, sum(change_amount) as change_amount
        from order_payments
        where timestamp >= ? and timestamp < ?
        group by payment_type
        order by rows desc
        """,
        ("2026-05-01", "2026-06-01"),
    )]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
except Exception as exc:
    print(f"payment type query failed: {exc}")

print("linepay sample")
try:
    rows = [dict(r) for r in con.execute(
        """
        select *
        from order_payments
        where timestamp >= ? and timestamp < ?
          and lower(payment_type) like '%line%'
        order by timestamp, id
        limit 5
        """,
        ("2026-05-01", "2026-06-01"),
    )]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
except Exception as exc:
    print(f"linepay sample failed: {exc}")
