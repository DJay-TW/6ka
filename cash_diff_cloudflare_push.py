import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = Path(os.getenv("CASH_FINANCE_SYNC_STATE_PATH", BASE_DIR / "data" / "finance_cache" / "sync_state.json"))
DEFAULT_API_URL = "https://6ka-cash-diff-api.jay-fbf.workers.dev/api/cash/current"
TOKEN_PATH = BASE_DIR / "cash_diff_push_token.local.txt"
API_URL = os.getenv("CASH_DIFF_CLOUD_API_URL", DEFAULT_API_URL)
API_TOKEN = os.getenv("CASH_DIFF_CLOUD_API_TOKEN", "")


def force_utf8_stdio():
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def build_payload(state):
    summary = state.get("summary") or {}
    denominations = {}
    for value, row in (summary.get("usable_by_denomination") or {}).items():
        denominations[str(value)] = {
            "quantity": row.get("quantity"),
            "total_amount": row.get("total_amount"),
            "type": row.get("type"),
            "target": row.get("target"),
            "latest_time": row.get("latest_time"),
        }
    return {
        "source": "6kaweb",
        "latest_id": summary.get("latest_id") or state.get("agent_latest_id"),
        "latest_cash_record_time": summary.get("latest_cash_record_time"),
        "usable_total_amount": summary.get("usable_total_amount") or 0,
        "denominations": denominations,
        "cashbox": summary.get("cashbox") or {},
    }


def read_local_token():
    if not TOKEN_PATH.exists():
        return ""
    return TOKEN_PATH.read_text(encoding="utf-8").strip()


def resolved_token(token=API_TOKEN):
    return token or read_local_token()


def has_push_credentials(api_url=API_URL, token=API_TOKEN):
    return bool(api_url and resolved_token(token))


def push_payload(payload, api_url=API_URL, token=API_TOKEN):
    if not api_url:
        raise RuntimeError("CASH_DIFF_CLOUD_API_URL is not set")
    token = resolved_token(token)
    if not token:
        raise RuntimeError("CASH_DIFF_CLOUD_API_TOKEN is not set and cash_diff_push_token.local.txt is missing")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=body,
        method="PUT",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    force_utf8_stdio()
    parser = argparse.ArgumentParser(description="Push local cash finance summary to Cloudflare Worker KV.")
    parser.add_argument("--state", default=str(STATE_PATH), help="Path to cash finance sync_state.json.")
    parser.add_argument("--dry-run", action="store_true", help="Print payload without sending it.")
    args = parser.parse_args()

    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    payload = build_payload(state)
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    result = push_payload(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
