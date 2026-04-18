import tempfile
import unittest
from unittest.mock import patch

import ticket_system


class TicketSystemResolutionTests(unittest.TestCase):
    def test_resolving_enrollment_ticket_creates_suppression_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tickets_file = f"{tmpdir}/tickets.json"
            overrides_file = f"{tmpdir}/resolved_enrollment_overrides.json"

            with patch.object(ticket_system, "TICKETS_FILE", tickets_file):
                with patch.object(ticket_system, "ENROLLMENT_RESOLUTIONS_FILE", overrides_file):
                    ticket = ticket_system.create_enrollment_ticket(
                        student_name="Student One",
                        student_email="student@example.com",
                        course_title="MikroTik Hybrid",
                        price="PHP 1499",
                        date_paid="Sat, 18 Apr 2026 10:00:00 +0800",
                    )

                    resolved_ticket, status = ticket_system.resolve_ticket(ticket["id"])
                    self.assertEqual(status, "resolved")
                    self.assertEqual(resolved_ticket["status"], "done")

                    active, suppressed = ticket_system.filter_resolved_enrollment_students([
                        {
                            "email": "student@example.com",
                            "course": "MikroTik Hybrid",
                            "amount": "PHP 1499",
                            "date": "Sat, 18 Apr 2026 10:00:00 +0800",
                        }
                    ])

                    self.assertEqual(active, [])
                    self.assertEqual(len(suppressed), 1)

    def test_done_enrollment_ticket_is_treated_as_suppressed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tickets_file = f"{tmpdir}/tickets.json"
            overrides_file = f"{tmpdir}/resolved_enrollment_overrides.json"

            with patch.object(ticket_system, "TICKETS_FILE", tickets_file), patch.object(
                ticket_system, "ENROLLMENT_RESOLUTIONS_FILE", overrides_file
            ):
                ticket_system._save_tickets(
                    [
                        {
                            "id": 9,
                            "type": "enrollment_incomplete",
                            "student_name": "Karl Student",
                            "student_email": "student@example.com",
                            "course_title": "MikroTik Hybrid",
                            "price": "1500",
                            "payment_method": "xendit",
                            "date_paid": "2026-04-18",
                            "fb_sender_id": "",
                            "extra_info": "",
                            "status": "done",
                            "created_at": "2026-04-18T10:00:00+08:00",
                            "resolved_at": "2026-04-18T10:10:00+08:00",
                        }
                    ]
                )

                active, suppressed = ticket_system.filter_resolved_enrollment_students(
                    [
                        {
                            "email": "student@example.com",
                            "course": "MikroTik Hybrid",
                            "amount": "1500",
                            "date_paid": "2026-04-18",
                        }
                    ]
                )

                self.assertEqual(active, [])
                self.assertEqual(len(suppressed), 1)

    def test_record_followup_attempt_persists_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tickets_file = f"{tmpdir}/tickets.json"
            overrides_file = f"{tmpdir}/resolved_enrollment_overrides.json"

            with patch.object(ticket_system, "TICKETS_FILE", tickets_file), patch.object(
                ticket_system, "ENROLLMENT_RESOLUTIONS_FILE", overrides_file
            ):
                ticket = ticket_system.create_enrollment_ticket(
                    student_name="Student One",
                    student_email="student@example.com",
                    course_title="MikroTik Hybrid",
                    price="PHP 1499",
                    date_paid="Sat, 18 Apr 2026 10:00:00 +0800",
                )

                updated_ticket = ticket_system.record_followup_attempt(
                    ticket_id=ticket["id"],
                    contact_name="Student One",
                    phone_number="639171234567",
                    message_text="Follow up message",
                    provider="semaphore",
                    result_status="Queued",
                    provider_message_id="12345",
                    provider_response={"status": "Queued"},
                )

                self.assertEqual(updated_ticket["id"], ticket["id"])
                self.assertEqual(len(updated_ticket["followup_history"]), 1)
                self.assertEqual(
                    updated_ticket["followup_history"][0]["phone_number"],
                    "639171234567",
                )

    def test_resolve_all_pending_tickets_marks_every_pending_ticket_done(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tickets_file = f"{tmpdir}/tickets.json"
            overrides_file = f"{tmpdir}/resolved_enrollment_overrides.json"

            with patch.object(ticket_system, "TICKETS_FILE", tickets_file), patch.object(
                ticket_system, "ENROLLMENT_RESOLUTIONS_FILE", overrides_file
            ):
                first = ticket_system.create_support_email_ticket(
                    student_name="Juan",
                    student_email="juan@example.com",
                    subject="Support email",
                )
                second = ticket_system.create_enrollment_ticket(
                    student_name="Maria",
                    student_email="maria@example.com",
                    course_title="MikroTik Hybrid",
                    price="PHP 1499",
                    date_paid="2026-04-18T10:00:00+08:00",
                )

                resolved = ticket_system.resolve_all_pending_tickets()
                pending = ticket_system.get_pending_tickets()
                active, suppressed = ticket_system.filter_resolved_enrollment_students(
                    [
                        {
                            "email": "maria@example.com",
                            "course": "MikroTik Hybrid",
                            "amount": "PHP 1499",
                            "date_paid": "2026-04-18T10:00:00+08:00",
                        }
                    ]
                )

                self.assertEqual({ticket["id"] for ticket in resolved}, {first["id"], second["id"]})
                self.assertEqual(pending, [])
                self.assertEqual(active, [])
                self.assertEqual(len(suppressed), 1)


if __name__ == "__main__":
    unittest.main()
