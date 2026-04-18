import tempfile
import unittest
from unittest.mock import patch

import enrollment_checker


class EnrollmentCheckerTests(unittest.TestCase):
    def test_extract_payer_email_accepts_payer_email_label(self):
        body = """
        Successful payment
        Payer Email: student@example.com
        Support: notifications@xendit.co
        """

        self.assertEqual(
            enrollment_checker._extract_payer_email(body),
            "student@example.com",
        )

    def test_extract_payer_email_ignores_other_email_labels(self):
        body = """
        Customer Email: learner@example.com
        Reply to course@karlcomboy.com for help
        """

        self.assertIsNone(
            enrollment_checker._extract_payer_email(body),
        )

    def test_extract_payer_email_accepts_html_payer_email_label(self):
        body = """
        <table>
          <tr><td>Payer Email</td><td>student@example.com</td></tr>
        </table>
        """

        self.assertEqual(
            enrollment_checker._extract_payer_email(body),
            "student@example.com",
        )

    def test_extract_enrolment_email_accepts_email_label(self):
        body = """
        Welcome to the course
        Email: student@example.com
        Reply to course@karlcomboy.com for help
        """

        self.assertEqual(
            enrollment_checker._extract_enrolment_email(body),
            "student@example.com",
        )

    def test_extract_enrolment_email_ignores_random_body_emails(self):
        body = """
        Support: course@karlcomboy.com
        Backup contact: learner@example.com
        """

        self.assertIsNone(
            enrollment_checker._extract_enrolment_email(body),
        )

    def test_extract_enrolment_email_accepts_html_email_label(self):
        body = """
        <div>Email</div><div>student@example.com</div>
        <div>Need help? course@karlcomboy.com</div>
        """

        self.assertEqual(
            enrollment_checker._extract_enrolment_email(body),
            "student@example.com",
        )

    def test_compare_payments_uses_paid_specific_queries(self):
        messages = {
            f"from:{enrollment_checker.XENDIT_SENDER} newer_than:7d": [
                {
                    "subject": "Payment Link Expired",
                    "from": "notifications@xendit.co",
                    "date": "Sat, 18 Apr 2026 08:00:00 +0800",
                    "body": "Expired payment reminder",
                }
            ],
            f'from:{enrollment_checker.XENDIT_SENDER} subject:"INVOICE PAID" newer_than:7d': [
                {
                    "subject": "INVOICE PAID: karlcw-quickstart-799-123",
                    "from": "notifications@xendit.co",
                    "date": "Sat, 18 Apr 2026 07:30:00 +0800",
                    "body": "Payer Email: payer@example.com\nTotal: PHP 799",
                }
            ],
            f'from:{enrollment_checker.XENDIT_SENDER} subject:"Successful Payment" newer_than:7d': [],
            f'from:{enrollment_checker.XENDIT_SENDER} subject:"Payment received" newer_than:7d': [],
            f'from:{enrollment_checker.XENDIT_SENDER} subject:"Payment completed" newer_than:7d': [],
            f'from:{enrollment_checker.XENDIT_SENDER} subject:"Pembayaran Berhasil" newer_than:7d': [],
            f"from:{enrollment_checker.SYSTEME_SENDER} newer_than:7d": [],
        }

        def fake_search(query, limit=20):
            return messages.get(query, [])

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch("enrollment_checker.gmail_imap.available", return_value=True):
                    with patch("enrollment_checker.gmail_imap.search", side_effect=fake_search):
                        report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

        self.assertEqual(report["total_payments"], 1)
        self.assertEqual(report["payments"][0]["email"], "payer@example.com")
        self.assertEqual(report["payments"][0]["course"], "MikroTik Basic (QuickStart)")


if __name__ == "__main__":
    unittest.main()
