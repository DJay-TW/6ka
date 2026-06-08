import argparse
import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def force_utf8_stdio():
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def load_dotenv(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def sync_sales_cache(days=None):
    import sales_cache_sync_worker

    sales_cache_sync_worker.sync_once(days_override=days)


def main():
    force_utf8_stdio()
    load_dotenv(BASE_DIR / ".env")

    parser = argparse.ArgumentParser(
        description="Sync kiosk sales data into local SQLite through the Tailscale HTTP KioskAgent."
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Not supported over the daily HTTP sync path. Use a dedicated maintenance export if historical backfill is needed.",
    )
    parser.add_argument("--days", type=int, help="Override Agent incremental days for this run, capped by the Agent.")
    args = parser.parse_args()

    if args.full:
        raise SystemExit(
            "--full is no longer allowed in the daily sync entrypoint. "
            "Add or use a maintenance HTTP export/backfill endpoint for historical rebuilds."
        )

    sync_sales_cache(days=args.days)


if __name__ == "__main__":
    main()
