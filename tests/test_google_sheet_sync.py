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
        self.assertEqual(row[2], "QUICKSTART_PAID, HYBRID_PAID")

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

    def test_clean_list_value_repairs_nested_mojibake_bullet_prefix(self):
        google_sheet_sync = importlib.import_module("google_sheet_sync")
        value = "ÃÂÃÂ¢ÃÂÃÂÃÂÃÂ¢ Hybrid Access Combo: IPoE + PPPoE"
        self.assertEqual(
            google_sheet_sync._clean_list_value(value),
            "Hybrid Access Combo: IPoE + PPPoE",
        )

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
                    "courses": [{"name": "MikroTik QuickStart: Configure From Scratch"}],
                    "tags": ["QUICKSTART_PAID"],
                },
                {
                    "email": "new@example.com",
                    "name": "New Student",
                    "phone": "639222222222",
                    "courses": [{"name": "Hybrid Access Combo: IPoE + PPPoE"}],
                    "tags": ["HYBRID_PAID"],
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

    def test_sync_all_students_updates_same_row_when_student_gets_second_course(self):
        google_sheet_sync = importlib.import_module("google_sheet_sync")

        students = {
            "students": [
                {
                    "email": "same@example.com",
                    "name": "Same Student",
                    "phone": "639111111111",
                    "courses": [
                        {"name": "MikroTik QuickStart: Configure From Scratch"},
                        {"name": "MikroTik Traffic Control Basics"},
                    ],
                    "tags": ["QUICKSTART_PAID", "TRAFFIC_PAID"],
                }
            ]
        }

        existing_sheet_rows = [
            ["email", "courses", "tags", "name", "phone"],
            [
                "same@example.com",
                "MikroTik QuickStart: Configure From Scratch",
                "QUICKSTART_PAID",
                "Same Student",
                "639111111111",
            ],
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
        self.assertEqual(result["appended"], 0)
        mock_update_values.assert_not_called()
        mock_batch_update.assert_called_once()
        update_payload = mock_batch_update.call_args[0][0][0]
        self.assertEqual(update_payload["range"], "Sheet1!A2:E2")
        self.assertEqual(
            update_payload["values"][0][1],
            "MikroTik QuickStart: Configure From Scratch, MikroTik Traffic Control Basics",
        )
        self.assertEqual(update_payload["values"][0][2], "QUICKSTART_PAID, TRAFFIC_PAID")
        mock_append.assert_not_called()

    def test_sync_all_students_removes_duplicate_email_rows_before_updating(self):
        google_sheet_sync = importlib.import_module("google_sheet_sync")

        students = {
            "students": [
                {
                    "email": "dup@example.com",
                    "name": "Dup Student",
                    "phone": "639111111111",
                    "courses": [{"name": "MikroTik QuickStart: Configure From Scratch"}],
                    "tags": ["QUICKSTART_PAID"],
                }
            ]
        }

        first_read = [
            ["email", "courses", "tags", "name", "phone"],
            ["dup@example.com", "Old Course", "OLD_TAG", "Old Name", ""],
            ["dup@example.com", "Old Course", "OLD_TAG", "Old Name", ""],
        ]
        second_read = [
            ["email", "courses", "tags", "name", "phone"],
            ["dup@example.com", "Old Course", "OLD_TAG", "Old Name", ""],
        ]

        with patch.object(google_sheet_sync, "available", return_value=True), \
             patch.object(google_sheet_sync, "load_student_store", return_value=students), \
             patch.object(google_sheet_sync, "_get_sheet_values", side_effect=[first_read, second_read]), \
             patch.object(google_sheet_sync, "_delete_rows") as mock_delete_rows, \
             patch.object(google_sheet_sync, "_update_values") as mock_update_values, \
             patch.object(google_sheet_sync, "_batch_update_values") as mock_batch_update, \
             patch.object(google_sheet_sync, "_append_values") as mock_append:
            result = google_sheet_sync.sync_all_students()

        self.assertTrue(result["ok"])
        self.assertEqual(result["duplicates_removed"], 1)
        mock_delete_rows.assert_called_once_with([3])
        mock_batch_update.assert_called_once()
        mock_append.assert_not_called()
        mock_update_values.assert_not_called()

    def test_sync_student_record_removes_duplicate_rows_before_update(self):
        google_sheet_sync = importlib.import_module("google_sheet_sync")

        student = {
            "email": "dup@example.com",
            "name": "Dup Student",
            "phone": "639111111111",
            "courses": [{"name": "MikroTik QuickStart: Configure From Scratch"}],
            "tags": ["QUICKSTART_PAID"],
        }

        with patch.object(google_sheet_sync, "available", return_value=True), \
             patch.object(google_sheet_sync, "_ensure_headers"), \
             patch.object(
                 google_sheet_sync,
                 "_get_sheet_values",
                 return_value=[
                     ["email"],
                     ["dup@example.com"],
                     ["dup@example.com"],
                 ],
             ), \
             patch.object(google_sheet_sync, "_delete_rows") as mock_delete_rows, \
             patch.object(google_sheet_sync, "_update_values") as mock_update_values, \
             patch.object(google_sheet_sync, "_append_values") as mock_append:
            result = google_sheet_sync.sync_student_record(student)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "updated")
        self.assertEqual(result["duplicates_removed"], 1)
        mock_delete_rows.assert_called_once_with([3])
        mock_update_values.assert_called_once()
        mock_append.assert_not_called()

    def test_student_row_values_normalizes_invoice_style_course_titles_to_official_names_and_tags(self):
        google_sheet_sync = importlib.import_module("google_sheet_sync")

        row = google_sheet_sync._student_row_values(
            {
                "email": "romel@example.com",
                "courses": [
                    {
                        "name": "10G Core Part 3: Centralized Pisowifi Setup",
                        "status": "enrolled",
                    },
                    {
                        "name": "Build a true centralized Pisowifi system with auto select, random MAC fix, and synchronized multi-vendo deployment. - Invoice for Romel marimla",
                        "status": "enrolled",
                    },
                    {
                        "name": "Get ALL 4 MikroTik courses in one bundle! From basic setup to advanced ISP operations — lahat kasama na. Save ₱1, 050 vs buying separately. - Invoice for Maro Urbano",
                        "status": "enrolled",
                    },
                ],
            }
        )

        self.assertEqual(
            row[1],
            "10G Core Part 3: Centralized Pisowifi Setup, Complete MikroTik Mastery Bundle",
        )
        self.assertEqual(row[2], "PISOWIFI_PAID, BUNDLE4_PAID")

    def test_student_row_values_preserves_legacy_access_markers_for_sheet_baseline(self):
        google_sheet_sync = importlib.import_module("google_sheet_sync")

        row = google_sheet_sync._student_row_values(
            {
                "email": "legacy@example.com",
                "courses": [
                    {
                        "name": "OLD Bundle Access",
                        "status": "enrolled",
                    },
                    {
                        "name": "OLD Course Access",
                        "status": "enrolled",
                    },
                ],
            }
        )

        self.assertEqual(row[1], "OLD Bundle Access, OLD Course Access")
        self.assertEqual(row[2], "BUNDLE_PAID, 1KW_PAID")

    def test_sync_student_record_skips_sheet_write_for_non_enrolled_student(self):
        google_sheet_sync = importlib.import_module("google_sheet_sync")

        student = {
            "email": "pending@example.com",
            "name": "Pending Student",
            "phone": "639111111111",
            "courses": [{"name": "Course A", "status": "sold"}],
            "tags": ["TAG_A"],
        }

        with patch.object(google_sheet_sync, "available", return_value=True), \
             patch.object(google_sheet_sync, "_ensure_headers"), \
             patch.object(google_sheet_sync, "_get_sheet_values", return_value=[["email"]]), \
             patch.object(google_sheet_sync, "_delete_rows") as mock_delete_rows, \
             patch.object(google_sheet_sync, "_update_values") as mock_update_values, \
             patch.object(google_sheet_sync, "_append_values") as mock_append:
            result = google_sheet_sync.sync_student_record(student)

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "skipped_not_enrolled")
        mock_delete_rows.assert_not_called()
        mock_update_values.assert_not_called()
        mock_append.assert_not_called()


if __name__ == "__main__":
    unittest.main()
