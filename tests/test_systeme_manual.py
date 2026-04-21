import os
import tempfile
import unittest
from unittest.mock import patch

import systeme_manual
import systeme_students


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
        courses = [{"id": 33, "name": "MikroTik Hybrid"}]

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = os.path.join(tmpdir, "systeme_students.json")
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file), patch(
                "systeme_manual.systeme_api.available", return_value=True
            ), patch(
                "systeme_manual.get_ticket", return_value=ticket
            ), patch(
                "systeme_manual.systeme_api.create_contact", return_value=contact
            ), patch(
                "systeme_manual.systeme_api.list_courses", return_value=courses
            ), patch(
                "systeme_manual.systeme_api.create_enrollment", return_value={"id": 99}
            ), patch(
                "systeme_manual.resolve_ticket", return_value=(dict(ticket, status="done"), "resolved")
            ) as resolve_ticket:
                result = systeme_manual.enroll_student(ticket_id=15)
                store = systeme_students.load_student_store()

        resolve_ticket.assert_called_once_with(15)
        self.assertFalse(result["already_enrolled"])
        self.assertEqual(store["students"][0]["courses"][0]["name"], "MikroTik Hybrid")
        self.assertEqual(store["students"][0]["courses"][0]["status"], "enrolled")


if __name__ == "__main__":
    unittest.main()
