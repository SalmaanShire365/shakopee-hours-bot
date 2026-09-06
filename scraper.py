#!/usr/bin/env python3
"""Shakopee Community Center daily-hours Discord bot.

Fetches the official Community Center page, determines today's hours with
special/holiday overrides taking priority over the normal weekly schedule,
compares the result with the last successful run, and posts a Discord embed
via an incoming webhook.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser


try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass



URL = "https://www.shakopeemn.gov/recreation/community_center/index.php"
TZ = ZoneInfo("America/Chicago")
STATE_PATH = Path(os.getenv("STATE_PATH", "state.json"))
SEND_MODE = os.getenv("SEND_MODE", "daily").strip().lower()  # daily | changes_only
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
USER_AGENT = os.getenv(
    "USER_AGENT",
    "ShakopeeCommunityCenterHoursBot/1.0 (+personal notification bot)",
)

DISCORD_USERNAME = os.getenv("DISCORD_USERNAME", "Shakopee Hours Bot")
DISCORD_AVATAR_URL = os.getenv("DISCORD_AVATAR_URL", "").strip() or None
# Optional: "@here", "@everyone", or a role mention like "<@&123456789012345678>".
# Only used when hours are modified/closed (never on an ordinary normal day).
DISCORD_PING_ON_ALERT = os.getenv("DISCORD_PING_ON_ALERT", "").strip()

# Embed accent colors (decimal, not hex string).
COLOR_NORMAL = 0x2ECC71  # green
COLOR_MODIFIED = 0xF1C40F  # yellow
COLOR_CLOSED = 0xE74C3C  # red
COLOR_ERROR = 0x992D22  # dark red

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger(__name__)

DAY_NAMES = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

MONTH_RE = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)


@dataclass(frozen=True)
class HoursResult:
    checked_at: str
    local_date: str
    weekday: str
    status: str  # normal | modified | closed
    hours: str
    reason: str
    source_url: str
    source_fingerprint: str

    @property
    def comparison_key(self) -> tuple[str, str, str]:
        return self.status, self.hours, self.reason


def fetch_page() -> str:
    response = requests.get(
        URL,
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    if len(response.text) < 1000:
        raise RuntimeError("Official page response was unexpectedly short.")
    return response.text


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def normalize_time_text(value: str) -> str:
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value.strip())
    value = re.sub(r"(?i)\s*a\.?\s*m\.?", " AM", value)
    value = re.sub(r"(?i)\s*p\.?\s*m\.?", " PM", value)
    value = re.sub(r"(?i)\s*(?:-|\bto\b)\s*", " – ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def expand_day_expression(expr: str) -> list[str]:
    expr = expr.lower().replace("–", "-").replace("—", "-")
    expr = re.sub(r"\s+", "", expr)
    if "-" not in expr:
        return [expr]
    start, end = expr.split("-", 1)
    if start not in DAY_NAMES or end not in DAY_NAMES:
        return []
    a, b = DAY_NAMES.index(start), DAY_NAMES.index(end)
    if a <= b:
        return DAY_NAMES[a : b + 1]
    return DAY_NAMES[a:] + DAY_NAMES[: b + 1]


def parse_normal_schedule(text: str) -> dict[str, str]:
    schedule: dict[str, str] = {}
    # Supports examples such as:
    # Monday-Thursday: 5 a.m. to 8 p.m.
    # Friday: 5 a.m. to 7 p.m.
    pattern = re.compile(
        r"(?im)^\s*"
        r"(Monday(?:\s*[-–—]\s*Thursday)?|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
        r"\s*:\s*"
        r"([^\n]+)$"
    )
    for match in pattern.finditer(text):
        day_expr = match.group(1)
        hours_raw = match.group(2).strip()
        if not re.search(r"(?i)(a\.?m\.?|p\.?m\.?|closed)", hours_raw):
            continue
        hours = "CLOSED" if "closed" in hours_raw.lower() else normalize_time_text(hours_raw)
        for day in expand_day_expression(day_expr):
            schedule[day] = hours

    if len(schedule) < 7:
        missing = sorted(set(DAY_NAMES) - set(schedule))
        raise RuntimeError(f"Could not confidently parse the full normal schedule; missing: {missing}")
    return schedule


def month_day_to_date(month: str, day_num: int, year: int) -> date:
    parsed = date_parser.parse(f"{month} {day_num} {year}", fuzzy=False)
    return parsed.date()


def parse_modified_hours_for_date(text: str, target: date) -> Optional[tuple[str, str]]:
    """Parse date-specific special hours banners.

    Looks for a heading containing words such as "Modified Hours" or "Holiday Hours"
    and then date/hour segments like "Sunday, Sep. 6 8:00am-5:00pm".
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    special_heading_indexes = [
        i
        for i, line in enumerate(lines)
        if re.search(r"(?i)(modified|holiday|special|adjusted).{0,30}hours|hours.{0,30}(modified|holiday|special|adjusted)", line)
    ]

    segment_re = re.compile(
        rf"(?i)\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*,?\s*"
        rf"({MONTH_RE})\.?\s+(\d{{1,2}})(?:,?\s+(\d{{4}}))?\s+"
        rf"(CLOSED|\d{{1,2}}(?::\d{{2}})?\s*(?:a\.?m\.?|p\.?m\.?)\s*(?:-|–|—|to)\s*"
        rf"\d{{1,2}}(?::\d{{2}})?\s*(?:a\.?m\.?|p\.?m\.?))\b"
    )

    for idx in special_heading_indexes:
        heading = lines[idx]
        # The site currently puts the actual dates on the following line. Search a
        # bounded window so unrelated page dates do not become overrides.
        block = " | ".join(lines[idx : idx + 4])
        for match in segment_re.finditer(block):
            month, day_num, year_text = match.group(2), int(match.group(3)), match.group(4)
            year = int(year_text) if year_text else target.year
            try:
                candidate = month_day_to_date(month, day_num, year)
            except ValueError:
                continue
            if candidate != target:
                continue
            raw = match.group(5)
            hours = "CLOSED" if raw.upper() == "CLOSED" else normalize_time_text(raw)
            return hours, heading
    return None


