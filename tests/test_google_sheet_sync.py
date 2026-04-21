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
        self.assertEqual(
            row[1],
            "MikroTik QuickStart: Configure From Scratch, Hybrid Access Combo: IPoE + PPPoE",
        )
        self.assertEqual(row[2], "QUICKSTART_PAID, HYBRID_PAID")
        self.assertEqual(row[3], "Juan Dela Cruz")
        self.assertEqual(row[4], "639171234567")

    def test_student_row_values_excludes_discount_verification_tag_and_cleans_bullets(self):
        google_sheet_sync = importlib.import_module("google_sheet_sync")

        row = google_sheet_sync._student_row_values(
            {
                "email": "juan@example.com",
                "name": "Juan Dela Cruz",
                "phone": "639171234567",
                "courses": [
                    {"name": "â¢ MikroTik QuickStart: Configure From Scratch"},
                    {"name": "Ã¢ÂÂ¢ Hybrid Access Combo: IPoE + PPPoE"},
                ],
                "tags": ["500OFF_FOR_VERIFICATION", "500OFF_VERIFIED", "â¢ QUICKSTART_PAID"],
            }
        )

        self.assertEqual(
            row[1],
            "MikroTik QuickStart: Configure From Scratch, Hybrid Access Combo: IPoE + PPPoE",
        )
        self.assertEqual(row[2], "QUICKSTART_PAID")

    def test_student_row_values_splits_legacy_combined_entries(self):
        google_sheet_sync = importlib.import_module("google_sheet_sync")

        row = google_sheet_sync._student_row_values(
            {
                "email": "legacy@example.com",
                "courses": [
                    {
                        "name": "â¢ MikroTik QuickStart: Configure From Scratch, Ã¢ÂÂ¢ Hybrid Access Combo: IPoE + PPPoE, Hybrid Access Combo: IPoE + PPPoE"
                    }
                ],
                "tags": [
                    "500OFF_FOR_VERIFICATION, â¢ QUICKSTART_PAID, Ã¢ÂÂ¢ HYBRID_PAID, HYBRID_PAID"
                ],
            }
        )

        self.assertEqual(
            row[1],
            "MikroTik QuickStart: Configure From Scratch, Hybrid Access Combo: IPoE + PPPoE",
        )
        self.assertEqual(row[2], "QUICKSTART_PAID, HYBRID_PAID")

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

    def test_sync_all_students_batches_updates_and_appends(self):
        google_sheet_sync = importlib.import_module("google_sheet_sync")

        students = {
            "students": [
                {
                    "email": "existing@example.com",
                    "name": "Existing Updated",
                    "phone": "639111111111",
                    "courses": [{"name": "Course A"}],
                    "tags": ["TAG_A"],
                },
                {
                    "email": "new@example.com",
                    "name": "New Student",
                    "phone": "639222222222",
                    "courses": [{"name": "Course B"}],
                    "tags": ["TAG_B"],
                },
            ]
        }

        existing_sheet_rows = [
            ["email", "courses", "tags", "name", "phone"],
            ["existing@example.com", "Old Course", "OLD_TAG", "Old Name", ""],
        ]

        with patch.object(google_sheet_sync, "available", return_value=True), \
             patch.object(google_sheet_sync, "load_student_store", return_value=students), \
             patch.object(google_sheet_sync, "_get_sheet_values", return_value=existing_sheet_rows), \
             patch.object(google_sheet_sync, "_update_values") as mock_update_values, \
             patch.object(google_sheet_sync, "_batch_update_values") as mock_batch_update, \
             patch.object(google_sheet_sync, "_append_values") as mock_append:
            result = google_sheet_sync.sync_all_students()

        self.assertTrue(result["ok"])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["appended"], 1)
        mock_update_values.assert_not_called()
        mock_batch_update.assert_called_once()
        mock_append.assert_called_once()


if __name__ == "__main__":
    unittest.main()
