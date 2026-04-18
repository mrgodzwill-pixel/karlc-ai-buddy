import tempfile
import unittest
from unittest.mock import patch

import support_inbox
import ticket_system


class SupportInboxTests(unittest.TestCase):
    def test_get_recent_support_emails_dedupes_and_filters_system_senders(self):
        student_message = {
            "from": "Student One <student1@example.com>",
            "subject": "Need help with enrollment",
            "date": "Sat, 18 Apr 2026 10:00:00 +0800",
            "body": "Hi, my email was wrong.",
        }
        system_message = {
            "from": "notifications@xendit.co",
            "subject": "Payment completed",
            "date": "Sat, 18 Apr 2026 09:00:00 +0800",
            "body": "Payment notice",
        }

        def fake_search(query, limit=20):
            if query.startswith("to:"):
                return [student_message, system_message]
            return [student_message]

        with patch("support_inbox.gmail_imap.available", return_value=True), patch(
            "support_inbox.gmail_imap.search", side_effect=fake_search
        ):
            emails = support_inbox.get_recent_support_emails(days_back=7, limit=10)

        self.assertEqual(len(emails), 1)
        self.assertIn("student1@example.com", emails[0]["from"])

    def test_get_new_support_emails_baselines_existing_messages(self):
        first = [
            {
                "id": "msg-1",
                "from": "Student One <student1@example.com>",
                "subject": "Need help",
                "date": "Sat, 18 Apr 2026 10:00:00 +0800",
                "preview": "Wrong email",
            }
        ]
        second = [
            {
                "id": "msg-2",
                "from": "Student Two <student2@example.com>",
                "subject": "Correct email",
                "date": "Sat, 18 Apr 2026 11:00:00 +0800",
                "preview": "Here is my correct email",
            }
        ] + first

        with tempfile.TemporaryDirectory() as tmpdir:
            seen_file = f"{tmpdir}/support_inbox_seen.json"
            with patch.object(support_inbox, "SUPPORT_SEEN_FILE", seen_file):
                with patch("support_inbox.get_recent_support_emails", return_value=first):
                    initial = support_inbox.get_new_support_emails(days_back=7, limit=20)
                with patch("support_inbox.get_recent_support_emails", return_value=second):
                    new_emails = support_inbox.get_new_support_emails(days_back=7, limit=20)

        self.assertEqual(initial, [])
        self.assertEqual(len(new_emails), 1)
        self.assertEqual(new_emails[0]["id"], "msg-2")

    def test_sync_support_email_tickets_creates_and_reuses_pending_ticket(self):
        email = {
            "id": "support-1",
            "from": "Juan Dela Cruz <juan@example.com>",
            "subject": "Need help with enrollment",
            "date": "Sat, 18 Apr 2026 10:00:00 +0800",
            "preview": "My correct email is juan@example.com",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tickets_file = f"{tmpdir}/tickets.json"
            overrides_file = f"{tmpdir}/resolved_enrollment_overrides.json"

            with patch.object(ticket_system, "TICKETS_FILE", tickets_file), patch.object(
                ticket_system, "ENROLLMENT_RESOLUTIONS_FILE", overrides_file
            ):
                first_synced, created = support_inbox.sync_support_email_tickets([email])
                second_synced, second_created = support_inbox.sync_support_email_tickets([email])
                pending_support_tickets = ticket_system.get_pending_tickets("support_email")

        self.assertEqual(len(created), 1)
        self.assertEqual(len(second_created), 0)
        self.assertEqual(first_synced[0]["ticket_id"], second_synced[0]["ticket_id"])
        self.assertEqual(pending_support_tickets[0]["student_email"], "juan@example.com")


if __name__ == "__main__":
    unittest.main()
