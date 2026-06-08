import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import parse, request, error


DISCORD_API_BASE = "https://discord.com/api/v10"
DEFAULT_ENV_FILES = [
    Path(".env"),
    Path(r"C:\6KAweb\.env"),
    Path(r"C:\RP\.env"),
]
DEFAULT_WEBHOOK_SOURCE = Path("switchbot_temp_monitor.pi.py")


def load_dotenv(path):
    if not path or not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_iso_datetime(value):
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc)


def parse_discord_timestamp(value):
    return parse_iso_datetime(value)


def snowflake_time_ms(snowflake):
    return ((int(snowflake) >> 22) + 1420070400000)


def find_webhook_url(source_path):
    if not source_path or not source_path.exists():
        return None
    text = source_path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r'WEBHOOK_URL\s*=\s*["\'](https://discord(?:app)?\.com/api/webhooks/[^"\']+)["\']', text)
    return match.group(1) if match else None


def resolve_webhook_url(args):
    if args.webhook_url:
        return args.webhook_url
    for key in args.webhook_url_env:
        value = os.getenv(key)
        if value:
            return value
    return find_webhook_url(args.webhook_source)


def parse_webhook_url(webhook_url):
    parsed = parse.urlparse(webhook_url)
    parts = [part for part in parsed.path.split("/") if part]
    try:
        index = parts.index("webhooks")
        webhook_id = parts[index + 1]
        token = parts[index + 2]
    except (ValueError, IndexError):
        raise ValueError("invalid Discord webhook URL")
    return webhook_id, token


def discord_request(method, url, bot_token=None, payload=None):
    data = None
    headers = {
        "User-Agent": "6KA-webhook-message-cleanup/1.0",
    }
    if bot_token:
        headers["Authorization"] = f"Bot {bot_token}"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    while True:
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=20) as response:
                body = response.read()
                if not body:
                    return None
                return json.loads(body.decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429:
                retry_after = 1.0
                try:
                    retry_after = float(json.loads(body).get("retry_after", retry_after))
                except Exception:
                    pass
                time.sleep(retry_after + 0.25)
                continue
            raise RuntimeError(f"Discord HTTP {exc.code}: {body[:300]}") from exc


def get_webhook_info(webhook_url):
    webhook_id, _token = parse_webhook_url(webhook_url)
    return discord_request("GET", f"{DISCORD_API_BASE}/webhooks/{webhook_id}/{_token}")


def fetch_messages(channel_id, bot_token, before_id=None, limit=100):
    params = {"limit": str(min(100, max(1, limit)))}
    if before_id:
        params["before"] = str(before_id)
    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages?{parse.urlencode(params)}"
    return discord_request("GET", url, bot_token=bot_token) or []


def delete_message(channel_id, message_id, bot_token):
    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}"
    discord_request("DELETE", url, bot_token=bot_token)


def preview_content(content):
    text = " ".join((content or "").split())
    if len(text) > 80:
        return text[:77] + "..."
    return text


def collect_target_messages(args, channel_id, webhook_id, bot_token):
    matched = []
    scanned = 0
    before_id = args.before_message_id
    after_dt = parse_iso_datetime(args.after)
    before_dt = parse_iso_datetime(args.before)
    scan_limit = args.scan_limit

    while True:
        remaining = 100 if scan_limit <= 0 else min(100, scan_limit - scanned)
        if remaining <= 0:
            break

        messages = fetch_messages(channel_id, bot_token, before_id=before_id, limit=remaining)
        if not messages:
            break

        stop_for_after = False
        for message in messages:
            scanned += 1
            before_id = message["id"]
            ts = parse_discord_timestamp(message.get("timestamp"))

            if after_dt and ts and ts < after_dt:
                stop_for_after = True
                break
            if before_dt and ts and ts >= before_dt:
                continue
            if args.content_contains and args.content_contains not in (message.get("content") or ""):
                continue
            if str(message.get("webhook_id") or "") == str(webhook_id):
                matched.append(message)

        if stop_for_after:
            break
        if len(messages) < remaining:
            break

    return scanned, matched


def summarize(scanned, matched, webhook_id, channel_id):
    timestamps = [parse_discord_timestamp(message.get("timestamp")) for message in matched]
    timestamps = [ts for ts in timestamps if ts]
    summary = {
        "scanned": scanned,
        "matched": len(matched),
        "webhook_id": str(webhook_id),
        "channel_id": str(channel_id),
        "newest": max(timestamps).isoformat() if timestamps else None,
        "oldest": min(timestamps).isoformat() if timestamps else None,
    }
    return summary


