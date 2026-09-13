import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from scraper import (
    build_embed,
    build_failure_payload,
    determine_hours,
    parse_normal_schedule,
    send_discord_webhook,
)

TZ = ZoneInfo("America/Chicago")

FIXTURE = """
<html><body>
<h1>Community Center</h1>
<h3>Labor Day Weekend Modified Hours</h3>
<h5>Saturday, Sep. 5 6:00am-5:00pm | Sunday, Sep. 6 8:00am-5:00pm | Monday, Sep. 7 CLOSED</h5>
<h6>HOURS</h6>
<div>Monday-Thursday: 5 a.m. to 8 p.m.</div>
<div>Friday: 5 a.m. to 7 p.m.</div>
<div>Saturday: 6 a.m. to 7 p.m.</div>
<div>Sunday: 8 a.m. to 6 p.m.</div>
</body></html>
"""

MAINTENANCE = """
<html><body>
<p>The Shakopee Community Center will be closed August 24–30 and will resume normal operating hours on Monday, August 31.</p>
<div>Monday-Thursday: 5 a.m. to 8 p.m.</div>
<div>Friday: 5 a.m. to 7 p.m.</div>
<div>Saturday: 6 a.m. to 7 p.m.</div>
<div>Sunday: 8 a.m. to 6 p.m.</div>
</body></html>
"""


class ScraperTests(unittest.TestCase):
    def test_normal_schedule_expands_range(self):
        schedule = parse_normal_schedule(
            "Monday-Thursday: 5 a.m. to 8 p.m.\n"
            "Friday: 5 a.m. to 7 p.m.\n"
            "Saturday: 6 a.m. to 7 p.m.\n"
            "Sunday: 8 a.m. to 6 p.m."
        )
        self.assertEqual(schedule["monday"], "5:00 AM – 8:00 PM")
        self.assertEqual(schedule["thursday"], "5:00 AM – 8:00 PM")
        self.assertEqual(schedule["sunday"], "8:00 AM – 6:00 PM")

    def test_modified_hours_override_normal_hours(self):
        now = datetime(2026, 9, 6, 6, 0, tzinfo=TZ)
        result = determine_hours(FIXTURE, now)
        self.assertEqual(result.status, "modified")
        self.assertEqual(result.hours, "8:00 AM – 5:00 PM")
        self.assertIn("Modified Hours", result.reason)

    def test_closed_special_day(self):
        now = datetime(2026, 9, 7, 6, 0, tzinfo=TZ)
        result = determine_hours(FIXTURE, now)
        self.assertEqual(result.status, "closed")
        self.assertEqual(result.hours, "CLOSED")

    def test_normal_day(self):
        now = datetime(2026, 9, 8, 6, 0, tzinfo=TZ)
        result = determine_hours(FIXTURE, now)
        self.assertEqual(result.status, "normal")
        self.assertEqual(result.hours, "5:00 AM – 8:00 PM") 

    def test_dated_maintenance_closure(self):
        now = datetime(2026, 8, 25, 6, 0, tzinfo=TZ)
        result = determine_hours(MAINTENANCE, now)
        self.assertEqual(result.status, "closed")
        self.assertEqual(result.hours, "CLOSED")

    def test_incomplete_schedule_fails_closed(self):
        with self.assertRaises(RuntimeError):
            parse_normal_schedule("Monday: 5 a.m. to 8 p.m.")


class DiscordEmbedTests(unittest.TestCase):
    def test_normal_day_embed_has_no_alert_field(self):
        now = datetime(2026, 9, 8, 6, 0, tzinfo=TZ)
        result = determine_hours(FIXTURE, now)
        payload = build_embed(result, changed=True)

        embed = payload["embeds"][0]
        self.assertEqual(embed["title"], "🏊 Shakopee Community Center")
        field_names = [f["name"] for f in embed["fields"]]
        self.assertIn("📅 Date", field_names)
        self.assertIn("🕐 Today's Hours", field_names)
        self.assertNotIn("⚠️ MODIFIED HOURS", field_names)
        self.assertNotIn("🚨 CLOSED", field_names)
        self.assertNotIn("content", payload)

    def test_modified_day_embed_has_warning_field(self):
        now = datetime(2026, 9, 6, 6, 0, tzinfo=TZ)
        result = determine_hours(FIXTURE, now)
        payload = build_embed(result, changed=True)

        embed = payload["embeds"][0]
        field_names = [f["name"] for f in embed["fields"]]
        self.assertIn("⚠️ MODIFIED HOURS", field_names)

    def test_closed_day_embed_marks_closed(self):
        now = datetime(2026, 9, 7, 6, 0, tzinfo=TZ)
        result = determine_hours(FIXTURE, now)
        payload = build_embed(result, changed=True)

        embed = payload["embeds"][0]
        hours_field = next(f for f in embed["fields"] if f["name"] == "🕐 Today's Hours")
        self.assertIn("CLOSED", hours_field["value"])

    def test_ping_only_added_on_alert_with_env(self):
        now = datetime(2026, 9, 6, 6, 0, tzinfo=TZ)
        result = determine_hours(FIXTURE, now)
        with patch.dict("os.environ", {"DISCORD_PING_ON_ALERT": "@here"}):
            import importlib

            import scraper as scraper_module

            importlib.reload(scraper_module)
            try:
                payload = scraper_module.build_embed(result, changed=True)
                self.assertEqual(payload["content"], "@here")
            finally:
                importlib.reload(scraper_module)

    def test_failure_payload_has_error_embed(self):
        payload = build_failure_payload("https://example.com")
        embed = payload["embeds"][0]
        self.assertIn("could not verify", embed["title"].lower())

def test_both_page_formats_normalize_identically(self):
    a = scraper.parse_normal_schedule(
        "Monday-Friday: 5 a.m. to 9 p.m.\n"
        "Saturday: 6 a.m. to 8 p.m.\nSunday: 8 a.m. to 8 p.m."
    )
    b = scraper.parse_normal_schedule(
        "Monday-Friday: 5:00 A.M.- 9:00 P.M.\n"
        "Saturday: 6:00 A.M.- 8:00 P.M.\nSunday: 8:00 A.M.-8:00 P.M."
    )
    self.assertEqual(a, b)

def test_conflicting_blocks_fail_closed(self):
    with self.assertRaises(RuntimeError):
        scraper.parse_normal_schedule(
            "Monday-Friday: 5 a.m. to 9 p.m.\n"
            "Saturday: 6 a.m. to 8 p.m.\nSunday: 8 a.m. to 8 p.m.\n"
            "Monday-Friday: 5 a.m. to 7 p.m."
        )

class SendDiscordWebhookTests(unittest.TestCase):
    def test_raises_without_webhook_url(self):
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("DISCORD_WEBHOOK_URL", None)
            with self.assertRaises(RuntimeError):
                send_discord_webhook({"content": "test"})

    @patch("scraper.requests.post")
    def test_posts_to_webhook_url(self, mock_post):
        mock_post.return_value = Mock(status_code=204, text="")
        with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test"}):
            send_discord_webhook({"content": "hello"})
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://discord.com/api/webhooks/test")
        self.assertEqual(kwargs["json"], {"content": "hello"})

    @patch("scraper.requests.post")
    def test_raises_on_error_status(self, mock_post):
        mock_post.return_value = Mock(status_code=400, text="bad request")
        with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test"}):
            with self.assertRaises(RuntimeError):
                send_discord_webhook({"content": "hello"})


if __name__ == "__main__":
    unittest.main()