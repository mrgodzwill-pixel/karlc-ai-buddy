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

telegram_bot = importlib.import_module("telegram_bot")


class TelegramBotCommandTests(unittest.TestCase):
    def test_parse_follow_command(self):
        ticket_id, contact_name, phone_number = telegram_bot._parse_follow_command(
            "/follow 12 | Juan Dela Cruz | 09171234567"
        )

        self.assertEqual(ticket_id, 12)
        self.assertEqual(contact_name, "Juan Dela Cruz")
        self.assertEqual(phone_number, "09171234567")

    def test_parse_follow_command_uses_saved_contact_when_only_ticket_id_given(self):
        ticket_id, contact_name, phone_number = telegram_bot._parse_follow_command("/follow 12")

        self.assertEqual(ticket_id, 12)
        self.assertEqual(contact_name, "")
        self.assertEqual(phone_number, "")

    def test_process_message_done_all_uses_bulk_resolve(self):
        with patch("telegram_bot.resolve_all_tickets") as bulk_resolve:
            result = telegram_bot.process_message("/done all")

        bulk_resolve.assert_called_once_with()
        self.assertEqual(result, "done_all")

    def test_send_ticket_followup_uses_saved_ticket_contact_details(self):
        ticket = {
            "id": 12,
            "student_name": "Juan Dela Cruz",
            "phone_number": "09171234567",
            "course_title": "MikroTik Hybrid",
        }
        result = {
            "recipient": "639171234567",
            "status": "Queued",
            "provider_message_id": "abc123",
            "provider_response": {"status": "Queued"},
            "message_text": "Hi Juan",
        }

        with patch.object(telegram_bot, "SEMAPHORE_ENABLED", True), patch(
            "telegram_bot.send_message"
        ) as send_message, patch(
            "ticket_system.get_ticket", return_value=ticket
        ), patch(
            "sms_followup.send_followup_sms", return_value=result
        ) as send_followup_sms, patch(
            "ticket_system.record_followup_attempt"
        ) as record_followup_attempt:
            telegram_bot.send_ticket_followup(12, "", "")

        send_followup_sms.assert_called_once_with(ticket, "Juan Dela Cruz", "09171234567")
        record_followup_attempt.assert_called_once()
        self.assertTrue(send_message.called)

    def test_process_message_students_runs_grouped_systeme_summary(self):
        with patch("telegram_bot.send_systeme_students") as send_students, patch(
            "telegram_bot.send_message"
        ) as send_message:
            result = telegram_bot.process_message("/students hybrid")

        send_students.assert_called_once_with(course_query="hybrid")
        self.assertTrue(send_message.called)
        self.assertEqual(result, "students")

    def test_process_message_systeme_sync_runs_backfill(self):
        with patch("telegram_bot.send_systeme_backfill") as send_backfill, patch(
            "telegram_bot.send_message"
        ) as send_message:
            result = telegram_bot.process_message("/systeme_sync")

        send_backfill.assert_called_once_with()
        self.assertTrue(send_message.called)
        self.assertEqual(result, "systeme_sync")

    def test_send_systeme_backfill_rejects_duplicate_run(self):
        with patch.object(telegram_bot, "_SYSTEME_BACKFILL_RUNNING", True), patch(
            "telegram_bot.send_message"
        ) as send_message:
            started = telegram_bot.send_systeme_backfill()

        self.assertFalse(started)
        send_message.assert_called_once()

    def test_send_systeme_backfill_starts_background_thread(self):
        started_targets = []

        class FakeThread:
            def __init__(self, target=None, daemon=None):
                started_targets.append({"target": target, "daemon": daemon, "started": False})
                self._slot = started_targets[-1]

            def start(self):
                self._slot["started"] = True

        with patch.object(telegram_bot, "_SYSTEME_BACKFILL_RUNNING", False), patch(
            "telegram_bot.threading.Thread", FakeThread
        ):
            started = telegram_bot.send_systeme_backfill()

        self.assertTrue(started)
        self.assertEqual(len(started_targets), 1)
        self.assertTrue(started_targets[0]["started"])
        self.assertTrue(started_targets[0]["daemon"])

    def test_process_message_systeme_add_uses_manual_contact_flow(self):
        with patch("telegram_bot.send_systeme_manual_contact") as send_contact, patch(
            "telegram_bot.send_message"
        ) as send_message:
            result = telegram_bot.process_message("/systeme_add 12")

        send_contact.assert_called_once_with(ticket_id=12, email="", name="", phone_number="")
        self.assertTrue(send_message.called)
        self.assertEqual(result, "systeme_add")

    def test_process_message_systeme_enroll_uses_manual_enrollment_flow(self):
        with patch("telegram_bot.send_systeme_manual_enrollment") as send_enroll, patch(
            "telegram_bot.send_message"
        ) as send_message:
            result = telegram_bot.process_message("/systeme_enroll 12")

        send_enroll.assert_called_once_with(
            ticket_id=12,
            email="",
            course_query="",
            name="",
            phone_number="",
        )
        self.assertTrue(send_message.called)
        self.assertEqual(result, "systeme_enroll")


if __name__ == "__main__":
    unittest.main()
