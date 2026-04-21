import os
import tempfile
import unittest
from unittest.mock import patch

import systeme_backfill
import systeme_students


class SystemeBackfillTests(unittest.TestCase):
    def test_run_systeme_backfill_imports_historical_enrollments(self):
        courses = [{"id": 1, "name": "MikroTik Hybrid"}]
        contacts = [
            {
                "id": 10,
                "email": "juan@example.com",
                "fields": {
                    "first_name": "Juan",
                    "surname": "Dela Cruz",
                    "phone_number": "09171234567",
                },
            }
        ]
        enrollments = [
            {
                "id": 99,
                "contact": {"id": 10},
                "course": {"id": 1},
                "created_at": "2026-04-21T10:00:00+00:00",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = os.path.join(tmpdir, "systeme_students.json")
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file), patch(
                "systeme_backfill.systeme_api.available", return_value=True
            ), patch(
                "systeme_backfill.systeme_api.list_courses", return_value=courses
            ), patch(
                "systeme_backfill.systeme_api.list_contacts", return_value=contacts
            ), patch(
                "systeme_backfill.systeme_api.list_enrollments", return_value=enrollments
            ):
                result = systeme_backfill.run_systeme_backfill()
                store = systeme_students.load_student_store()

        self.assertTrue(result["ok"])
        self.assertEqual(result["students_imported"], 1)
        self.assertEqual(store["students"][0]["email"], "juan@example.com")
        self.assertEqual(store["students"][0]["courses"][0]["name"], "MikroTik Hybrid")
        self.assertEqual(store["students"][0]["courses"][0]["status"], "enrolled")

    def test_run_systeme_backfill_skips_enrollment_without_email(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = os.path.join(tmpdir, "systeme_students.json")
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file), patch(
                "systeme_backfill.systeme_api.available", return_value=True
            ), patch(
                "systeme_backfill.systeme_api.list_courses", return_value=[]
            ), patch(
                "systeme_backfill.systeme_api.list_contacts", return_value=[]
            ), patch(
                "systeme_backfill.systeme_api.list_enrollments",
                return_value=[{"id": 1, "course": {"id": 55, "name": "Unknown Course"}}],
            ):
                result = systeme_backfill.run_systeme_backfill()
                store = systeme_students.load_student_store()

        self.assertTrue(result["ok"])
        self.assertEqual(result["students_imported"], 0)
        self.assertEqual(result["skipped_without_email"], 1)
        self.assertEqual(store["students"], [])


if __name__ == "__main__":
    unittest.main()
