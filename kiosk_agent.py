import datetime as dt
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


VERSION = "0.1.0"
BASE_DIR = Path(__file__).resolve().parent
STATE_DB_PATH = Path(os.getenv("KIOSK_AGENT_STATE_DB", BASE_DIR / "kiosk_agent_state.sqlite"))
HOST = os.getenv("KIOSK_AGENT_HOST", "0.0.0.0")
PORT = int(os.getenv("KIOSK_AGENT_PORT", "3010"))
SQL_INSTANCE = os.getenv("KIOSK_SQL_INSTANCE", r"localhost\SQLEXPRESS")
SQL_DATABASE = os.getenv("KIOSK_SQL_DATABASE", "SuitRepository")
STARTED_AT = dt.datetime.now().astimezone()


def force_utf8_stdio():
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def iso_now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def init_state_db():
    STATE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(STATE_DB_PATH) as conn:
        conn.execute(
            """
            create table if not exists sync_state (
                source text primary key,
                running integer not null default 0,
                last_success_at text,
                last_error_at text,
                last_error text,
                source_latest_order_time text,
                last_exported_order_time text
            )
            """
        )
        conn.execute(
            """
            insert into sync_state (source)
            values ('kiosk')
            on conflict(source) do nothing
            """
        )


def read_sync_state():
    init_state_db()
    with sqlite3.connect(STATE_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("select * from sync_state where source = 'kiosk'").fetchone()
        return dict(row) if row else {}


def run_local_sql_json(sql, timeout=15):
    ps_script = rf"""
$ErrorActionPreference = 'Stop'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

Add-Type -AssemblyName System.Data
$conn = New-Object System.Data.SqlClient.SqlConnection(
    "Server={SQL_INSTANCE};Database={SQL_DATABASE};Integrated Security=True;Connection Timeout=5;"
)
$conn.Open()
try {{
    $cmd = $conn.CreateCommand()
    $cmd.CommandTimeout = 10
    $cmd.CommandText = {ps_quote(sql)}
    $reader = $cmd.ExecuteReader()
    $rows = @()
    while ($reader.Read()) {{
        $row = [ordered]@{{}}
        for ($i = 0; $i -lt $reader.FieldCount; $i++) {{
            $name = $reader.GetName($i)
            $value = $reader.GetValue($i)
            if ($value -is [DBNull]) {{
                $row[$name] = $null
            }} elseif ($value -is [DateTime]) {{
                $row[$name] = $value.ToString("yyyy-MM-dd HH:mm:ss")
            }} else {{
                $row[$name] = $value
            }}
        }}
        $rows += [pscustomobject]$row
    }}
    @($rows) | ConvertTo-Json -Depth 6
}} finally {{
    $conn.Close()
}}
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip())
    text = completed.stdout.strip()
    if not text:
        return []
    parsed = json.loads(text)
    return parsed if isinstance(parsed, list) else [parsed]


def database_status():
    started = time.time()
    try:
        rows = run_local_sql_json(
            """
            select
                convert(varchar(10), max(BusinessDate), 120) as max_business_date,
                convert(varchar(19), max(Timestamp), 120) as latest_order_time,
                count(*) as order_rows
            from dbo.[Order];
            """
        )
        row = rows[0] if rows else {}
        return {
            "ok": True,
            "instance": SQL_INSTANCE,
            "database": SQL_DATABASE,
            "latency_ms": round((time.time() - started) * 1000),
            "latest_order_time": row.get("latest_order_time"),
            "max_business_date": row.get("max_business_date"),
            "order_rows": row.get("order_rows"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "instance": SQL_INSTANCE,
            "database": SQL_DATABASE,
            "latency_ms": round((time.time() - started) * 1000),
            "error": str(exc),
        }


def health_payload():
    now = dt.datetime.now().astimezone()
    return {
        "ok": True,
        "service": "6ka-kiosk-agent",
        "version": VERSION,
        "host": socket.gethostname(),
        "started_at": STARTED_AT.isoformat(timespec="seconds"),
        "uptime_seconds": round((now - STARTED_AT).total_seconds()),
        "time": now.isoformat(timespec="seconds"),
    }


def status_payload():
    health = health_payload()
    sync = read_sync_state()
    db = database_status()
    if db.get("ok") and db.get("latest_order_time"):
        sync["source_latest_order_time"] = db["latest_order_time"]

    return {
        "ok": bool(db.get("ok")),
        "agent": {
            "online": True,
            "version": VERSION,
            "host": health["host"],
            "started_at": health["started_at"],
            "uptime_seconds": health["uptime_seconds"],
        },
        "database": db,
        "sync": {
            "running": bool(sync.get("running")),
            "last_success_at": sync.get("last_success_at"),
            "last_error_at": sync.get("last_error_at"),
            "last_error": sync.get("last_error"),
            "source_latest_order_time": sync.get("source_latest_order_time"),
            "last_exported_order_time": sync.get("last_exported_order_time"),
        },
    }


def send_json(handler, status_code, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class KioskAgentHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            send_json(self, 200, health_payload())
            return
        if path == "/api/status":
            payload = status_payload()
            send_json(self, 200 if payload["agent"]["online"] else 503, payload)
            return
        send_json(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/sync/run":
            send_json(
                self,
                202,
                {
                    "ok": True,
                    "accepted": True,
                    "job_id": dt.datetime.now().strftime("%Y%m%d-%H%M%S"),
                    "message": "sync worker not implemented yet",
                },
            )
            return
        send_json(self, 404, {"ok": False, "error": "not found"})

    def log_message(self, fmt, *args):
        print("[%s] %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), fmt % args))


def main():
    force_utf8_stdio()
    init_state_db()
    server = ThreadingHTTPServer((HOST, PORT), KioskAgentHandler)
    print(f"6ka-kiosk-agent {VERSION} listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
