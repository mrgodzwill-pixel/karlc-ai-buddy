import importlib
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import systeme_students
import xendit_payments

if "requests" not in sys.modules:
    fake_requests = types.ModuleType("requests")
    fake_requests.get = lambda *args, **kwargs: None
    fake_requests.post = lambda *args, **kwargs: None
    sys.modules["requests"] = fake_requests

data_queries = importlib.import_module("data_queries")


class DataQueriesTests(unittest.TestCase):
    def test_build_data_context_includes_payment_lookup_for_specific_payer_query(self):
        message = {
            "subject": "INVOICE PAID: karlcw-quickstart-799-123",
            "date": "Sat, 18 Apr 2026 07:30:00 +0800",
            "body": """
            Payer Name: Juan Dela Cruz
            Payer Email: juan@example.com
            Mobile Number: 0917-123-4567
            Total: PHP 799
            """,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                xendit_payments.sync_payment_records([message], checked_at="2026-04-18T09:00:00+08:00")
                context = data_queries.build_data_context("May payment ba si Juan Dela Cruz?")

        self.assertIn("[PAYMENT LOOKUP]", context)
        self.assertIn("Juan Dela Cruz", context)
        self.assertIn("juan@example.com", context)
        self.assertIn("Stored Xendit payments last synced: 2026-04-18 09:00 PHT", context)

    def test_get_payment_lookup_refreshes_xendit_sync_when_api_available(self):
        with patch("data_queries.xendit_api.available", return_value=True):
            with patch("data_queries.sync_recent_invoice_payments") as sync_recent:
                with patch("data_queries.format_payment_lookup_summary", return_value={"summary": "ok"}):
                    result = data_queries.get_payment_lookup("check payment for juan@example.com")

        sync_recent.assert_called_once_with(days_back=30)
        self.assertEqual(result["summary"], "ok")

    def test_build_data_context_includes_systeme_student_lookup(self):
        payload = {
            "type": "contact.course.enrolled",
            "data": {
                "course": {"id": 44685, "name": "MikroTik OSPF"},
                "contact": {
                    "id": 29150265,
                    "email": "learner@example.com",
                    "fields": {
                        "first_name": "Learner",
                        "surname": "One",
                    },
                },
            },
            "created_at": "2026-04-21T11:12:29+00:00",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = os.path.join(tmpdir, "systeme_students.json")
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file):
                systeme_students.upsert_systeme_student(payload)
                context = data_queries.build_data_context(
                    "What course is learner@example.com enrolled in sa systeme?"
                )

        self.assertIn("[SYSTEME STUDENT LOOKUP]", context)
        self.assertIn("MikroTik OSPF", context)
        self.assertIn("learner@example.com", context)


if __name__ == "__main__":
    unittest.main()