def parse_dated_closure_for_date(text: str, target: date) -> Optional[str]:
    """Detect explicit closure ranges in update text, e.g. "closed August 24–30"."""
    compact = re.sub(r"\s+", " ", text)
    range_re = re.compile(
        rf"(?i)([^.\n]{{0,120}}\bclosed\b[^.\n]{{0,120}}?)"
        rf"\b({MONTH_RE})\s+(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}})(?:,?\s+(\d{{4}}))?"
    )
    for match in range_re.finditer(compact):
        context = match.group(1).strip()
        month, start_day, end_day, year_text = (
            match.group(2),
            int(match.group(3)),
            int(match.group(4)),
            match.group(5),
        )
        year = int(year_text) if year_text else target.year
        try:
            start = month_day_to_date(month, start_day, year)
            end = month_day_to_date(month, end_day, year)
        except ValueError:
            continue
        if start <= target <= end:
            short_reason = re.sub(r"\s+", " ", context)
            return short_reason[:180]
    return None


def determine_hours(html: str, now: datetime) -> HoursResult:
    text = html_to_text(html)
    target = now.astimezone(TZ).date()
    weekday = DAY_NAMES[target.weekday()]
    schedule = parse_normal_schedule(text)

    modified = parse_modified_hours_for_date(text, target)
    if modified:
        hours, reason = modified
        status = "closed" if hours == "CLOSED" else "modified"
    else:
        closure_reason = parse_dated_closure_for_date(text, target)
        if closure_reason:
            hours = "CLOSED"
            status = "closed"
            reason = closure_reason
        else:
            hours = schedule[weekday]
            status = "closed" if hours == "CLOSED" else "normal"
            reason = "Normal operating hours"

    return HoursResult(
        checked_at=now.astimezone(TZ).isoformat(timespec="seconds"),
        local_date=target.isoformat(),
        weekday=weekday.title(),
        status=status,
        hours=hours,
        reason=reason,
        source_url=URL,
        source_fingerprint=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    )


def load_previous_state() -> Optional[HoursResult]:
    if not STATE_PATH.exists():
        return None
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return HoursResult(**payload)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        LOG.warning("Ignoring unreadable state file: %s", exc)
        return None


def save_state(result: HoursResult) -> None:
    STATE_PATH.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")


