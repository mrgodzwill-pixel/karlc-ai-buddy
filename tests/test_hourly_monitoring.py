import importlib
import sys
import types
import unittest
from unittest.mock import patch

if "requests" not in sys.modules:
    fake_requests = types.ModuleType("requests")
    fake_requests.post = lambda *args, **kwargs: None
    fake_requests.get = lambda *args, **kwargs: None
    sys.modules["requests"] = fake_requests

fb_agent = importlib.import_module("fb_agent")
main = importlib.import_module("main")


class HourlyMonitoringTests(unittest.TestCase):
    def test_run_enrollment_check_sends_hourly_reminder_for_active_unmatched(self):
        base_report = {
            "total_payments": 1,
            "total_enrolments": 0,
            "matched": 0,
            "unmatched": 1,
            "matched_students": [],
            "unmatched_students": [
                {
                    "payer_name": "Juan Dela Cruz",
                    "email": "juan@example.com",
                    "phone": "09171234567",
                    "course": "MikroTik Hybrid",
                    "amount": "PHP 1499",
                    "date": "2026-04-18T10:00:00+08:00",
                }
            ],
            "payments": [],
            "enrolments": [],
            "checked_at": "2026-04-18T10:00:00+08:00",
        }

        with patch("fb_agent.compare_payments_vs_enrolments", return_value=dict(base_report)), patch(
            "fb_agent.filter_resolved_enrollment_students",
            return_value=(list(base_report["unmatched_students"]), []),
        ), patch("fb_agent.create_enrollment_ticket", return_value=None), patch(
            "fb_agent.send_message"
        ) as send_message:
            report = fb_agent.run_enrollment_check(notify_if_new_tickets=True)

        self.assertEqual(report["unmatched"], 1)
        self.assertTrue(send_message.called)
        self.assertIn("Active unmatched students still pending", send_message.call_args[0][0])
        self.assertIn("09171234567", send_message.call_args[0][0])

    def test_run_enrollment_check_collapses_duplicate_unmatched_rows_into_one_ticket_case(self):
        duplicate_rows = [
            {
                "payer_name": "Ricky Andeo",
                "email": "rickyandeo90@gmail.com",
                "phone": "639777235690",
                "course": "10G Core Part 3: Centralized Pisowifi Setup",
                "amount": "PHP 1,500",
                "payment_method": "xendit",
                "date_paid": "2026-04-21T16:30:00+08:00",
            },
            {
                "payer_name": "Ricky Andeo",
                "email": " RickyAndeo90@gmail.com ",
                "phone": "",
                "course": " 10G Core Part 3: Centralized Pisowifi Setup ",
                "amount": "PHP 1,500",
                "payment_method": "",
                "date_paid": "",
            },
        ]
        base_report = {
            "total_payments": 2,
            "total_enrolments": 0,
            "matched": 0,
            "unmatched": 2,
            "matched_students": [],
            "unmatched_students": list(duplicate_rows),
            "payments": [],
            "enrolments": [],
            "checked_at": "2026-04-21T16:30:00+08:00",
        }

        with patch("fb_agent.compare_payments_vs_enrolments", return_value=dict(base_report)), patch(
            "fb_agent.filter_resolved_enrollment_students",
            return_value=(list(duplicate_rows), []),
        ), patch("fb_agent.create_enrollment_ticket", return_value={"id": 1}) as create_ticket:
            report = fb_agent.run_enrollment_check(notify_if_new_tickets=False)

        self.assertEqual(report["unmatched"], 1)
        self.assertEqual(report["collapsed_unmatched_duplicates"], 1)
        self.assertEqual(len(report["unmatched_students"]), 1)
        self.assertEqual(create_ticket.call_count, 1)
        self.assertEqual(report["unmatched_students"][0]["phone"], "639777235690")

    def test_run_hourly_support_watch_sends_reminder_for_pending_support_tickets(self):
        pending_ticket = {
            "id": 7,
            "student_name": "Juan Dela Cruz",
            "student_email": "juan@example.com",
            "phone_number": "639171234567",
            "course_title": "Need help with enrollment",
        }

        with patch("support_inbox.get_new_support_emails", return_value=[]), patch(
            "ticket_system.get_pending_tickets", return_value=[pending_ticket]
        ), patch("telegram_bot.send_message") as send_message:
            main.run_hourly_support_watch()

        self.assertTrue(send_message.called)
        self.assertIn("Hourly Support Reminder", send_message.call_args[0][0])
        self.assertIn("639171234567", send_message.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
