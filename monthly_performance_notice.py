"""Send one monthly SGA Coach performance-report reminder through Discord.

All message content and the webhook token come from encrypted GitHub Actions
secrets. Never put either one in this public source file.
"""

from __future__ import annotations

import calendar
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib import error, parse, request
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("Asia/Seoul")
STATE_FILE = Path(__file__).with_name("monthly_performance_notice_state.json")
MAX_LENGTH = 2000


def report_monday(year: int, month: int) -> date:
    """Latest Monday whose following Saturday and Sunday are in this month."""
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    monday = last_day - timedelta(days=last_day.weekday())
    if (monday + timedelta(days=6)).month != month:
        monday -= timedelta(days=7)
    return monday


def next_report_monday(today: date) -> date:
    candidate = report_monday(today.year, today.month)
    if today <= candidate:
        return candidate
    year = today.year + (today.month == 12)
    month = 1 if today.month == 12 else today.month + 1
    return report_monday(year, month)


def load_state() -> dict:
    if not STATE_FILE.exists():
        raise RuntimeError("Missing monthly_performance_notice_state.json")
    value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("Invalid monthly notice state")
    return value


def normalized_webhook(value: str) -> str:
    url = parse.urlsplit(value.strip())
    if url.scheme != "https" or url.hostname not in {"discord.com", "discordapp.com"}:
        raise RuntimeError("Invalid Discord webhook domain")
    if not url.path.startswith("/api/webhooks/") and not url.path.startswith("/api/v10/webhooks/"):
        raise RuntimeError("Invalid Discord webhook path")
    return "https://discord.com" + url.path


def post_discord(webhook: str, content: str, role_id: str) -> str:
    payload = json.dumps(
        {"content": content, "allowed_mentions": {"parse": [], "roles": [role_id]}},
        ensure_ascii=False,
    ).encode("utf-8")
    req = request.Request(
        normalized_webhook(webhook) + "?wait=true",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "SGA-Monthly-Performance-Notice/1.0"},
    )
    try:
        with request.urlopen(req, timeout=25) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise RuntimeError(f"Discord rejected reminder (HTTP {exc.code})") from None
    except (error.URLError, TimeoutError):
        # Do not print the URL: it contains the webhook token.
        raise RuntimeError("Discord network request failed") from None
    message_id = str(result.get("id", ""))
    if not message_id:
        raise RuntimeError("Discord did not confirm the posted reminder")
    return message_id


def run() -> None:
    now = datetime.now(TIMEZONE)
    today = now.date()
    due = report_monday(today.year, today.month)
    template = os.environ.get("PERFORMANCE_NOTICE_TEMPLATE", "")
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")

    if not webhook or not template:
        raise RuntimeError("Missing webhook or notice template secret")

    normalized_webhook(webhook)

    preview_only = os.environ.get(
        "PREVIEW_ONLY", "false"
    ).lower() == "true"

    if not preview_only and (
        today != due or now.hour < 9 or now.hour >= 18
    ):
        print("Not scheduled reminder day/time")
        return

    send_date = next_report_monday(today) if preview_only else due

    deadline = date(
        send_date.year,
        send_date.month,
        calendar.monthrange(send_date.year, send_date.month)[1]
    )

    weekday = ("월", "화", "수", "목", "금", "토", "일")[deadline.weekday()]

    try:
        content = template.format(
            year=deadline.year,
            month=deadline.month,
            day=deadline.day,
            weekday=weekday
        )
    except (KeyError, ValueError, IndexError):
        raise RuntimeError("Invalid notice template placeholders") from None

    if not 1 <= len(content) <= MAX_LENGTH:
        raise RuntimeError("Invalid notice length")

    parts = content.strip().split(maxsplit=1)
    role_id = parts[0]
    body = parts[1] if len(parts) == 2 else ""

    if not (
        role_id.isascii()
        and role_id.isdigit()
        and 16 <= len(role_id) <= 22
    ):
        raise RuntimeError("Invalid SGA COACH role ID")

    content = f"<@&{role_id}> {body.lstrip()}"

    if len(content) > MAX_LENGTH:
        raise RuntimeError("Notice exceeds Discord message limit")

    if preview_only:
        print("PREVIEW ONLY - no Discord message sent.")
        print(f"Next send date: {send_date.isoformat()} (Mon), 09:13 KST")
        print(f"Deadline: {deadline.isoformat()} ({weekday}), 18:00 KST")
        print("Webhook, template and role mention configured.")
        return

    state = load_state()

    if state.get("last_sent_date") == today.isoformat():
        print("Already posted; not posting a duplicate")
        return

    message_id = post_discord(webhook, content, role_id)

    state["last_sent_date"] = today.isoformat()
    state["last_message_id"] = message_id

    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8"
    )

    print(f"Monthly performance reminder posted for {today.isoformat()}")




if __name__ == "__main__":
    try:
        run()
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"Monthly performance notice error: {exc}")
        raise SystemExit(1) from None
