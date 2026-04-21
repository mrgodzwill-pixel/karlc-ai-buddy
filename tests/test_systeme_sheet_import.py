import os
import tempfile
import unittest
from unittest.mock import patch

import systeme_students
import systeme_sheet_import


class SystemeSheetImportTests(unittest.TestCase):
    def test_import_summary_csv_text_merges_courses_and_tags(self):
        csv_text = (
            "email,courses,tags\n"
            "\"juan@example.com\",\"• MikroTik QuickStart: Configure From Scratch\n• Hybrid Access Combo: IPoE + PPPoE\",\"• QUICKSTART_PAID\n• HYBRID_PAID\"\n"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = os.path.join(tmpdir, "systeme_students.json")
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file):
                result = systeme_sheet_import.import_summary_csv_text(csv_text, source_label="test")
                store = systeme_students.load_student_store()

        self.assertTrue(result["ok"])
        self.assertEqual(result["rows_scanned"], 1)
        self.assertEqual(result["students_imported"], 1)
        self.assertEqual(len(store["students"]), 1)
        student = store["students"][0]
        self.assertEqual(student["email"], "juan@example.com")
        self.assertEqual(
            [course["name"] for course in student["courses"]],
            [
                "MikroTik QuickStart: Configure From Scratch",
                "Hybrid Access Combo: IPoE + PPPoE",
            ],
        )
        self.assertEqual(student["tags"], ["QUICKSTART_PAID", "HYBRID_PAID"])
        self.assertTrue(all(course["status"] == "enrolled" for course in student["courses"]))

    def test_import_summary_csv_text_accepts_comma_separated_courses_and_tags(self):
        csv_text = (
            "email,courses,tags\n"
            "\"juan@example.com\",\"MikroTik QuickStart: Configure From Scratch, Hybrid Access Combo: IPoE + PPPoE\",\"QUICKSTART_PAID, HYBRID_PAID\"\n"
        )

        with patch("systeme_sheet_import.upsert_systeme_student_snapshot", return_value=True) as mock_upsert:
            result = systeme_sheet_import.import_summary_csv_text(csv_text, source_label="test.csv")

        self.assertTrue(result["ok"])
        snapshot = mock_upsert.call_args[0][0]
        self.assertEqual(
            [course["name"] for course in snapshot["courses"]],
            [
                "MikroTik QuickStart: Configure From Scratch",
                "Hybrid Access Combo: IPoE + PPPoE",
            ],
        )
        self.assertEqual(snapshot["tags"], ["QUICKSTART_PAID", "HYBRID_PAID"])

    def test_import_summary_csv_text_enriches_name_and_phone_from_xendit(self):
        csv_text = (
            "email,courses,tags\n"
            "\"juan@example.com\",\"• MikroTik QuickStart: Configure From Scratch\",\"• QUICKSTART_PAID\"\n"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = os.path.join(tmpdir, "systeme_students.json")
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file), patch(
                "systeme_sheet_import.find_payment_by_email",
                return_value={"payer_name": "Juan Dela Cruz", "phone": "639171234567"},
            ):
                result = systeme_sheet_import.import_summary_csv_text(csv_text, source_label="test")
                store = systeme_students.load_student_store()

        self.assertTrue(result["ok"])
        self.assertEqual(result["xendit_matches"], 1)
        student = store["students"][0]
        self.assertEqual(student["name"], "Juan Dela Cruz")
        self.assertEqual(student["phone"], "639171234567")


if __name__ == "__main__":
    unittest.main()
