import importlib
import os
import unittest
from unittest.mock import patch


class GoogleSheetSyncTests(unittest.TestCase):
    def test_student_row_values_formats_courses_tags_name_and_phone(self):
        google_sheet_sync = importlib.import_module("google_sheet_sync")

        row = google_sheet_sync._student_row_values(
            {
                "email": "juan@example.com",
                "name": "Juan Dela Cruz",
                "phone": "639171234567",
                "courses": [
                    {"name": "MikroTik QuickStart: Configure From Scratch"},
                    {"name": "Hybrid Access Combo: IPoE + PPPoE"},
                ],
                "tags": ["QUICKSTART_PAID", "HYBRID_PAID"],
            }
        )

        self.assertEqual(row[0], "juan@example.com")
        self.assertIn("• MikroTik QuickStart: Configure From Scratch", row[1])
        self.assertIn("• Hybrid Access Combo: IPoE + PPPoE", row[1])
        self.assertEqual(row[3], "Juan Dela Cruz")
        self.assertEqual(row[4], "639171234567")

    def test_available_requires_sheet_id_and_google_credentials(self):
        with patch.dict(
            os.environ,
            {
                "SYSTEME_STUDENTS_SHEET_ID": "sheet123",
                "GOOGLE_SERVICE_ACCOUNT_JSON": '{"client_email":"bot@example.com","private_key":"fake"}',
            },
            clear=False,
        ):
            import config

            importlib.reload(config)
            import google_sheet_sync

            importlib.reload(google_sheet_sync)
            self.assertTrue(google_sheet_sync.available())


if __name__ == "__main__":
    unittest.main()
