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


if __name__ == "__main__":
    unittest.main()