def print_summary(scanned, matched, webhook_id, channel_id, args):
    mode = "delete" if args.delete else "dry-run"
    summary = summarize(scanned, matched, webhook_id, channel_id)
    print(f"mode: {mode}")
    print(f"channel_id: {summary['channel_id']}")
    print(f"webhook_id: {summary['webhook_id']}")
    print(f"scanned: {summary['scanned']}")
    print(f"matched: {summary['matched']}")
    print(f"newest: {summary['newest'] or '-'}")
    print(f"oldest: {summary['oldest'] or '-'}")
    if matched:
        print("sample:")
        for message in matched[: min(args.sample, len(matched))]:
            print(f"- {message.get('timestamp')} id={message.get('id')} {preview_content(message.get('content'))}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Dry-run or delete Discord messages created by one webhook. Defaults to the SwitchBot temperature webhook.",
    )
    parser.add_argument("--env-file", action="append", type=Path, default=[], help="Additional .env file to load.")
    parser.add_argument("--bot-token-env", default="DISCORD_BOT_TOKEN", help="Environment variable containing the Discord bot token.")
    parser.add_argument(
        "--webhook-url-env",
        action="append",
        default=["TEMPERATURE_DISCORD_WEBHOOK_URL", "SWITCHBOT_TEMP_WEBHOOK_URL", "WEBHOOK_URL"],
        help="Environment variable containing the target webhook URL. Can be repeated.",
    )
    parser.add_argument("--webhook-url", help="Target webhook URL. Prefer env files instead of passing secrets on the command line.")
    parser.add_argument("--webhook-source", type=Path, default=DEFAULT_WEBHOOK_SOURCE, help="Python file to scan for WEBHOOK_URL fallback.")
    parser.add_argument("--channel-id", help="Channel id. If omitted, the webhook info API is used.")
    parser.add_argument("--scan-limit", type=int, default=1000, help="Maximum messages to scan from newest to oldest. Use 0 for no fixed limit.")
    parser.add_argument("--before-message-id", help="Start scanning before this Discord message id.")
    parser.add_argument("--after", help="Only consider messages at or after this ISO datetime.")
    parser.add_argument("--before", help="Only consider messages before this ISO datetime.")
    parser.add_argument("--content-contains", help="Extra safety filter: only match messages containing this text.")
    parser.add_argument("--sample", type=int, default=10, help="Number of matched messages to print as a sample.")
    parser.add_argument("--delay", type=float, default=0.35, help="Delay between deletes in seconds.")
    parser.add_argument("--delete", action="store_true", help="Actually delete matched messages. Omit for dry-run.")
    parser.add_argument("--yes", action="store_true", help="Required together with --delete.")
    return parser


def main():
    args = build_parser().parse_args()

    for env_file in [*DEFAULT_ENV_FILES, *args.env_file]:
        load_dotenv(env_file)

    bot_token = os.getenv(args.bot_token_env)
    if not bot_token:
        print(f"missing bot token: set {args.bot_token_env} in .env or the environment", file=sys.stderr)
        return 2

    webhook_url = resolve_webhook_url(args)
    if not webhook_url:
        print("missing webhook URL: set TEMPERATURE_DISCORD_WEBHOOK_URL or keep WEBHOOK_URL in the source file", file=sys.stderr)
        return 2

    webhook_id, _token = parse_webhook_url(webhook_url)
    channel_id = args.channel_id
    if not channel_id:
        webhook_info = get_webhook_info(webhook_url)
        channel_id = webhook_info.get("channel_id")
    if not channel_id:
        print("missing channel id: webhook info did not return channel_id", file=sys.stderr)
        return 2

    scanned, matched = collect_target_messages(args, channel_id, webhook_id, bot_token)
    print_summary(scanned, matched, webhook_id, channel_id, args)

    if not args.delete:
        return 0
    if not args.yes:
        print("refusing to delete without --yes", file=sys.stderr)
        return 2

    deleted = 0
    failed = 0
    for message in matched:
        message_id = message["id"]
        try:
            delete_message(channel_id, message_id, bot_token)
            deleted += 1
            print(f"deleted: {message_id} {message.get('timestamp')}")
            if args.delay > 0:
                time.sleep(args.delay)
        except Exception as exc:
            failed += 1
            print(f"delete failed: {message_id} {exc}", file=sys.stderr)

    print(f"delete_result: deleted={deleted} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
