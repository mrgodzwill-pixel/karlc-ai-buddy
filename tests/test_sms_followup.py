import importlib
import sys
import types
import unittest
from unittest.mock import Mock, patch

if "requests" not in sys.modules:
    fake_requests = types.ModuleType("requests")
    fake_requests.post = Mock()
    sys.modules["requests"] = fake_requests

sms_followup = importlib.import_module("sms_followup")


class SMSFollowupTests(unittest.TestCase):
    def test_normalize_ph_phone_number_accepts_local_mobile(self):
        self.assertEqual(
            sms_followup.normalize_ph_phone_number("0917-123-4567"),
            "639171234567",
        )

    def test_normalize_ph_phone_number_accepts_plus63(self):
        self.assertEqual(
            sms_followup.normalize_ph_phone_number("+639171234567"),
            "639171234567",
        )

    def test_build_followup_message_for_enrollment_ticket(self):
        message = sms_followup.build_followup_message(
            {
                "type": "enrollment_incomplete",
                "course_title": "MikroTik Hybrid",
            },
            "Juan Dela Cruz",
        )

        self.assertIn("Juan", message)
        self.assertIn("MikroTik Hybrid", message)
        self.assertIn("course@karlcomboy.com", message)
        self.assertIn("correct email", message)

    def test_send_followup_sms_posts_to_semaphore(self):
        fake_response = Mock()
        fake_response.status_code = 200
        fake_response.json.return_value = [
            {
                "message_id": 98765,
                "recipient": "639171234567",
                "status": "Queued",
            }
        ]

        with patch.object(sms_followup, "SEMAPHORE_ENABLED", True), patch.object(
            sms_followup, "SEMAPHORE_API_KEY", "secret"
        ), patch.object(sms_followup, "SEMAPHORE_SENDER_NAME", "KarlC"), patch(
            "sms_followup.requests.post", return_value=fake_response
        ) as mock_post:
            result = sms_followup.send_followup_sms(
                {
                    "type": "enrollment_incomplete",
                    "course_title": "MikroTik Hybrid",
                },
                "Juan Dela Cruz",
                "09171234567",
            )

        self.assertEqual(result["recipient"], "639171234567")
        self.assertEqual(result["status"], "Queued")
        self.assertEqual(result["provider_message_id"], "98765")
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args.kwargs["data"]["sendername"], "KarlC")


if __name__ == "__main__":
    unittest.main()
