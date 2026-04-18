import importlib
import sys
import types
import unittest

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


if __name__ == "__main__":
    unittest.main()
