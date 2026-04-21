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


if __name__ == "__main__":
    unittest.main()
