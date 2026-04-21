import os
import importlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import systeme_students

if "requests" not in sys.modules:
    fake_requests = types.ModuleType("requests")
    fake_requests.request = lambda *args, **kwargs: None
    sys.modules["requests"] = fake_requests

systeme_manual = importlib.import_module("systeme_manual")


class SystemeManualTests(unittest.TestCase):
    def test_add_contact_from_ticket_uses_ticket_details(self):
        ticket = {
            "id": 12,
            "student_name": "Juan Dela Cruz",
            "student_email": "juan@example.com",
            "phone_number": "09171234567",
            "course_title": "MikroTik Hybrid",
        }
        contact = {"id": 501, "email": "juan@example.com"}

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = os.path.join(tmpdir, "systeme_students.json")
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file), patch(
                "systeme_manual.systeme_api.available", return_value=True
            ), patch(
                "systeme_manual.get_ticket", return_value=ticket
            ), patch(
                "systeme_manual.systeme_api.create_contact", return_value=contact
            ):
                result = systeme_manual.add_contact(ticket_id=12)
                store = systeme_students.load_student_store()

        self.assertEqual(result["email"], "juan@example.com")
        self.assertEqual(store["students"][0]["email"], "juan@example.com")
        self.assertEqual(store["students"][0]["phone"], "09171234567")

    def test_enroll_student_from_ticket_resolves_ticket(self):
        ticket = {
            "id": 15,
            "student_name": "Juan Dela Cruz",
            "student_email": "juan@example.com",
            "phone_number": "09171234567",
            "course_title": "MikroTik Hybrid",
        }
        contact = {"id": 501, "email": "juan@example.com"}
        tag = {"id": 33, "name": "MikroTik Hybrid"}

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = os.path.join(tmpdir, "systeme_students.json")
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file), patch(
                "systeme_manual.systeme_api.available", return_value=True
            ), patch(
                "systeme_manual.get_ticket", return_value=ticket
            ), patch(
                "systeme_manual.systeme_api.create_contact", return_value=contact
            ), patch(
                "systeme_manual.systeme_api.find_tag_by_name", return_value=tag
            ), patch(
                "systeme_manual.systeme_api.assign_tag_to_contact", return_value={}
            ), patch(
                "systeme_manual.resolve_ticket", return_value=(dict(ticket, status="done"), "resolved")
            ) as resolve_ticket:
                result = systeme_manual.enroll_student(ticket_id=15)
                store = systeme_students.load_student_store()

        resolve_ticket.assert_called_once_with(15)
        self.assertEqual(result["tag"]["name"], "MikroTik Hybrid")
        self.assertEqual(store["students"][0]["courses"][0]["name"], "MikroTik Hybrid")
        self.assertEqual(store["students"][0]["courses"][0]["status"], "sold")

    def test_enroll_student_uses_special_bundle_tag_mapping(self):
        contact = {"id": 501, "email": "juan@example.com"}
        tag = {"id": 77, "name": "BUNDLE4_PAID"}

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = os.path.join(tmpdir, "systeme_students.json")
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file), patch(
                "systeme_manual.systeme_api.available", return_value=True
            ), patch(
                "systeme_manual.systeme_api.create_contact", return_value=contact
            ), patch(
                "systeme_manual.systeme_api.find_tag_by_name", return_value=tag
            ), patch(
                "systeme_manual.systeme_api.assign_tag_to_contact", return_value={}
            ) as assign_tag:
                result = systeme_manual.enroll_student(
                    email="juan@example.com",
                    course_query="Complete MikroTik Mastery Bundle",
                    name="Juan Dela Cruz",
                )

        assign_tag.assert_called_once_with("501", "77")
        self.assertEqual(result["tag"]["name"], "BUNDLE4_PAID")

    def test_enroll_student_uses_old_bundle_fallback_tag_for_unknown_legacy_course(self):
        contact = {"id": 501, "email": "juan@example.com"}
        tag = {"id": 88, "name": "OLD_BUNDLE"}

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = os.path.join(tmpdir, "systeme_students.json")
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file), patch(
                "systeme_manual.systeme_api.available", return_value=True
            ), patch(
                "systeme_manual.systeme_api.create_contact", return_value=contact
            ), patch(
                "systeme_manual.systeme_api.find_tag_by_name", return_value=tag
            ), patch(
                "systeme_manual.systeme_api.assign_tag_to_contact", return_value={}
            ) as assign_tag:
                result = systeme_manual.enroll_student(
                    email="juan@example.com",
                    course_query="Some Old 3-in-1 Bundle 2024",
                    name="Juan Dela Cruz",
                )

        assign_tag.assert_called_once_with("501", "88")
        self.assertEqual(result["tag"]["name"], "OLD_BUNDLE")

    def test_sanitize_name_fields_truncates_long_names(self):
        first_name, surname, full_name = systeme_manual._sanitize_name_fields(
            "A" * 120,
            email="juan@example.com",
        )

        self.assertLessEqual(len(first_name), 64)
        self.assertLessEqual(len(full_name), 64)
        self.assertTrue(full_name)


if __name__ == "__main__":
    unittest.main()
