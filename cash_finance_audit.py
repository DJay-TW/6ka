import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
AGENT_STATUS_URL = os.getenv("CASH_FINANCE_AGENT_STATUS_URL", "http://100.113.224.68:3012/api/finance/status")
AGENT_DB_URL = os.getenv("CASH_FINANCE_AGENT_DB_URL", "http://100.113.224.68:3012/api/finance/db")
REQUEST_TIMEOUT_SECONDS = int(os.getenv("CASH_FINANCE_REQUEST_TIMEOUT_SECONDS", "20"))
SNAPSHOT_DIR = Path(os.getenv("CASH_FINANCE_SNAPSHOT_DIR", BASE_DIR / "data" / "finance_cache" / "snapshots"))
CURRENT_DB = Path(os.getenv("CASH_FINANCE_CURRENT_DB", BASE_DIR / "data" / "finance_cache" / "finance-current.db"))
REPORT_DIR = Path(os.getenv("CASH_FINANCE_REPORT_DIR", BASE_DIR / "data" / "finance_cache" / "reports"))


def force_utf8_stdio():
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def now_slug():
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_snapshot():
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = SNAPSHOT_DIR / f"finance-{now_slug()}.tmp"
    final_path = tmp_path.with_suffix(".db")
    started_at = time.time()
    with urllib.request.urlopen(AGENT_DB_URL, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        with tmp_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    latency_ms = int((time.time() - started_at) * 1000)
    validate_db(tmp_path)
    tmp_path.replace(final_path)
    CURRENT_DB.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(final_path, CURRENT_DB)
    return final_path, latency_ms


def validate_db(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute("pragma quick_check").fetchone()
        if not row or row[0] != "ok":
            raise RuntimeError(f"sqlite quick_check failed: {row[0] if row else 'no result'}")
        for table in ("CashRecord", "Settlement", "SettlementDetail"):
            conn.execute(f'select count(*) from "{table}"').fetchone()
    finally:
        conn.close()


def open_db(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def one(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def all_rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def parse_snapshot(path, agent_status=None, snapshot_latency_ms=None):
    info = Path(path).stat()
    conn = open_db(path)
    try:
        latest_cash = one(conn, 'select * from "CashRecord" order by "Timestamp" desc, "Id" desc limit 1')
        latest_timestamp = latest_cash["Timestamp"] if latest_cash else None

        counts = one(
            conn,
            """
            select
                (select count(*) from "CashRecord") as cash_record_rows,
                (select count(*) from "Settlement") as settlement_rows,
                (select count(*) from "SettlementDetail") as settlement_detail_rows,
                (select min("Timestamp") from "CashRecord") as first_cash_record_time,
                (select max("Timestamp") from "CashRecord") as latest_cash_record_time
            """,
        )

        latest_records = all_rows(
            conn,
            """
            select "Id", "TotalAmount", "Quantity", "Value", "Type", "Target", "FlowMode", "Timestamp"
            from "CashRecord"
            where "Timestamp" = (select max("Timestamp") from "CashRecord")
            order by "Type", "Target", "FlowMode", "Value"
            """,
        )

        latest_totals = all_rows(
            conn,
            """
            select "Type", "Target", "FlowMode",
                   sum("TotalAmount") as total_amount,
                   sum("Quantity") as quantity,
                   count(*) as denomination_rows
            from "CashRecord"
            where "Timestamp" = (select max("Timestamp") from "CashRecord")
            group by "Type", "Target", "FlowMode"
            order by "Type", "Target", "FlowMode"
            """,
        )

        latest_by_type = all_rows(
            conn,
            """
            select "Type", sum("TotalAmount") as total_amount, sum("Quantity") as quantity
            from "CashRecord"
            where "Timestamp" = (select max("Timestamp") from "CashRecord")
            group by "Type"
            order by "Type"
            """,
        )

        running_balances = all_rows(
            conn,
            """
            select "Type", "Target", "FlowMode", "Value",
                   sum("TotalAmount") as total_amount,
                   sum("Quantity") as quantity,
                   count(*) as rows,
                   min("Timestamp") as first_time,
                   max("Timestamp") as latest_time
            from "CashRecord"
            group by "Type", "Target", "FlowMode", "Value"
            order by "Type", "Target", "FlowMode", "Value"
            """,
        )

        running_totals = all_rows(
            conn,
            """
            select "Type", "Target",
                   sum("TotalAmount") as total_amount,
                   sum("Quantity") as quantity,
                   count(*) as rows,
                   max("Timestamp") as latest_time
            from "CashRecord"
            group by "Type", "Target"
            order by "Type", "Target"
            """,
        )

        normal_denomination_balances = all_rows(
            conn,
            """
            select "Type", "Target", "Value",
                   sum("TotalAmount") as total_amount,
                   sum("Quantity") as quantity,
                   count(*) as rows,
                   max("Timestamp") as latest_time
            from "CashRecord"
            where ("Type" = 0 and "Value" in (1, 5, 10, 50))
               or ("Type" = 1 and "Value" in (100, 200, 500, 1000, 2000))
            group by "Type", "Target", "Value"
            order by "Type", "Target", "Value"
            """,
        )

        suspicious_value_rows = all_rows(
            conn,
            """
            select "Type", "Target", "FlowMode", "Value",
                   sum("TotalAmount") as total_amount,
                   sum("Quantity") as quantity,
                   count(*) as rows,
                   max("Timestamp") as latest_time
            from "CashRecord"
            where not (("Type" = 0 and "Value" in (1, 5, 10, 50))
                    or ("Type" = 1 and "Value" in (100, 200, 500, 1000, 2000)))
            group by "Type", "Target", "FlowMode", "Value"
            order by max("Timestamp") desc, "Type", "Target", "Value"
            limit 80
            """,
        )

        latest_settlement = one(
            conn,
            """
            select *
            from "Settlement"
            order by "Timestamp" desc
            limit 1
            """,
        )

        settlement_detail_totals = []
        if latest_settlement:
            settlement_detail_totals = all_rows(
                conn,
                """
                select "Type", "Target", "FlowMode",
                       sum("TotalAmount") as total_amount,
                       sum("Quantity") as quantity,
                       count(*) as detail_rows
                from "SettlementDetail"
                where "SettlementGuid" = ?
                group by "Type", "Target", "FlowMode"
                order by "Type", "Target", "FlowMode"
                """,
                (latest_settlement["Guid"],),
            )

        recent_timestamp_groups = all_rows(
            conn,
            """
            select "Timestamp",
                   count(*) as rows,
                   sum("TotalAmount") as total_amount,
                   min("Id") as first_id,
                   max("Id") as last_id
            from "CashRecord"
            group by "Timestamp"
            order by "Timestamp" desc
            limit 12
            """,
        )

        return {
            "snapshot": {
                "path": str(path),
                "size_bytes": info.st_size,
                "captured_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "download_latency_ms": snapshot_latency_ms,
            },
            "agent_status": agent_status,
            "counts": counts,
            "latest_cash_record_time": latest_timestamp,
            "latest_cash_record_totals": latest_totals,
            "latest_cash_record_by_type": latest_by_type,
            "latest_cash_record_rows": latest_records,
            "running_totals": running_totals,
            "running_balances": running_balances,
            "normal_denomination_balances": normal_denomination_balances,
            "suspicious_value_rows": suspicious_value_rows,
            "latest_settlement": latest_settlement,
            "latest_settlement_detail_totals": settlement_detail_totals,
            "recent_cash_record_timestamp_groups": recent_timestamp_groups,
        }
    finally:
        conn.close()


def print_human(report):
    print(f"snapshot: {report['snapshot']['path']}")
    print(f"db latest CashRecord time: {report.get('latest_cash_record_time')}")
    counts = report.get("counts") or {}
    print(
        "rows: CashRecord={cash_record_rows} Settlement={settlement_rows} SettlementDetail={settlement_detail_rows}".format(
            **counts
        )
    )
    print("")
    print("Latest CashRecord totals by Type/Target/FlowMode:")
    for row in report["latest_cash_record_totals"]:
        print(
            "  Type={Type} Target={Target} FlowMode={FlowMode} total={total_amount} qty={quantity} rows={denomination_rows}".format(
                **row
            )
        )
    print("")
    print("Latest CashRecord denomination rows:")
    for row in report["latest_cash_record_rows"]:
        print(
            "  Type={Type} Target={Target} FlowMode={FlowMode} Value={Value} Qty={Quantity} Total={TotalAmount} Id={Id}".format(
                **row
            )
        )
    settlement = report.get("latest_settlement")
    if settlement:
        print("")
        print(
            "Latest Settlement: time={Timestamp} Initial={Initial} Receive={Receive} Change={Change} CoinRecycle={CoinRecycle} CoinCashBox={CoinCashBox} BanknoteRecycle={BanknoteRecycle} BanknoteCashBox={BanknoteCashBox} BusinessAmount={BusinessAmount}".format(
                **settlement
            )
        )
    print("")
    print("Running totals by Type/Target from all CashRecord rows:")
    for row in report["running_totals"]:
        print(
            "  Type={Type} Target={Target} total={total_amount} qty={quantity} rows={rows} latest={latest_time}".format(
                **row
            )
        )
    print("")
    print("Normal denomination running balances:")
    for row in report["normal_denomination_balances"]:
        print(
            "  Type={Type} Target={Target} Value={Value} Qty={quantity} Total={total_amount} latest={latest_time}".format(
                **row
            )
        )
    if report["suspicious_value_rows"]:
        print("")
        print("Non-standard Value rows, likely batch/cashbox events:")
        for row in report["suspicious_value_rows"][:20]:
            print(
                "  Type={Type} Target={Target} FlowMode={FlowMode} Value={Value} Qty={quantity} Total={total_amount} latest={latest_time}".format(
                    **row
                )
            )


def save_report(report):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"finance-audit-{now_slug()}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main():
    force_utf8_stdio()
    parser = argparse.ArgumentParser(description="Fetch or inspect kiosk finance.db snapshots for cash amount mismatch audits.")
    parser.add_argument("--fetch", action="store_true", help="Download a fresh finance.db snapshot from the CashFinanceAgent.")
    parser.add_argument("--db", default=None, help="Inspect an existing finance.db path instead of the current cache.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of the human summary.")
    args = parser.parse_args()

    agent_status = None
    latency_ms = None
    if args.fetch:
        agent_status = fetch_json(AGENT_STATUS_URL)
        db_path, latency_ms = fetch_snapshot()
    elif args.db:
        db_path = Path(args.db)
    else:
        db_path = CURRENT_DB

    report = parse_snapshot(db_path, agent_status=agent_status, snapshot_latency_ms=latency_ms)
    report_path = save_report(report)
    report["report_path"] = str(report_path)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)
        print("")
        print(f"report: {report_path}")


if __name__ == "__main__":
    main()
