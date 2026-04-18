import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

if "requests" not in sys.modules:
    fake_requests = types.ModuleType("requests")
    fake_requests.request = lambda *args, **kwargs: None
    sys.modules["requests"] = fake_requests

import xendit_payments
import xendit_sync


class XenditSyncTests(unittest.TestCase):
    def test_process_invoice_webhook_stores_paid_invoice(self):
        payload = {
            "id": "inv-123",
            "external_id": "order-123",
            "status": "PAID",
            "payment_method": "BANK_TRANSFER",
            "payment_channel": "BPI",
            "amount": 799,
            "paid_amount": 799,
            "payer_email": "juan@example.com",
            "description": "MikroTik Basic (QuickStart)",
            "paid_at": "2026-04-18T01:30:00Z",
            "currency": "PHP",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                record = xendit_sync.process_invoice_webhook(payload)
                store = xendit_payments.load_payment_store()

        self.assertEqual(record["xendit_invoice_id"], "inv-123")
        self.assertEqual(record["email"], "juan@example.com")
        self.assertEqual(record["amount"], "PHP 799")
        self.assertEqual(store["payments"][0]["external_id"], "order-123")

    def test_process_payment_webhook_enriches_customer_name_and_phone(self):
        payload = {
            "event": "payment.capture",
            "data": {
                "payment_id": "py-123",
                "status": "SUCCEEDED",
                "request_amount": 799,
                "currency": "PHP",
                "description": "MikroTik Basic (QuickStart)",
                "customer_id": "cust-123",
                "reference_id": "order-123",
                "channel_code": "GCASH",
                "updated": "2026-04-18T01:35:00Z",
            },
        }
        customer = {
            "id": "cust-123",
            "email": "juan@example.com",
            "mobile_number": "+639171234567",
            "individual_detail": {
                "given_names": "Juan",
                "surname": "Dela Cruz",
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                with patch("xendit_sync.xendit_api.available", return_value=True):
                    with patch("xendit_sync.xendit_api.get_customer", return_value=customer):
                        record = xendit_sync.process_payment_webhook(payload)

        self.assertEqual(record["xendit_payment_id"], "py-123")
        self.assertEqual(record["payer_name"], "Juan Dela Cruz")
        self.assertEqual(record["email"], "juan@example.com")
        self.assertEqual(record["phone_normalized"], "639171234567")

    def test_process_payment_webhook_falls_back_to_payload_contact_fields(self):
        payload = {
            "event": "payment.capture",
            "data": {
                "payment_id": "py-456",
                "status": "SUCCEEDED",
                "request_amount": 1499,
                "currency": "PHP",
                "description": "MikroTik Hybrid",
                "reference_id": "order-456",
                "channel_code": "GCASH",
                "payment_details": {
                    "payer_name": "Maria Clara",
                    "payer_account_number": "09171234567",
                },
                "metadata": {
                    "payer_email": "maria@example.com",
                    "course": "MikroTik Hybrid",
                },
                "updated": "2026-04-18T01:35:00Z",
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                with patch("xendit_sync.xendit_api.available", return_value=False):
                    record = xendit_sync.process_payment_webhook(payload)

        self.assertEqual(record["payer_name"], "Maria Clara")
        self.assertEqual(record["email"], "maria@example.com")
        self.assertEqual(record["phone_normalized"], "639171234567")


if __name__ == "__main__":
    unittest.main()
