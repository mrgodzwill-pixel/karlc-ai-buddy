import tempfile
import unittest
from datetime import datetime
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

    def test_resolved_enrollment_suppression_normalizes_price_and_date_formats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tickets_file = f"{tmpdir}/tickets.json"
            overrides_file = f"{tmpdir}/resolved_enrollment_overrides.json"

            with patch.object(ticket_system, "TICKETS_FILE", tickets_file), patch.object(
                ticket_system, "ENROLLMENT_RESOLUTIONS_FILE", overrides_file
            ):
                ticket = ticket_system.create_enrollment_ticket(
                    student_name="Ricky Andeo",
                    student_email="rickyandeo90@gmail.com",
                    course_title="10G Core Part 3: Centralized Pisowifi Setup",
                    price="PHP 1,500",
                    date_paid="2026-04-23T15:55:12+08:00",
                )
                ticket_system.resolve_ticket(ticket["id"])

                active, suppressed = ticket_system.filter_resolved_enrollment_students(
                    [
                        {
                            "email": "rickyandeo90@gmail.com",
                            "course": "10G Core Part 3: Centralized Pisowifi Setup",
                            "amount": "1500",
                            "date": "Wed, 23 Apr 2026 15:55:55 +0800",
                        }
                    ]
                )

                self.assertEqual(active, [])
                self.assertEqual(len(suppressed), 1)

    def test_resolved_enrollment_suppression_normalizes_course_aliases(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tickets_file = f"{tmpdir}/tickets.json"
            overrides_file = f"{tmpdir}/resolved_enrollment_overrides.json"

            with patch.object(ticket_system, "TICKETS_FILE", tickets_file), patch.object(
                ticket_system, "ENROLLMENT_RESOLUTIONS_FILE", overrides_file
            ):
                ticket = ticket_system.create_enrollment_ticket(
                    student_name="Student One",
                    student_email="student@example.com",
                    course_title="MikroTik Basic (QuickStart)",
                    price="PHP 799",
                    date_paid="2026-04-18",
                )
                ticket_system.resolve_ticket(ticket["id"])

                active, suppressed = ticket_system.filter_resolved_enrollment_students(
                    [
                        {
                            "email": "student@example.com",
                            "course": "MikroTik QuickStart: Configure From Scratch",
                            "amount": "799",
                            "date_paid": "2026-04-18T10:00:00+08:00",
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
                self.assertEqual(updated_ticket["phone_number"], "639171234567")

    def test_duplicate_pending_ticket_gets_phone_enrichment(self):
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
                duplicate = ticket_system.create_support_email_ticket(
                    student_name="Juan Dela Cruz",
                    student_email="juan@example.com",
                    subject="Support email",
                    phone_number="639171234567",
                )
                stored = ticket_system.get_ticket(first["id"])

                self.assertIsNone(duplicate)
                self.assertEqual(stored["phone_number"], "639171234567")

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

    def test_create_enrollment_ticket_normalises_email_and_course_for_duplicate_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tickets_file = f"{tmpdir}/tickets.json"
            overrides_file = f"{tmpdir}/resolved_enrollment_overrides.json"

            with patch.object(ticket_system, "TICKETS_FILE", tickets_file), patch.object(
                ticket_system, "ENROLLMENT_RESOLUTIONS_FILE", overrides_file
            ):
                first = ticket_system.create_enrollment_ticket(
                    student_name="Ricky Andeo",
                    student_email="rickyandeo90@gmail.com",
                    course_title="10G Core Part 3: Centralized Pisowifi Setup",
                    price="PHP 1500",
                    phone_number="639777235690",
                )
                duplicate = ticket_system.create_enrollment_ticket(
                    student_name="Ricky",
                    student_email=" RickyAndeo90@gmail.com ",
                    course_title=" 10G Core Part 3: Centralized Pisowifi Setup ",
                    price="PHP 1500",
                )
                pending = ticket_system.get_pending_tickets("enrollment_incomplete")

                self.assertIsNotNone(first)
                self.assertIsNone(duplicate)
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0]["phone_number"], "639777235690")

    def test_resolve_matching_enrollment_tickets_marks_pending_case_done(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tickets_file = f"{tmpdir}/tickets.json"
            overrides_file = f"{tmpdir}/resolved_enrollment_overrides.json"

            with patch.object(ticket_system, "TICKETS_FILE", tickets_file), patch.object(
                ticket_system, "ENROLLMENT_RESOLUTIONS_FILE", overrides_file
            ):
                ticket = ticket_system.create_enrollment_ticket(
                    student_name="Alexis Honculada",
                    student_email="jaahonculada77@gmail.com",
                    course_title="10G Core Part 2: OSPF & Advanced Routing",
                    price="PHP 977",
                    date_paid="2026-04-23T16:58:00+08:00",
                )

                resolved = ticket_system.resolve_matching_enrollment_tickets(
                    [
                        {
                            "email": " Jaahonculada77@gmail.com ",
                            "course": " 10G Core Part 2: OSPF & Advanced Routing ",
                            "amount": "PHP 977",
                        }
                    ]
                )

                refreshed = ticket_system.get_ticket(ticket["id"])
                self.assertEqual(len(resolved), 1)
                self.assertEqual(refreshed["status"], "done")
                self.assertIsNotNone(refreshed["resolved_at"])

    def test_prune_resolved_tickets_removes_only_old_done_tickets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tickets_file = f"{tmpdir}/tickets.json"
            overrides_file = f"{tmpdir}/resolved_enrollment_overrides.json"

            old_done = {
                "id": 1,
                "type": "support_email",
                "student_name": "Old Done",
                "student_email": "old@example.com",
                "course_title": "Need help",
                "price": "",
                "payment_method": "support_email",
                "date_paid": "",
                "fb_sender_id": "",
                "extra_info": "Old detail",
                "phone_number": "",
                "status": "done",
                "created_at": "2026-04-01T10:00:00+08:00",
                "resolved_at": "2026-04-10T10:00:00+08:00",
                "followup_history": [],
            }
            recent_done = {
                "id": 2,
                "type": "support_email",
                "student_name": "Recent Done",
                "student_email": "recent@example.com",
                "course_title": "Need help",
                "price": "",
                "payment_method": "support_email",
                "date_paid": "",
                "fb_sender_id": "",
                "extra_info": "Recent detail",
                "phone_number": "",
                "status": "done",
                "created_at": "2026-04-20T10:00:00+08:00",
                "resolved_at": "2026-04-22T10:00:00+08:00",
                "followup_history": [],
            }
            pending_ticket = {
                "id": 3,
                "type": "enrollment_incomplete",
                "student_name": "Pending",
                "student_email": "pending@example.com",
                "course_title": "MikroTik Hybrid",
                "price": "PHP 1499",
                "payment_method": "xendit",
                "date_paid": "2026-04-22",
                "fb_sender_id": "",
                "extra_info": "",
                "phone_number": "",
                "status": "pending",
                "created_at": "2026-04-22T11:00:00+08:00",
                "resolved_at": None,
                "followup_history": [],
            }

            class FixedDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    base = datetime(2026, 4, 23, 12, 0, 0, tzinfo=ticket_system.PHT)
                    return base if tz is None else base.astimezone(tz)

            with patch.object(ticket_system, "TICKETS_FILE", tickets_file), patch.object(
                ticket_system, "ENROLLMENT_RESOLUTIONS_FILE", overrides_file
            ), patch.object(ticket_system, "datetime", FixedDateTime):
                ticket_system._save_tickets([old_done, recent_done, pending_ticket])

                removed = ticket_system.prune_resolved_tickets(retention_days=7)
                remaining = ticket_system._load_tickets()

            self.assertEqual([ticket["id"] for ticket in removed], [1])
            self.assertEqual({ticket["id"] for ticket in remaining}, {2, 3})
            self.assertEqual(
                {ticket["status"] for ticket in remaining if ticket["id"] == 3},
                {"pending"},
            )


if __name__ == "__main__":
    unittest.main()
