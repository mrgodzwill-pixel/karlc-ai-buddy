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

    def test_process_message_done_all_uses_bulk_resolve(self):
        with patch("telegram_bot.resolve_all_tickets") as bulk_resolve:
            result = telegram_bot.process_message("/done all")

        bulk_resolve.assert_called_once_with()
        self.assertEqual(result, "done_all")


if __name__ == "__main__":
    unittest.main()
