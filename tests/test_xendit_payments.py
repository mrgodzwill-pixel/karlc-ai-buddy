import os
import tempfile
import unittest
from unittest.mock import patch

import xendit_payments


class XenditPaymentsTests(unittest.TestCase):
    def test_extract_payment_record_parses_name_email_phone_and_amount(self):
        message = {
            "subject": "INVOICE PAID: karlcw-quickstart-799-123",
            "date": "Sat, 18 Apr 2026 07:30:00 +0800",
            "body": """
            <table>
              <tr><td>Payer Name</td><td>Juan Dela Cruz</td></tr>
              <tr><td>Payer Email</td><td>juan@example.com</td></tr>
              <tr><td>Mobile Number</td><td>0917-123-4567</td></tr>
              <tr><td>Payment Method</td><td>GCash</td></tr>
              <tr><td>Total</td><td>PHP 799</td></tr>
            </table>
            """,
        }

        record = xendit_payments.extract_payment_record(message)

        self.assertEqual(record["payer_name"], "Juan Dela Cruz")
        self.assertEqual(record["email"], "juan@example.com")
        self.assertEqual(record["phone"], "0917-123-4567")
        self.assertEqual(record["phone_normalized"], "639171234567")
        self.assertEqual(record["course"], "MikroTik Basic (QuickStart)")
        self.assertEqual(record["amount"], "PHP 799")
        self.assertEqual(record["payment_method"], "GCash")

    def test_sync_and_search_payments_support_name_email_and_phone(self):
        message = {
            "subject": "INVOICE PAID: karlcw-quickstart-799-123",
            "date": "Sat, 18 Apr 2026 07:30:00 +0800",
            "body": """
            Payer Name: Juan Dela Cruz
            Payer Email: juan@example.com
            Phone Number: 0917 123 4567
            Total: PHP 799
            """,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            payments_file = os.path.join(tmpdir, "xendit_payments.json")
            with patch.object(xendit_payments, "XENDIT_PAYMENTS_FILE", payments_file):
                store, parsed = xendit_payments.sync_payment_records([message, message])

                self.assertEqual(len(parsed), 2)
                self.assertEqual(len(store["payments"]), 1)

                by_name = xendit_payments.search_payment_records("May payment ba si Juan Dela Cruz?")
                by_email = xendit_payments.search_payment_records("check payment for juan@example.com")
                by_phone = xendit_payments.search_payment_records("check payment for 09171234567")

        self.assertEqual(by_name["matches"][0]["email"], "juan@example.com")
        self.assertEqual(by_email["matches"][0]["payer_name"], "Juan Dela Cruz")
        self.assertEqual(by_phone["matches"][0]["phone_normalized"], "639171234567")

    def test_build_record_from_invoice_data_reads_customer_phone(self):
        invoice = {
            "id": "inv-123",
            "payment_id": "py-123",
            "status": "PAID",
            "paid_amount": 1499,
            "currency": "PHP",
            "description": "MikroTik Hybrid",
            "paid_at": "2026-04-18T01:35:00Z",
            "customer": {
                "given_names": "Juan",
                "surname": "Dela Cruz",
                "email": "juan@example.com",
                "mobile_number": "+639171234567",
            },
        }

        record = xendit_payments.build_record_from_invoice_data(invoice)

        self.assertEqual(record["payer_name"], "Juan Dela Cruz")
        self.assertEqual(record["email"], "juan@example.com")
        self.assertEqual(record["phone_normalized"], "639171234567")


if __name__ == "__main__":
    unittest.main()
