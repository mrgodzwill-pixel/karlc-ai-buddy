import importlib
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
