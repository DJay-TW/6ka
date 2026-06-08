import argparse
import csv
import datetime as dt
import json
import sqlite3
from pathlib import Path


DEFAULT_SALES_DB = r"C:\6KAweb\data\sales_cache.sqlite"
DEFAULT_CASH_DB = r"C:\6KAweb\data\finance_cache\cash_finance_cache.sqlite"
DEFAULT_SETTLEMENT_DB = r"C:\6KAweb\data\finance_cache\finance-current.db"
DEFAULT_OUTPUT_DIR = r"C:\6KAweb\data\finance_cache\monthly_audit"
DENOMINATIONS = (1, 5, 10, 50, 100, 200, 500, 1000, 2000)


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def as_int(value):
    return int(round(float(value or 0)))


def money(value):
    return f"{as_int(value):,}"


def month_range(month):
    start = dt.date.fromisoformat(f"{month}-01")
    if start.month == 12:
        end = dt.date(start.year + 1, 1, 1)
    else:
        end = dt.date(start.year, start.month + 1, 1)
    return start, end


def iter_dates(start, end):
    cur = start
    while cur < end:
        yield cur.isoformat()
        cur += dt.timedelta(days=1)


def scalar(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else 0


def table_exists(conn, table_name):
    row = conn.execute(
        "select 1 from sqlite_master where type='table' and name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def cash_columns(table_name):
    if table_name == "cash_records":
        return {
            "id": "id",
            "total_amount": "total_amount",
            "quantity": "quantity",
            "value": "value",
            "type": "type",
            "target": "target",
            "flow_mode": "flow_mode",
            "timestamp": "timestamp",
        }
    return {
        "id": "Id",
        "total_amount": "TotalAmount",
        "quantity": "Quantity",
        "value": "Value",
        "type": "Type",
        "target": "Target",
        "flow_mode": "FlowMode",
        "timestamp": "Timestamp",
    }


def cash_sales_by_date(sales_db, start, end):
    conn = connect(sales_db)
    try:
        rows = conn.execute(
            """
            select
                o.business_date,
                count(*) as cash_orders,
                sum(p.amount) as cash_gross,
                sum(p.change_amount) as cash_change,
                sum(p.amount - p.change_amount) as cash_net,
                min(o.order_time) as first_order_time,
                max(o.order_time) as last_order_time
            from order_payments p
            join orders o
              on o.guid = p.order_guid
             and o.store_guid = p.store_guid
            where o.business_date >= ?
              and o.business_date < ?
              and p.payment_type_id = 'Cash'
            group by o.business_date
            order by o.business_date
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return {row["business_date"]: dict(row) for row in rows}
    finally:
        conn.close()


def empty_flow_row(date_text):
    return {
        "business_date": date_text,
        "target0_in_amount": 0,
        "target0_out_amount": 0,
        "target0_net_change": 0,
        "cashbox_inflow": 0,
        "cashbox_out_amount": 0,
        "cashbox_out_signed": 0,
        "cashbox_out_events": 0,
        "cash_event_rows": 0,
        "cash_record_rows": 0,
        "settlement_detail_rows": 0,
        "first_cash_record_time": None,
        "last_cash_record_time": None,
        "sources": set(),
    }


def add_event_to_flow(target, event):
    date_text = event["timestamp"][:10]
    item = target.setdefault(date_text, empty_flow_row(date_text))
    total_amount = as_int(event["total_amount"])
    target_value = as_int(event["target"])
    flow_mode = as_int(event["flow_mode"])
    if target_value == 0 and total_amount > 0:
        item["target0_in_amount"] += total_amount
    if target_value == 0 and total_amount < 0:
        item["target0_out_amount"] += abs(total_amount)
    if target_value == 0:
        item["target0_net_change"] += total_amount
    if target_value == 1 and flow_mode != 4:
        item["cashbox_inflow"] += total_amount
    if target_value == 1 and flow_mode == 4:
        item["cashbox_out_amount"] += abs(total_amount)
        item["cashbox_out_signed"] += total_amount
        item["cashbox_out_events"] += 1
    item["cash_event_rows"] += 1
    if event["source"] == "cash_records":
        item["cash_record_rows"] += 1
    else:
        item["settlement_detail_rows"] += 1
    first_time = event["timestamp"]
    last_time = event["timestamp"]
    if first_time and (not item["first_cash_record_time"] or first_time < item["first_cash_record_time"]):
        item["first_cash_record_time"] = first_time
    if last_time and (not item["last_cash_record_time"] or last_time > item["last_cash_record_time"]):
        item["last_cash_record_time"] = last_time
    item["sources"].add(event["source"])


def read_cash_events(db_path, table_name, source_name, start, end):
    path = Path(db_path)
    if not path.exists():
        return [], {"exists": False, "table_exists": False, "rows": 0}

    conn = connect(str(path))
    try:
        if not table_exists(conn, table_name):
            return [], {"exists": True, "table_exists": False, "rows": 0}

        col = cash_columns(table_name)
        rows = conn.execute(
            f"""
            select
                {col['id']} as id,
                {col['total_amount']} as total_amount,
                {col['quantity']} as quantity,
                {col['value']} as value,
                {col['type']} as type,
                {col['target']} as target,
                {col['flow_mode']} as flow_mode,
                {col['timestamp']} as timestamp
            from {table_name}
            where {col['timestamp']} >= ?
              and {col['timestamp']} < ?
            order by {col['id']}
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()

        info = conn.execute(
            f"""
            select count(*) as rows,
                   min({col['timestamp']}) as first_time,
                   max({col['timestamp']}) as last_time
            from {table_name}
            """
        ).fetchone()
        events = []
        for row in rows:
            item = dict(row)
            item["source"] = source_name
            events.append(item)
        return events, dict(info)
    finally:
        conn.close()


def cash_flows_by_date(cash_db, settlement_db, start, end):
    events_by_id = {}
    source_info = {}

    rows, info = read_cash_events(cash_db, "cash_records", "cash_records", start, end)
    source_info["cash_records"] = {"path": cash_db, **info}
    for row in rows:
        events_by_id[int(row["id"])] = row

    rows, info = read_cash_events(settlement_db, "SettlementDetail", "SettlementDetail", start, end)
    source_info["SettlementDetail"] = {"path": settlement_db, **info}
    duplicate_ids = 0
    for row in rows:
        event_id = int(row["id"])
        if event_id in events_by_id:
            duplicate_ids += 1
        events_by_id[event_id] = row

    flows = {}
    for event in sorted(events_by_id.values(), key=lambda item: (item["timestamp"], int(item["id"]))):
        add_event_to_flow(flows, event)

    for item in flows.values():
        item["sources"] = ",".join(sorted(item["sources"]))

    source_info["dedupe"] = {
        "key": "Id",
        "duplicate_ids": duplicate_ids,
        "preferred_source": "SettlementDetail",
        "note": "The CashRecord/SettlementDetail boundary can move after settlement; duplicate Ids are counted once.",
    }
    return flows, source_info


def read_usable_events_before(db_path, table_name, source_name, cutoff_date):
    path = Path(db_path)
    if not path.exists():
        return []
    conn = connect(str(path))
    try:
        if not table_exists(conn, table_name):
            return []
        col = cash_columns(table_name)
        placeholders = ",".join("?" for _ in DENOMINATIONS)
        params = [cutoff_date.isoformat(), *DENOMINATIONS]
        rows = conn.execute(
            f"""
            select {col['id']} as id, {col['total_amount']} as total_amount
            from {table_name}
            where {col['timestamp']} < ?
              and {col['target']} = 0
              and {col['value']} in ({placeholders})
            """,
            params,
        )
        return [{"id": int(row["id"]), "total_amount": as_int(row["total_amount"]), "source": source_name} for row in rows]
    finally:
        conn.close()


def usable_total_at(cash_db, settlement_db, cutoff_date):
    events_by_id = {}
    for event in read_usable_events_before(cash_db, "cash_records", "cash_records", cutoff_date):
        events_by_id[event["id"]] = event
    for event in read_usable_events_before(settlement_db, "SettlementDetail", "SettlementDetail", cutoff_date):
        events_by_id[event["id"]] = event
    return sum(event["total_amount"] for event in events_by_id.values())


def available_months_for_table(db_path, table_name):
    path = Path(db_path)
    if not path.exists():
        return []
    conn = connect(str(path))
    try:
        if not table_exists(conn, table_name):
            return []
        col = cash_columns(table_name)
        rows = conn.execute(
            f"""
            select substr({col['timestamp']}, 1, 7) as month,
                   min({col['timestamp']}) as first_time,
                   max({col['timestamp']}) as last_time,
                   count(*) as rows
            from {table_name}
            group by substr({col['timestamp']}, 1, 7)
            order by month
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def available_months(cash_db, settlement_db):
    merged = {}
    for source, db_path, table_name in (
        ("cash_records", cash_db, "cash_records"),
        ("SettlementDetail", settlement_db, "SettlementDetail"),
    ):
        for row in available_months_for_table(db_path, table_name):
            month = row["month"]
            item = merged.setdefault(
                month,
                {"month": month, "first_time": None, "last_time": None, "rows": 0, "sources": set()},
            )
            item["rows"] += as_int(row["rows"])
            if not item["first_time"] or row["first_time"] < item["first_time"]:
                item["first_time"] = row["first_time"]
            if not item["last_time"] or row["last_time"] > item["last_time"]:
                item["last_time"] = row["last_time"]
            item["sources"].add(source)
    result = []
    for item in merged.values():
        item["sources"] = ",".join(sorted(item["sources"]))
        result.append(item)
    return sorted(result, key=lambda item: item["month"])


def build_month_report(sales_db, cash_db, settlement_db, month, actual_removed=None):
    start, end = month_range(month)
    sales = cash_sales_by_date(sales_db, start, end)
    flows, source_info = cash_flows_by_date(cash_db, settlement_db, start, end)
    cash_db_month_rows = sum(as_int(row.get("cash_event_rows")) for row in flows.values())
    cash_db_has_month = cash_db_month_rows > 0
    daily = []

    for date_text in iter_dates(start, end):
        s = sales.get(date_text, {})
        f = flows.get(date_text, {})
        cash_net = as_int(s.get("cash_net"))
        target0_net = as_int(f.get("target0_net_change"))
        target0_in = as_int(f.get("target0_in_amount"))
        target0_out = as_int(f.get("target0_out_amount"))
        cashbox_inflow = as_int(f.get("cashbox_inflow"))
        machine_accounted = target0_net + cashbox_inflow
        pos_vs_machine = cash_net - machine_accounted
        gross_intake_variance = as_int(s.get("cash_gross")) - (target0_in + cashbox_inflow)
        change_payout_variance = as_int(s.get("cash_change")) - target0_out
        cashbox_out = as_int(f.get("cashbox_out_amount"))
        implied_unsettled_cashbox_delta = cashbox_inflow - cashbox_out
        flags = []

        if not cash_db_has_month and s:
            status = "cash_db_unavailable"
        elif not s and not f:
            status = "no_data"
        elif pos_vs_machine == 0:
            status = "balanced"
        elif abs(pos_vs_machine) <= 5:
            status = "minor_rounding"
        else:
            status = "variance"

        if s and not f and cash_db_has_month:
            flags.append("只有 POS 現金資料，沒有 cash DB 流水")
        if s and not cash_db_has_month:
            flags.append("本月找不到 cash DB 流水，無法判斷機器閉環")
        if f and not s:
            flags.append("只有 cash DB 流水，沒有 POS 現金資料")
        if cash_db_has_month and abs(pos_vs_machine) > 5:
            flags.append(f"POS 現金淨收與機器閉環差異 {money(pos_vs_machine)}")
        if cash_db_has_month and abs(gross_intake_variance) > 5:
            flags.append(f"現金投入 gross 對不上 {money(gross_intake_variance)}")
        if cash_db_has_month and abs(change_payout_variance) > 5:
            flags.append(f"找零/退鈔 out 對不上 {money(change_payout_variance)}，可能有退鈔更正或跨日記錄")
        if cashbox_out > 0:
            flags.append(f"出鈔/清帳記錄 {money(cashbox_out)} ({as_int(f.get('cashbox_out_events'))} 次)")
        if implied_unsettled_cashbox_delta > 0:
            flags.append(f"推估仍未出鈔錢箱變化 {money(implied_unsettled_cashbox_delta)}")

        hard_anomaly = cash_db_has_month and any(
            text.startswith("POS 現金淨收")
            or text.startswith("現金投入 gross")
            or text.startswith("找零/退鈔")
            or text.startswith("只有 ")
            for text in flags
        )
        if not cash_db_has_month and s:
            anomaly_level = "insufficient_data"
        else:
            anomaly_level = "anomaly" if hard_anomaly else ("review" if flags else "ok")

        daily.append(
            {
                "business_date": date_text,
                "cash_orders": as_int(s.get("cash_orders")),
                "cash_gross": as_int(s.get("cash_gross")),
                "cash_change": as_int(s.get("cash_change")),
                "cash_net": cash_net,
                "target0_change_recycler_in": target0_in,
                "target0_change_recycler_out": target0_out,
                "target0_change_recycler_net": target0_net,
                "target1_cashbox_inflow": cashbox_inflow,
                "machine_accounted_cash": machine_accounted,
                "pos_vs_machine_variance": pos_vs_machine,
                "gross_intake_variance": gross_intake_variance,
                "change_payout_variance": change_payout_variance,
                "cashbox_out_by_flow4": cashbox_out,
                "cashbox_out_events": as_int(f.get("cashbox_out_events")),
                "implied_unsettled_cashbox_delta": implied_unsettled_cashbox_delta,
                "cash_event_rows": as_int(f.get("cash_event_rows")),
                "cash_record_rows": as_int(f.get("cash_record_rows")),
                "settlement_detail_rows": as_int(f.get("settlement_detail_rows")),
                "cash_sources": f.get("sources"),
                "first_order_time": s.get("first_order_time"),
                "last_order_time": s.get("last_order_time"),
                "first_cash_record_time": f.get("first_cash_record_time"),
                "last_cash_record_time": f.get("last_cash_record_time"),
                "status": status,
                "anomaly_level": anomaly_level,
                "anomaly_flags": "；".join(flags),
            }
        )

    totals = {
        "cash_orders": sum(row["cash_orders"] for row in daily),
        "cash_gross": sum(row["cash_gross"] for row in daily),
        "cash_change": sum(row["cash_change"] for row in daily),
        "cash_net": sum(row["cash_net"] for row in daily),
        "target0_change_recycler_in": sum(row["target0_change_recycler_in"] for row in daily),
        "target0_change_recycler_out": sum(row["target0_change_recycler_out"] for row in daily),
        "target0_change_recycler_net": sum(row["target0_change_recycler_net"] for row in daily),
        "target1_cashbox_inflow": sum(row["target1_cashbox_inflow"] for row in daily),
        "machine_accounted_cash": sum(row["machine_accounted_cash"] for row in daily),
        "pos_vs_machine_variance": sum(row["pos_vs_machine_variance"] for row in daily),
        "gross_intake_variance": sum(row["gross_intake_variance"] for row in daily),
        "change_payout_variance": sum(row["change_payout_variance"] for row in daily),
        "cashbox_out_by_flow4": sum(row["cashbox_out_by_flow4"] for row in daily),
        "cashbox_out_events": sum(row["cashbox_out_events"] for row in daily),
        "implied_unsettled_cashbox_delta": sum(row["implied_unsettled_cashbox_delta"] for row in daily),
        "cash_event_rows": sum(row["cash_event_rows"] for row in daily),
        "cash_record_rows": sum(row["cash_record_rows"] for row in daily),
        "settlement_detail_rows": sum(row["settlement_detail_rows"] for row in daily),
        "anomaly_days": sum(1 for row in daily if row["anomaly_level"] == "anomaly"),
        "review_days": sum(1 for row in daily if row["anomaly_level"] == "review"),
        "insufficient_data_days": sum(1 for row in daily if row["anomaly_level"] == "insufficient_data"),
    }

    start_usable = usable_total_at(cash_db, settlement_db, start)
    end_usable = usable_total_at(cash_db, settlement_db, end)
    totals["period_start_usable_change_balance"] = start_usable
    totals["period_end_usable_change_balance"] = end_usable
    totals["usable_change_balance_delta"] = end_usable - start_usable

    actual = None
    if actual_removed is not None:
        actual = {
            "actual_removed_or_counted_cash": actual_removed,
            "against_cashbox_out_by_flow4": actual_removed - totals["cashbox_out_by_flow4"],
            "note": (
                "If the month is fully settled, actual removed/counted cash should be close to cashbox_out_by_flow4. "
                "If some cash remains unsettled in the machine, compare with cashbox_out_by_flow4 plus the unsettled amount."
            ),
        }

    return {
        "month": month,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": {
            "sales_db": sales_db,
            "cash_db": cash_db,
            "settlement_db": settlement_db,
            "cash_sources": source_info,
            "cash_db_available_months": available_months(cash_db, settlement_db),
        },
        "data_quality": {
            "cash_db_has_month": cash_db_has_month,
            "cash_db_month_rows": cash_db_month_rows,
            "warning": None
            if cash_db_has_month
            else (
                "No cash finance rows exist for this month in cash_records or SettlementDetail. "
                "This report can show POS cash totals, but cannot judge machine-vs-POS cash variance."
            ),
        },
        "logic": {
            "cash_event_source": "SettlementDetail for settled history plus cash_records for current/unsettled records",
            "machine_accounted_cash": "target0_change_recycler_net + target1_cashbox_inflow",
            "pos_vs_machine_variance": "POS cash_net - machine_accounted_cash",
            "cashbox_out_by_flow4": "abs(target=1, flow_mode=4), usually out-cash/settlement clearing",
            "gross_intake_variance": "POS cash_gross - (target0_in + target1_cashbox_inflow)",
            "change_payout_variance": "POS change_amount - target0_out; non-zero can indicate correction/refund timing or manual payout records",
            "important_note": (
                "A balanced POS-vs-machine result means the machine records explain POS cash sales. "
                "It does not prove physical cash was received correctly; compare actual counted/received cash separately."
            ),
        },
        "totals": totals,
        "actual_compare": actual,
        "daily": daily,
    }


def write_outputs(report, output_dir):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = f"cash_month_audit_{report['month']}"
    json_path = out / f"{base}.json"
    csv_path = out / f"{base}_daily.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(report["daily"][0].keys()))
        writer.writeheader()
        writer.writerows(report["daily"])

    return json_path, csv_path


def print_human(report, json_path=None, csv_path=None):
    totals = report["totals"]
    quality = report.get("data_quality") or {}
    print(f"6KA 月現金閉環比對 - {report['month']}")
    print(f"產生時間: {report['generated_at']}")
    if quality.get("warning"):
        print("")
        print("[資料不足警告]")
        print(f"  {quality['warning']}")
        months = report.get("source", {}).get("cash_db_available_months") or []
        if months:
            available = ", ".join(f"{item['month']}({item['sources']})" for item in months)
            print(f"  本機 cash DB 目前可比對月份: {available}")
    print("")
    print("[月合計]")
    print(f"  POS 現金單數: {totals['cash_orders']}")
    print(f"  POS 現金 gross: {money(totals['cash_gross'])}")
    print(f"  POS 找零: {money(totals['cash_change'])}")
    print(f"  POS 現金淨收: {money(totals['cash_net'])}")
    print(f"  cash event rows: {totals['cash_event_rows']} (CashRecord {totals['cash_record_rows']} / SettlementDetail {totals['settlement_detail_rows']})")
    print(f"  找零槽入金 target=0 in: {money(totals['target0_change_recycler_in'])}")
    print(f"  找零槽出金 target=0 out: {money(totals['target0_change_recycler_out'])}")
    print(f"  找零槽淨變化 target=0: {money(totals['target0_change_recycler_net'])}")
    print(f"  進錢箱 target=1 非出鈔: {money(totals['target1_cashbox_inflow'])}")
    print(f"  機器可解釋現金: {money(totals['machine_accounted_cash'])}")
    print(f"  POS vs 機器差異: {money(totals['pos_vs_machine_variance'])}")
    print(f"  gross 投入差異: {money(totals['gross_intake_variance'])}")
    print(f"  找零/退鈔差異: {money(totals['change_payout_variance'])}")
    print(f"  出鈔/清帳 flow_mode=4: {money(totals['cashbox_out_by_flow4'])} ({totals['cashbox_out_events']} 次)")
    print(f"  推估未出鈔錢箱變化: {money(totals['implied_unsettled_cashbox_delta'])}")
    print(f"  期初/期末找零槽: {money(totals['period_start_usable_change_balance'])} / {money(totals['period_end_usable_change_balance'])}")
    print("")
    actual = report.get("actual_compare")
    if actual:
        print("[實際盤點比對]")
        print(f"  實際收到/盤點: {money(actual['actual_removed_or_counted_cash'])}")
        print(f"  實際 - 機器出鈔: {money(actual['against_cashbox_out_by_flow4'])}")
        print("")
    bad_days = [row for row in report["daily"] if row["anomaly_level"] == "anomaly"]
    review_days = [row for row in report["daily"] if row["anomaly_level"] == "review"]
    insufficient_days = [row for row in report["daily"] if row["anomaly_level"] == "insufficient_data"]
    print(
        f"[每日標記] anomaly days: {len(bad_days)} / "
        f"review days: {len(review_days)} / insufficient data days: {len(insufficient_days)}"
    )
    for row in bad_days[:20]:
        print(
            f"  {row['business_date']} POS={money(row['cash_net'])} "
            f"machine={money(row['machine_accounted_cash'])} "
            f"diff={money(row['pos_vs_machine_variance'])} flags={row['anomaly_flags']}"
        )
    if json_path:
        print("")
        print(f"JSON: {json_path}")
    if csv_path:
        print(f"CSV: {csv_path}")
    print("")
    print("注意: POS vs 機器對得上，只代表機器流水能解釋 POS 現金；是否實際短少仍要跟現場盤點/收到金額比對。")


def main():
    parser = argparse.ArgumentParser(description="Compare monthly POS cash sales against cash finance DB flows.")
    parser.add_argument("--month", required=True, help="Month to audit, YYYY-MM.")
    parser.add_argument("--sales-db", default=DEFAULT_SALES_DB)
    parser.add_argument("--cash-db", default=DEFAULT_CASH_DB)
    parser.add_argument("--settlement-db", default=DEFAULT_SETTLEMENT_DB)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--actual-removed", type=int, help="Optional actual cash received/counted for the month.")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout.")
    args = parser.parse_args()

    report = build_month_report(
        args.sales_db,
        args.cash_db,
        args.settlement_db,
        args.month,
        actual_removed=args.actual_removed,
    )
    json_path, csv_path = write_outputs(report, args.output_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report, json_path=json_path, csv_path=csv_path)


if __name__ == "__main__":
    main()
