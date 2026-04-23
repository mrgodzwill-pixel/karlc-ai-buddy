import unittest
from datetime import datetime
from unittest.mock import patch
import sys
import types

if "requests" not in sys.modules:
    fake_requests = types.ModuleType("requests")
    fake_requests.request = lambda *args, **kwargs: None
    fake_requests.get = lambda *args, **kwargs: None
    fake_requests.post = lambda *args, **kwargs: None
    sys.modules["requests"] = fake_requests

import health_monitor


class HealthMonitorTests(unittest.TestCase):
    def test_build_health_report_counts_store_and_tickets(self):
        now = datetime(2026, 4, 23, 18, 0, tzinfo=health_monitor.PHT)

        with patch("health_monitor.gmail_imap.available", return_value=True), patch(
            "health_monitor.xendit_api.available", return_value=True
        ), patch(
            "health_monitor.systeme_api.available", return_value=False
        ), patch(
            "health_monitor.systeme_sheet_import.available", return_value=True
        ), patch(
            "health_monitor.google_sheet_sync.available", return_value=True
        ), patch(
            "health_monitor.load_payment_store",
            return_value={
                "checked_at": "2026-04-23T17:45:00+08:00",
                "payments": [
                    {"status": "paid"},
                    {"status": "settled"},
                    {"status": "failed"},
                ],
            },
        ), patch(
            "health_monitor.load_student_store",
            return_value={
                "checked_at": "2026-04-23T17:40:00+08:00",
                "students": [
                    {
                        "email": "a@example.com",
                        "courses": [
                            {"name": "Course A", "status": "enrolled"},
                            {"name": "Course B", "status": "sold"},
                        ],
                    },
                    {
                        "email": "b@example.com",
                        "courses": [{"name": "Course C", "status": "enrolled"}],
                    },
                ],
            },
        ), patch(
            "health_monitor.load_json",
            side_effect=[
                {"checked_at": "2026-04-23T17:30:00+08:00", "matched": 5, "unmatched": 1},
                ["x1", "x2"],
                ["s1"],
            ],
        ), patch(
            "health_monitor.get_ticket_stats",
            return_value={"total": 10, "pending": 3, "done": 7, "enrollment_incomplete": 1, "support_email": 1},
        ), patch(
            "health_monitor.os.path.exists",
            side_effect=lambda path: True,
        ), patch(
            "health_monitor.os.path.getmtime",
            side_effect=[now.timestamp(), now.timestamp()],
        ):
            report = health_monitor.build_health_report(now=now)

        self.assertEqual(report["xendit_store"]["payments"], 2)
        self.assertEqual(report["systeme_store"]["students"], 2)
        self.assertEqual(report["systeme_store"]["course_rows"], 2)
        self.assertEqual(report["enrollment_report"]["unmatched"], 1)
        self.assertEqual(report["tickets"]["pending"], 3)
        self.assertEqual(report["xendit_webhooks"]["count"], 2)
        self.assertEqual(report["systeme_webhooks"]["count"], 1)

    def test_format_health_report_includes_sections(self):
        text = health_monitor.format_health_report(
            {
                "checked_at": "2026-04-23T18:00:00+08:00",
                "gmail": {"configured": True, "status": "configured"},
                "xendit_api": {"configured": True, "status": "enabled"},
                "systeme_api": {"configured": False, "status": "missing API key"},
                "sheet_read": {"configured": True, "status": "enabled"},
                "sheet_write": {"configured": False, "status": "missing sheet ID or Google service account"},
                "xendit_store": {
                    "ok": True,
                    "payments": 120,
                    "age_label": "10m ago",
                    "checked_label": "2026-04-23 17:50 PHT",
                },
                "systeme_store": {
                    "ok": True,
                    "students": 829,
                    "course_rows": 1353,
                    "age_label": "15m ago",
                    "checked_label": "2026-04-23 17:45 PHT",
                },
                "enrollment_report": {
                    "ok": True,
                    "matched": 110,
                    "unmatched": 1,
                    "age_label": "30m ago",
                    "checked_label": "2026-04-23 17:30 PHT",
                },
                "xendit_webhooks": {"ok": True, "count": 45, "age_label": "1h ago"},
                "systeme_webhooks": {"ok": False, "count": 0, "age_label": "never"},
                "tickets": {"pending": 3, "enrollment_incomplete": 1, "support_email": 1, "done": 12, "total": 15},
            }
        )

        self.assertIn("Bot Health", text)
        self.assertIn("Gmail IMAP", text)
        self.assertIn("Xendit Store: 120 paid records", text)
        self.assertIn("Systeme Store: 829 students / 1353 course rows", text)
        self.assertIn("Enrollment Report: 110 matched / 1 unmatched", text)
        self.assertIn("Pending: 3 | Enrollment: 1 | Support: 1", text)


if __name__ == "__main__":
    unittest.main()
