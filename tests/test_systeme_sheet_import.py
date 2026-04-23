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

    def test_import_summary_csv_text_cleans_weird_bullets_and_ignores_discount_tag(self):
        csv_text = (
            "email,courses,tags\n"
            "\"juan@example.com\",\"â¢ MikroTik QuickStart: Configure From Scratch, Ã¢ÂÂ¢ Hybrid Access Combo: IPoE + PPPoE, Hybrid Access Combo: IPoE + PPPoE\",\"500OFF_FOR_VERIFICATION, 500OFF_VERIFIED, â¢ QUICKSTART_PAID, Ã¢ÂÂ¢ HYBRID_PAID\"\n"
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

    def test_import_summary_csv_text_normalizes_invoice_style_course_titles_to_official_values(self):
        csv_text = (
            "email,courses,tags\n"
            "\"romel@example.com\",\"10G Core Part 3: Centralized Pisowifi Setup, Build a true centralized Pisowifi system with auto select, random MAC fix, and synchronized multi-vendo deployment. - Invoice for Romel marimla, Get ALL 4 MikroTik courses in one bundle! From basic setup to advanced ISP operations — lahat kasama na. Save ₱1, 050 vs buying separately. - Invoice for Maro Urbano\",\"XENDIT_PISOWIFI_PAID, XENDIT_BUNDLE4_PAID\"\n"
        )

        with patch("systeme_sheet_import.upsert_systeme_student_snapshot", return_value=True) as mock_upsert:
            result = systeme_sheet_import.import_summary_csv_text(csv_text, source_label="test.csv")

        self.assertTrue(result["ok"])
        snapshot = mock_upsert.call_args[0][0]
        self.assertEqual(
            [course["name"] for course in snapshot["courses"]],
            [
                "10G Core Part 3: Centralized Pisowifi Setup",
                "Complete MikroTik Mastery Bundle",
            ],
        )
        self.assertEqual(snapshot["tags"], ["PISOWIFI_PAID", "BUNDLE4_PAID"])

    def test_import_summary_csv_text_recovers_courses_from_tags_when_course_text_is_missing_or_legacy(self):
        csv_text = (
            "email,courses,tags\n"
            "\"legacy@example.com\",\"OLD Bundle Access\",\"BUNDLE_PAID, XENDIT_BASIC_PAID\"\n"
        )

        with patch("systeme_sheet_import.upsert_systeme_student_snapshot", return_value=True) as mock_upsert:
            result = systeme_sheet_import.import_summary_csv_text(csv_text, source_label="legacy.csv")

        self.assertTrue(result["ok"])
        snapshot = mock_upsert.call_args[0][0]
        self.assertEqual(
            [course["name"] for course in snapshot["courses"]],
            [
                "OLD Bundle Access",
                "MikroTik QuickStart: Configure From Scratch",
            ],
        )
        self.assertEqual(snapshot["tags"], ["BUNDLE_PAID", "QUICKSTART_PAID"])


if __name__ == "__main__":
    unittest.main()
