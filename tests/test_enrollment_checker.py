import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

if "requests" not in sys.modules:
    fake_requests = types.ModuleType("requests")
    fake_requests.request = lambda *args, **kwargs: None
    fake_requests.get = lambda *args, **kwargs: None
    fake_requests.post = lambda *args, **kwargs: None
    sys.modules["requests"] = fake_requests

import enrollment_checker
import xendit_payments


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
        self.assertEqual(report["payments"][0]["course"], "MikroTik QuickStart: Configure From Scratch")

    def test_compare_payments_persists_local_xendit_store(self):
        messages = {
            f"from:{enrollment_checker.XENDIT_SENDER} newer_than:7d": [],
            f'from:{enrollment_checker.XENDIT_SENDER} subject:"INVOICE PAID" newer_than:7d': [
                {
                    "subject": "INVOICE PAID: karlcw-quickstart-799-123",
                    "from": "notifications@xendit.co",
                    "date": "Sat, 18 Apr 2026 07:30:00 +0800",
                    "body": (
                        "Payer Name: Juan Dela Cruz\n"
                        "Payer Email: payer@example.com\n"
                        "Mobile Number: 0917-123-4567\n"
                        "Payment Method: GCash\n"
                        "Total: PHP 799"
                    ),
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
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                    with patch("enrollment_checker.gmail_imap.available", return_value=True):
                        with patch("enrollment_checker.gmail_imap.search", side_effect=fake_search):
                            report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

            with open(payments_file) as f:
                payment_store = json.load(f)

        self.assertEqual(report["payments"][0]["payer_name"], "Juan Dela Cruz")
        self.assertEqual(report["payments"][0]["phone"], "0917-123-4567")
        self.assertEqual(report["payments"][0]["payment_method"], "GCash")
        self.assertEqual(payment_store["payments"][0]["payer_name"], "Juan Dela Cruz")
        self.assertEqual(payment_store["payments"][0]["email"], "payer@example.com")
        self.assertEqual(payment_store["payments"][0]["phone_normalized"], "639171234567")

    def test_compare_payments_uses_recent_local_store_records_before_gmail_fallback(self):
        enrolment_messages = {
            f"from:{enrollment_checker.SYSTEME_SENDER} newer_than:7d": [
                {
                    "subject": "Welcome to MikroTik Hybrid",
                    "from": enrollment_checker.SYSTEME_SENDER,
                    "date": "Sat, 18 Apr 2026 09:00:00 +0800",
                    "body": "Email: student@example.com",
                }
            ],
        }

        def fake_search(query, limit=20):
            if query.startswith(f"from:{enrollment_checker.XENDIT_SENDER}"):
                raise AssertionError("Xendit Gmail fallback should not run when recent store data exists")
            return enrolment_messages.get(query, [])

        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                    xendit_payments.upsert_payment_records([
                        {
                            "status": "paid",
                            "payer_name": "Juan Dela Cruz",
                            "email": "student@example.com",
                            "phone": "09171234567",
                            "course": "MikroTik Hybrid",
                            "amount": "PHP 1499",
                            "source": "xendit_payment_webhook",
                            "xendit_payment_id": "py-123",
                            "date": "2026-04-18T01:35:00Z",
                            "paid_at": "2026-04-18T01:35:00Z",
                        }
                    ])
                    with patch("enrollment_checker.xendit_api.available", return_value=False):
                        with patch("enrollment_checker.gmail_imap.available", return_value=True):
                            with patch("enrollment_checker.gmail_imap.search", side_effect=fake_search):
                                report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

        self.assertEqual(report["total_payments"], 1)
        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["payments"][0]["email"], "student@example.com")

    def test_compare_payments_does_not_trust_email_only_enrolment_message_for_course_match(self):
        messages = {
            f"from:{enrollment_checker.SYSTEME_SENDER} newer_than:7d": [],
            f"to:{enrollment_checker.SYSTEME_SENDER} newer_than:7d": [
                {
                    "subject": "Enrollment confirmation",
                    "from": "mailer@systeme.io",
                    "date": "Sat, 18 Apr 2026 09:00:00 +0800",
                    "body": "Email: student@example.com",
                }
            ],
            f'"{enrollment_checker.SYSTEME_SENDER}" newer_than:7d': [],
        }

        def fake_search(query, limit=20):
            return messages.get(query, [])

        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                    xendit_payments.upsert_payment_records([
                        {
                            "status": "paid",
                            "email": "student@example.com",
                            "course": "MikroTik Hybrid",
                            "amount": "PHP 1499",
                            "source": "xendit_payment_webhook",
                            "xendit_payment_id": "py-123",
                            "date": "2026-04-18T01:35:00Z",
                            "paid_at": "2026-04-18T01:35:00Z",
                        }
                    ])
                    with patch("enrollment_checker.xendit_api.available", return_value=False):
                        with patch("enrollment_checker.gmail_imap.available", return_value=True):
                            with patch("enrollment_checker.gmail_imap.search", side_effect=fake_search):
                                report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

        self.assertEqual(report["total_enrolments"], 1)
        self.assertEqual(report["matched"], 0)
        self.assertEqual(report["unmatched"], 1)

    def test_compare_payments_uses_direct_systeme_store_when_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                    xendit_payments.upsert_payment_records([
                        {
                            "status": "paid",
                            "email": "student@example.com",
                            "course": "MikroTik Hybrid",
                            "amount": "PHP 1499",
                            "source": "xendit_payment_webhook",
                            "xendit_payment_id": "py-123",
                            "date": "2026-04-18T01:35:00Z",
                            "paid_at": "2026-04-18T01:35:00Z",
                        }
                    ])
                    with patch("enrollment_checker.xendit_api.available", return_value=False):
                        with patch("enrollment_checker.gmail_imap.available", return_value=True):
                            with patch(
                                "enrollment_checker.list_recent_systeme_enrolments",
                                return_value=[
                                    {
                                        "email": "student@example.com",
                                        "name": "Student One",
                                        "course": "MikroTik Hybrid",
                                        "date": "2026-04-18T10:30:00+08:00",
                                    }
                                ],
                            ):
                                with patch("enrollment_checker._search_enrolment_messages") as search_emails:
                                    report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

        search_emails.assert_not_called()
        self.assertEqual(report["total_enrolments"], 1)
        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["unmatched"], 0)

    def test_compare_payments_uses_full_systeme_store_for_existing_students(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                    xendit_payments.upsert_payment_records([
                        {
                            "status": "paid",
                            "email": "student@example.com",
                            "course": "MikroTik Hybrid",
                            "amount": "PHP 1499",
                            "source": "xendit_payment_webhook",
                            "xendit_payment_id": "py-123",
                            "date": "2026-04-18T01:35:00Z",
                            "paid_at": "2026-04-18T01:35:00Z",
                        }
                    ])
                    with patch("enrollment_checker.xendit_api.available", return_value=False):
                        with patch("enrollment_checker.gmail_imap.available", return_value=True):
                            with patch(
                                "enrollment_checker.list_recent_systeme_enrolments",
                                return_value=[],
                            ), patch(
                                "enrollment_checker._search_enrolment_messages",
                                return_value=[],
                            ), patch(
                                "enrollment_checker.load_student_store",
                                return_value={
                                    "checked_at": "2026-04-18T10:30:00+08:00",
                                    "students": [
                                        {
                                            "email": "student@example.com",
                                            "courses": [
                                                {"name": "MikroTik Hybrid", "status": "enrolled"}
                                            ],
                                        }
                                    ],
                                },
                            ):
                                report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["unmatched"], 0)

    def test_compare_payments_requires_same_course_for_same_email(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                    xendit_payments.upsert_payment_records([
                        {
                            "status": "paid",
                            "email": "student@example.com",
                            "course": "MikroTik Traffic Control",
                            "amount": "PHP 749",
                            "source": "xendit_payment_webhook",
                            "xendit_payment_id": "py-traffic",
                            "date": "2026-04-18T01:35:00Z",
                            "paid_at": "2026-04-18T01:35:00Z",
                        }
                    ])
                    with patch("enrollment_checker.xendit_api.available", return_value=False):
                        with patch("enrollment_checker.gmail_imap.available", return_value=True):
                            with patch(
                                "enrollment_checker.list_recent_systeme_enrolments",
                                return_value=[],
                            ), patch(
                                "enrollment_checker._search_enrolment_messages",
                                return_value=[],
                            ), patch(
                                "enrollment_checker.load_student_store",
                                return_value={
                                    "checked_at": "2026-04-18T10:30:00+08:00",
                                    "students": [
                                        {
                                            "email": "student@example.com",
                                            "courses": [
                                                {
                                                    "name": "MikroTik QuickStart: Configure From Scratch",
                                                    "status": "enrolled",
                                                }
                                            ],
                                        }
                                    ],
                                },
                            ), patch(
                                "enrollment_checker.systeme_api.available",
                                return_value=False,
                            ):
                                report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

        self.assertEqual(report["matched"], 0)
        self.assertEqual(report["unmatched"], 1)

    def test_compare_payments_ignores_non_enrolled_systeme_store_courses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                    xendit_payments.upsert_payment_records([
                        {
                            "status": "paid",
                            "email": "student@example.com",
                            "course": "MikroTik Hybrid",
                            "amount": "PHP 1499",
                            "source": "xendit_payment_webhook",
                            "xendit_payment_id": "py-123",
                            "date": "2026-04-18T01:35:00Z",
                            "paid_at": "2026-04-18T01:35:00Z",
                        }
                    ])
                    with patch("enrollment_checker.xendit_api.available", return_value=False):
                        with patch("enrollment_checker.gmail_imap.available", return_value=True):
                            with patch(
                                "enrollment_checker.list_recent_systeme_enrolments",
                                return_value=[],
                            ), patch(
                                "enrollment_checker._search_enrolment_messages",
                                return_value=[],
                            ), patch(
                                "enrollment_checker.load_student_store",
                                return_value={
                                    "checked_at": "2026-04-18T10:30:00+08:00",
                                    "students": [
                                        {
                                            "email": "student@example.com",
                                            "courses": [
                                                {"name": "MikroTik Hybrid", "status": "sold"}
                                            ],
                                        }
                                    ],
                                },
                            ), patch(
                                "enrollment_checker.systeme_api.available",
                                return_value=False,
                            ):
                                report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

        self.assertEqual(report["matched"], 0)
        self.assertEqual(report["unmatched"], 1)

    def test_compare_payments_can_match_by_amount_when_xendit_course_text_is_generic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                    xendit_payments.upsert_payment_records([
                        {
                            "status": "paid",
                            "email": "jaahonculada77@gmail.com",
                            "course": "Invoice for Alexis Honculada",
                            "amount": "PHP 977",
                            "source": "xendit_payment_webhook",
                            "xendit_payment_id": "py-ospf-123",
                            "date": "2026-04-23T16:58:00Z",
                            "paid_at": "2026-04-23T16:58:00Z",
                        }
                    ])
                    with patch("enrollment_checker.xendit_api.available", return_value=False), patch(
                        "enrollment_checker.gmail_imap.available", return_value=True
                    ), patch(
                        "enrollment_checker.list_recent_systeme_enrolments",
                        return_value=[],
                    ), patch(
                        "enrollment_checker._search_enrolment_messages",
                        return_value=[],
                    ), patch(
                        "enrollment_checker.load_student_store",
                        return_value={
                            "checked_at": "2026-04-23T17:00:00+08:00",
                            "students": [
                                {
                                    "email": "jaahonculada77@gmail.com",
                                    "courses": [
                                        {
                                            "name": "10G Core Part 2: OSPF & Advanced Routing",
                                            "status": "enrolled",
                                        }
                                    ],
                                }
                            ],
                        },
                    ):
                        report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["unmatched"], 0)

    def test_compare_payments_does_not_override_explicit_course_when_its_price_matches_amount(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                    xendit_payments.upsert_payment_records([
                        {
                            "status": "paid",
                            "email": "student@example.com",
                            "course": "MikroTik Traffic Control",
                            "amount": "PHP 749",
                            "source": "xendit_payment_webhook",
                            "xendit_payment_id": "py-traffic-123",
                            "date": "2026-04-23T16:58:00Z",
                            "paid_at": "2026-04-23T16:58:00Z",
                        }
                    ])
                    with patch("enrollment_checker.xendit_api.available", return_value=False), patch(
                        "enrollment_checker.gmail_imap.available", return_value=True
                    ), patch(
                        "enrollment_checker.list_recent_systeme_enrolments",
                        return_value=[],
                    ), patch(
                        "enrollment_checker._search_enrolment_messages",
                        return_value=[],
                    ), patch(
                        "enrollment_checker.load_student_store",
                        return_value={
                            "checked_at": "2026-04-23T17:00:00+08:00",
                            "students": [
                                {
                                    "email": "student@example.com",
                                    "courses": [
                                        {
                                            "name": "10G Core Part 2: OSPF & Advanced Routing",
                                            "status": "enrolled",
                                        }
                                    ],
                                }
                            ],
                        },
                    ):
                        report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

        self.assertEqual(report["matched"], 0)
        self.assertEqual(report["unmatched"], 1)

    def test_compare_payments_can_match_historical_bundle_amount(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                    xendit_payments.upsert_payment_records([
                        {
                            "status": "paid",
                            "email": "bundle@example.com",
                            "course": "Invoice for Bundle Buyer",
                            "amount": "PHP 3,997",
                            "source": "xendit_payment_webhook",
                            "xendit_payment_id": "py-bundle-123",
                            "date": "2026-04-23T16:58:00Z",
                            "paid_at": "2026-04-23T16:58:00Z",
                        }
                    ])
                    with patch("enrollment_checker.xendit_api.available", return_value=False), patch(
                        "enrollment_checker.gmail_imap.available", return_value=True
                    ), patch(
                        "enrollment_checker.list_recent_systeme_enrolments",
                        return_value=[],
                    ), patch(
                        "enrollment_checker._search_enrolment_messages",
                        return_value=[],
                    ), patch(
                        "enrollment_checker.load_student_store",
                        return_value={
                            "checked_at": "2026-04-23T17:00:00+08:00",
                            "students": [
                                {
                                    "email": "bundle@example.com",
                                    "courses": [
                                        {
                                            "name": "Complete MikroTik Mastery Bundle",
                                            "status": "enrolled",
                                        }
                                    ],
                                }
                            ],
                        },
                    ):
                        report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["unmatched"], 0)

    def test_compare_payments_can_match_old_bundle_amount(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                    xendit_payments.upsert_payment_records([
                        {
                            "status": "paid",
                            "email": "dual@example.com",
                            "course": "MikroTik QuickStart: Configure From Scratch",
                            "amount": "PHP 3,500",
                            "source": "xendit_payment_webhook",
                            "xendit_payment_id": "py-dual-123",
                            "date": "2026-04-23T16:58:00Z",
                            "paid_at": "2026-04-23T16:58:00Z",
                        }
                    ])
                    with patch("enrollment_checker.xendit_api.available", return_value=False), patch(
                        "enrollment_checker.gmail_imap.available", return_value=True
                    ), patch(
                        "enrollment_checker.list_recent_systeme_enrolments",
                        return_value=[],
                    ), patch(
                        "enrollment_checker._search_enrolment_messages",
                        return_value=[],
                    ), patch(
                        "enrollment_checker.load_student_store",
                        return_value={
                            "checked_at": "2026-04-23T17:00:00+08:00",
                            "students": [
                                {
                                    "email": "dual@example.com",
                                    "courses": [
                                        {
                                            "name": "OLD Bundle Access",
                                            "status": "enrolled",
                                        }
                                    ],
                                }
                            ],
                        },
                    ):
                        report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["unmatched"], 0)

    def test_compare_payments_can_match_legacy_file_delivery_amount(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                    xendit_payments.upsert_payment_records([
                        {
                            "status": "paid",
                            "email": "jmjtechalicia@gmail.com",
                            "course": "DIY Hybrid Solar Setup",
                            "amount": "PHP 497",
                            "source": "xendit_payment_webhook",
                            "xendit_payment_id": "py-1kw-123",
                            "date": "2026-04-23T16:58:00Z",
                            "paid_at": "2026-04-23T16:58:00Z",
                        }
                    ])
                    with patch("enrollment_checker.xendit_api.available", return_value=False), patch(
                        "enrollment_checker.gmail_imap.available", return_value=True
                    ), patch(
                        "enrollment_checker.list_recent_systeme_enrolments",
                        return_value=[],
                    ), patch(
                        "enrollment_checker._search_enrolment_messages",
                        return_value=[],
                    ), patch(
                        "enrollment_checker.load_student_store",
                        return_value={
                            "checked_at": "2026-04-23T17:00:00+08:00",
                            "students": [
                                {
                                    "email": "jmjtechalicia@gmail.com",
                                    "courses": [
                                        {
                                            "name": "OLD Course Access",
                                            "status": "enrolled",
                                        }
                                    ],
                                }
                            ],
                        },
                    ):
                        report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["unmatched"], 0)

    def test_compare_payments_confirms_unmatched_via_systeme_contact_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                    xendit_payments.upsert_payment_records([
                        {
                            "status": "paid",
                            "email": "student@example.com",
                            "course": "MikroTik Hybrid",
                            "amount": "PHP 1499",
                            "source": "xendit_payment_webhook",
                            "xendit_payment_id": "py-123",
                            "date": "2026-04-18T01:35:00Z",
                            "paid_at": "2026-04-18T01:35:00Z",
                        }
                    ])
                    with patch("enrollment_checker.xendit_api.available", return_value=False):
                        with patch("enrollment_checker.gmail_imap.available", return_value=True):
                            with patch(
                                "enrollment_checker.list_recent_systeme_enrolments",
                                return_value=[],
                            ), patch(
                                "enrollment_checker._search_enrolment_messages",
                                return_value=[],
                            ), patch(
                                "enrollment_checker.load_student_store",
                                return_value={"checked_at": "", "students": []},
                            ), patch(
                                "enrollment_checker.systeme_api.available",
                                return_value=True,
                            ), patch(
                                "enrollment_checker.systeme_api.find_contact_by_email",
                                return_value={
                                    "email": "student@example.com",
                                    "tags": [{"name": "XENDIT_HYBRID_PAID"}],
                                },
                            ):
                                report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["unmatched"], 0)

    def test_compare_payments_does_not_confirm_via_wrong_systeme_contact_tag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(enrollment_checker, "DATA_DIR", tmpdir):
                with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                    xendit_payments.upsert_payment_records([
                        {
                            "status": "paid",
                            "email": "student@example.com",
                            "course": "MikroTik Hybrid",
                            "amount": "PHP 1499",
                            "source": "xendit_payment_webhook",
                            "xendit_payment_id": "py-123",
                            "date": "2026-04-18T01:35:00Z",
                            "paid_at": "2026-04-18T01:35:00Z",
                        }
                    ])
                    with patch("enrollment_checker.xendit_api.available", return_value=False):
                        with patch("enrollment_checker.gmail_imap.available", return_value=True):
                            with patch(
                                "enrollment_checker.list_recent_systeme_enrolments",
                                return_value=[],
                            ), patch(
                                "enrollment_checker._search_enrolment_messages",
                                return_value=[],
                            ), patch(
                                "enrollment_checker.load_student_store",
                                return_value={"checked_at": "", "students": []},
                            ), patch(
                                "enrollment_checker.systeme_api.available",
                                return_value=True,
                            ), patch(
                                "enrollment_checker.systeme_api.find_contact_by_email",
                                return_value={
                                    "email": "student@example.com",
                                    "tags": [{"name": "XENDIT_BASIC_PAID"}],
                                },
                            ):
                                report = enrollment_checker.compare_payments_vs_enrolments(days_back=7)

        self.assertEqual(report["matched"], 0)
        self.assertEqual(report["unmatched"], 1)

    def test_format_comparison_telegram_omits_matched_student_dump(self):
        report = {
            "checked_at": "2026-04-18T16:00:00+08:00",
            "total_payments": 2,
            "total_enrolments": 2,
            "total_enrolled_students": 1,
            "matched": 2,
            "unmatched": 0,
            "matched_students": [
                {"email": "juan@example.com", "course": "Course A"},
                {"email": "maria@example.com", "course": "Course B"},
            ],
            "unmatched_students": [],
        }

        message = enrollment_checker.format_comparison_telegram(report)

        self.assertNotIn("Matched Students", message)
        self.assertNotIn("juan@example.com", message)
        self.assertIn("Enrolled Students: 1", message)
        self.assertIn("Enrolled Course Rows: 2", message)


if __name__ == "__main__":
    unittest.main()