def changed_since_previous(previous: Optional[HoursResult], current: HoursResult) -> bool:
    if previous is None:
        return True
    # Compare the actual announced result, not the calendar date. This catches a
    # changed closure/hours announcement while avoiding false change alerts just
    # because a new day began.
    return previous.comparison_key != current.comparison_key


def build_embed(result: HoursResult, changed: bool) -> dict:
    """Build a Discord webhook payload with an embed describing today's hours."""
    d = datetime.fromisoformat(result.local_date).strftime("%A, %B %-d")

    if result.status == "closed":
        color = COLOR_CLOSED
        hours_value = "🚫 CLOSED today"
    else:
        color = COLOR_MODIFIED if result.status == "modified" else COLOR_NORMAL
        hours_value = result.hours

    fields = [
        {"name": "📅 Date", "value": d, "inline": False},
        {"name": "🕐 Today's Hours", "value": hours_value, "inline": False},
    ]

    if result.status == "modified":
        fields.append({"name": "⚠️ MODIFIED HOURS", "value": result.reason, "inline": False})
    elif result.status == "closed":
        fields.append({"name": "🚨 CLOSED", "value": result.reason, "inline": False})

    fields.append(
        {
            "name": "🔗 Official Source",
            "value": f"[ShakopeeMN.gov]({result.source_url})",
            "inline": False,
        }
    )

    embed = {
        "title": "🏊 Shakopee Community Center",
        "url": result.source_url,
        "color": color,
        "fields": fields,
        "footer": {"text": f"Checked {result.checked_at}"},
    }

    payload: dict = {
        "username": DISCORD_USERNAME,
        "embeds": [embed],
    }
    if DISCORD_AVATAR_URL:
        payload["avatar_url"] = DISCORD_AVATAR_URL
    if changed and result.status != "normal" and DISCORD_PING_ON_ALERT:
        payload["content"] = DISCORD_PING_ON_ALERT

    return payload


def build_failure_payload(url: str) -> dict:
    embed = {
        "title": "⚠️ Could not verify today's hours",
        "url": url,
        "color": COLOR_ERROR,
        "description": (
            "The Shakopee CC hours bot could not safely parse today's official "
            "hours from the city website. Please check it manually.\n\n"
            f"[ShakopeeMN.gov]({url})"
        ),
    }
    payload: dict = {
        "username": DISCORD_USERNAME,
        "embeds": [embed],
    }
    if DISCORD_AVATAR_URL:
        payload["avatar_url"] = DISCORD_AVATAR_URL
    if DISCORD_PING_ON_ALERT:
        payload["content"] = DISCORD_PING_ON_ALERT
    return payload


def send_discord_webhook(payload: dict) -> None:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        raise RuntimeError("Missing required environment variable: DISCORD_WEBHOOK_URL")

    response = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
    # Discord webhooks return 204 No Content on success.
    if response.status_code >= 300:
        raise RuntimeError(
            f"Discord webhook post failed: {response.status_code} {response.text[:300]}"
        )


def main() -> int:
    if SEND_MODE not in {"daily", "changes_only"}:
        raise RuntimeError("SEND_MODE must be 'daily' or 'changes_only'.")

    now = datetime.now(TZ)
    previous = load_previous_state()

    try:
        html = fetch_page()
        current = determine_hours(html, now)
    except Exception:
        LOG.exception("Could not safely determine today's hours")
        # We intentionally do not overwrite state after a parse/fetch failure.
        # Optionally alert the owner if the Discord webhook is configured.
        try:
            send_discord_webhook(build_failure_payload(URL))
            LOG.info("Sent verification-failure Discord alert")
        except Exception as webhook_exc:
            LOG.error("Could not send failure alert to Discord either: %s", webhook_exc)
        return 1

    changed = changed_since_previous(previous, current)
    should_send = SEND_MODE == "daily" or changed
    LOG.info(
        "Resolved %s: status=%s hours=%s reason=%s changed=%s send=%s",
        current.local_date,
        current.status,
        current.hours,
        current.reason,
        changed,
        should_send,
    )

    if should_send:
        send_discord_webhook(build_embed(current, changed))
        LOG.info("Posted Discord message")
    else:
        LOG.info("No change detected; SEND_MODE=changes_only, so no message sent.")

    save_state(current)
    return 0


if __name__ == "__main__":
    sys.exit(main())
