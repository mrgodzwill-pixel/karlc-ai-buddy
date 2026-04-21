import tempfile
import unittest
from unittest.mock import patch

import systeme_students


class SystemeStudentsTests(unittest.TestCase):
    def test_new_sale_stores_student_and_purchased_course(self):
        payload = {
            "type": "customer.sale.completed",
            "data": {
                "customer": {
                    "id": 616824,
                    "email": "juan@example.com",
                    "fields": {
                        "first_name": "Juan",
                        "surname": "Dela Cruz",
                        "phone_number": "09171234567",
                    },
                },
                "offer_price_plan": {
                    "name": "MikroTik Hybrid",
                    "direct_charge_amount": 1499,
                    "currency": "php",
                },
                "order": {"id": 612661, "created_at": "2026-04-21T10:23:17+00:00"},
                "order_item": {
                    "resources": [
                        {
                            "type": "membership_course",
                            "data": {"id": 111, "name": "MikroTik Hybrid"},
                        }
                    ]
                },
            },
            "created_at": "2026-04-21T10:23:33+00:00",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = f"{tmpdir}/systeme_students.json"
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file):
                student = systeme_students.upsert_systeme_student(payload)
                store = systeme_students.load_student_store()

        self.assertEqual(student["email"], "juan@example.com")
        self.assertEqual(student["name"], "Juan Dela Cruz")
        self.assertEqual(student["phone"], "09171234567")
        self.assertEqual(store["students"][0]["courses"][0]["name"], "MikroTik Hybrid")
        self.assertEqual(store["students"][0]["courses"][0]["status"], "sold")

    def test_course_enrolled_event_creates_recent_enrolment(self):
        payload = {
            "type": "contact.course.enrolled",
            "data": {
                "course": {"id": 44685, "name": "MikroTik OSPF"},
                "contact": {
                    "id": 29150265,
                    "email": "learner@example.com",
                    "fields": {
                        "first_name": "Learner",
                        "surname": "One",
                    },
                },
                "access_type": "full_access",
            },
            "created_at": "2026-04-21T11:12:29+00:00",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = f"{tmpdir}/systeme_students.json"
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file):
                systeme_students.upsert_systeme_student(payload)
                enrolments = systeme_students.list_recent_enrolments(days_back=30)

        self.assertEqual(len(enrolments), 1)
        self.assertEqual(enrolments[0]["email"], "learner@example.com")
        self.assertEqual(enrolments[0]["course"], "MikroTik OSPF")

    def test_student_lookup_returns_courses(self):
        payload = {
            "type": "contact.course.enrolled",
            "data": {
                "course": {"id": 44685, "name": "MikroTik OSPF"},
                "contact": {
                    "id": 29150265,
                    "email": "learner@example.com",
                    "fields": {
                        "first_name": "Learner",
                        "surname": "One",
                    },
                },
            },
            "created_at": "2026-04-21T11:12:29+00:00",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = f"{tmpdir}/systeme_students.json"
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file):
                systeme_students.upsert_systeme_student(payload)
                summary = systeme_students.format_student_lookup_summary(
                    "what course is learner@example.com enrolled in?"
                )

        self.assertEqual(summary["count"], 1)
        self.assertIn("MikroTik OSPF", summary["summary"])
        self.assertNotIn("no phone", summary["summary"])

    def test_course_enrollment_summary_groups_students_by_course(self):
        payload_a = {
            "type": "contact.course.enrolled",
            "data": {
                "course": {"id": 1, "name": "MikroTik Basic"},
                "contact": {
                    "email": "a@example.com",
                    "fields": {"first_name": "Alpha", "surname": "One"},
                },
            },
            "created_at": "2026-04-21T10:00:00+00:00",
        }
        payload_b = {
            "type": "contact.course.enrolled",
            "data": {
                "course": {"id": 1, "name": "MikroTik Basic"},
                "contact": {
                    "email": "b@example.com",
                    "fields": {"first_name": "Bravo", "surname": "Two"},
                },
            },
            "created_at": "2026-04-21T11:00:00+00:00",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store_file = f"{tmpdir}/systeme_students.json"
            with patch.object(systeme_students, "SYSTEME_STUDENTS_FILE", store_file):
                systeme_students.upsert_systeme_student(payload_a)
                systeme_students.upsert_systeme_student(payload_b)
                summary = systeme_students.format_course_enrollment_summary()
                filtered = systeme_students.format_course_enrollment_summary("basic")

        self.assertIn("*MikroTik Basic* (2)", summary)
        self.assertIn("Alpha One - a@example.com", summary)
        self.assertIn("Bravo Two - b@example.com", summary)
        self.assertIn("*MikroTik Basic* (2)", filtered)


if __name__ == "__main__":
    unittest.main()
