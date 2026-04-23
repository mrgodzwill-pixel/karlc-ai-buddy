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
        tag = {"id": 33, "name": "HYBRID_PAID"}

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
        self.assertEqual(result["tag"]["name"], "HYBRID_PAID")
        self.assertEqual(
            store["students"][0]["courses"][0]["name"],
            "Hybrid Access Combo: IPoE + PPPoE",
        )
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
        tag = {"id": 88, "name": "BUNDLE_PAID"}

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
        self.assertEqual(result["tag"]["name"], "BUNDLE_PAID")

    def test_enroll_student_maps_invoice_style_old_title_to_quickstart_tag(self):
        contact = {"id": 501, "email": "ericjamison21@gmail.com"}
        tag = {"id": 99, "name": "QUICKSTART_PAID"}
        course_query = "Step-by-step kung paano mag-setup ng MikroTik RouterOS from scratch. - Invoice for Eric John Jamison"

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
                    email="ericjamison21@gmail.com",
                    course_query=course_query,
                    name="Eric John Jamison",
                )

        assign_tag.assert_called_once_with("501", "99")
        self.assertEqual(result["tag"]["name"], "QUICKSTART_PAID")

    def test_enroll_student_uses_exact_configured_tag_not_partial_xendit_tag(self):
        contact = {"id": 501, "email": "juan@example.com"}
        configured_tag = {"id": 77, "name": "QUICKSTART_PAID"}
        ticket = {
            "id": 170,
            "student_name": "Juan Dela Cruz",
            "student_email": "juan@example.com",
            "phone_number": "09171234567",
            "course_title": "Invoice Paid: karlc-mikrotik-basic-799-1776000272022",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = os.path.join(tmpdir, "systeme_students.json")
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file), patch(
                "systeme_manual.systeme_api.available", return_value=True
            ), patch(
                "systeme_manual.get_ticket", return_value=ticket
            ), patch(
                "systeme_manual.systeme_api.create_contact", return_value=contact
            ), patch(
                "systeme_manual.systeme_api.find_tag_by_name", return_value=configured_tag
            ) as find_tag_by_name, patch(
                "systeme_manual.systeme_api.assign_tag_to_contact", return_value={}
            ) as assign_tag:
                result = systeme_manual.enroll_student(ticket_id=170, resolve_ticket_on_success=False)

        find_tag_by_name.assert_called_once_with("QUICKSTART_PAID", exact_only=True)
        assign_tag.assert_called_once_with("501", "77")
        self.assertEqual(result["tag"]["name"], "QUICKSTART_PAID")

    def test_enroll_student_recovers_missing_ticket_email_from_xendit_store(self):
        contact = {"id": 501, "email": "rickyandeo90@gmail.com"}
        configured_tag = {"id": 66, "name": "PISOWIFI_PAID"}
        ticket = {
            "id": 182,
            "type": "enrollment_incomplete",
            "student_name": "Ricky Andeo",
            "student_email": "",
            "phone_number": "",
            "course_title": "10G Core Part 3: Centralized Pisowifi Setup",
            "price": "PHP 1,500",
            "payment_method": "xendit",
            "date_paid": "2026-04-21T23:53:00+08:00",
        }
        payment_store = {
            "payments": [
                {
                    "email": "rickyandeo90@gmail.com",
                    "payer_name": "Ricky Andeo",
                    "phone": "639777235690",
                    "course": "10G Core Part 3: Centralized Pisowifi Setup",
                    "amount": "PHP 1,500",
                    "status": "paid",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = os.path.join(tmpdir, "systeme_students.json")
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file), patch(
                "systeme_manual.systeme_api.available", return_value=True
            ), patch(
                "systeme_manual.get_ticket", side_effect=[ticket, dict(ticket, student_email="rickyandeo90@gmail.com")]
            ), patch(
                "systeme_manual.load_payment_store", return_value=payment_store
            ), patch(
                "systeme_manual.update_ticket_contact_details"
            ) as update_ticket, patch(
                "systeme_manual.systeme_api.create_contact", return_value=contact
            ), patch(
                "systeme_manual.systeme_api.find_tag_by_name", return_value=configured_tag
            ), patch(
                "systeme_manual.systeme_api.assign_tag_to_contact", return_value={}
            ):
                result = systeme_manual.enroll_student(ticket_id=182, resolve_ticket_on_success=False)

        update_ticket.assert_called_once()
        self.assertEqual(result["email"], "rickyandeo90@gmail.com")
        self.assertEqual(result["phone_number"], "639777235690")
        self.assertEqual(result["tag"]["name"], "PISOWIFI_PAID")

    def test_enroll_student_ticket_without_recoverable_email_raises_clear_error(self):
        ticket = {
            "id": 182,
            "type": "enrollment_incomplete",
            "student_name": "Ricky Andeo",
            "student_email": "",
            "phone_number": "",
            "course_title": "10G Core Part 3: Centralized Pisowifi Setup",
            "price": "PHP 1,500",
        }

        with patch(
            "systeme_manual.systeme_api.available", return_value=True
        ), patch(
            "systeme_manual.get_ticket", return_value=ticket
        ), patch(
            "systeme_manual.load_payment_store", return_value={"payments": []}
        ):
            with self.assertRaisesRegex(ValueError, "couldn't recover one from Xendit"):
                systeme_manual.enroll_student(ticket_id=182, resolve_ticket_on_success=False)

    def test_enroll_student_rejects_non_enrollment_ticket_ids(self):
        ticket = {
            "id": 25,
            "type": "support_email",
            "student_name": "Juan Dela Cruz",
            "student_email": "juan@example.com",
            "course_title": "Help me enroll",
        }

        with patch("systeme_manual.systeme_api.available", return_value=True), patch(
            "systeme_manual.get_ticket", return_value=ticket
        ):
            with self.assertRaisesRegex(ValueError, "not an enrollment ticket"):
                systeme_manual.enroll_student(ticket_id=25, resolve_ticket_on_success=False)

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
