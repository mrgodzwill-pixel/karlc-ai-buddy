"""
Ticket System for Karl C AI Buddy
Tracks student issues (DMs, enrollment problems) with pending/done states.
"""

import os
from datetime import datetime, timedelta, timezone

from config import DATA_DIR
from storage import file_lock, load_json, save_json

PHT = timezone(timedelta(hours=8))
TICKETS_FILE = os.path.join(DATA_DIR, "tickets.json")
ENROLLMENT_RESOLUTIONS_FILE = os.path.join(DATA_DIR, "resolved_enrollment_overrides.json")


def _load_tickets():
    return load_json(TICKETS_FILE, [])


def _save_tickets(tickets):
    save_json(TICKETS_FILE, tickets)


def _load_enrollment_resolutions():
    return load_json(ENROLLMENT_RESOLUTIONS_FILE, [])


def _save_enrollment_resolutions(resolutions):
    save_json(ENROLLMENT_RESOLUTIONS_FILE, resolutions)


def _normalise_enrollment_key(student_email="", course_title="", price="", date_paid=""):
    return (
        str(student_email or "").strip().lower(),
        str(course_title or "").strip().lower(),
        str(price or "").strip().lower(),
        str(date_paid or "").strip().lower(),
    )


def _ticket_to_enrollment_key(ticket):
    return _normalise_enrollment_key(
        ticket.get("student_email", ""),
        ticket.get("course_title", ""),
        ticket.get("price", ""),
        ticket.get("date_paid", ""),
    )


def _student_to_enrollment_key(student):
    return _normalise_enrollment_key(
        student.get("email", ""),
        student.get("course", student.get("course_title", "")),
        student.get("amount", student.get("price", "")),
        student.get("date_paid", student.get("date", "")),
    )


def _mask_phone_number(phone_number):
    phone = str(phone_number or "").strip()
    if len(phone) <= 4:
        return phone
    return f"{'•' * (len(phone) - 4)}{phone[-4:]}"


def add_enrollment_resolution(ticket):
    """Suppress future unmatched alerts for this exact enrollment record."""
    if not ticket or ticket.get("type") != "enrollment_incomplete":
        return None

    resolution = {
        "student_email": ticket.get("student_email", ""),
        "course_title": ticket.get("course_title", ""),
        "price": ticket.get("price", ""),
        "date_paid": ticket.get("date_paid", ""),
        "resolved_at": datetime.now(PHT).isoformat(),
    }
    resolution_key = _ticket_to_enrollment_key(ticket)

    with file_lock(ENROLLMENT_RESOLUTIONS_FILE):
        resolutions = _load_enrollment_resolutions()
        existing_keys = {
            _normalise_enrollment_key(
                item.get("student_email", ""),
                item.get("course_title", ""),
                item.get("price", ""),
                item.get("date_paid", ""),
            )
            for item in resolutions
        }
        if resolution_key in existing_keys:
            return None
        resolutions.append(resolution)
        _save_enrollment_resolutions(resolutions)

    return resolution


def filter_resolved_enrollment_students(students):
    """Remove enrollment rows that were manually resolved earlier."""
    with file_lock(TICKETS_FILE):
        tickets = _load_tickets()
    with file_lock(ENROLLMENT_RESOLUTIONS_FILE):
        resolutions = _load_enrollment_resolutions()

    resolution_keys = {
        _normalise_enrollment_key(
            item.get("student_email", ""),
            item.get("course_title", ""),
            item.get("price", ""),
            item.get("date_paid", ""),
        )
        for item in resolutions
    }
    resolution_keys.update(
        _ticket_to_enrollment_key(ticket)
        for ticket in tickets
        if ticket.get("type") == "enrollment_incomplete" and ticket.get("status") == "done"
    )

    active = []
    suppressed = []
    for student in students:
        if _student_to_enrollment_key(student) in resolution_keys:
            suppressed.append(student)
        else:
            active.append(student)

    return active, suppressed


def create_ticket(ticket_type, student_name, student_email, course_title="", price="",
                  payment_method="", date_paid="", fb_sender_id="", extra_info=""):
    """Create a new ticket, skipping if a matching pending ticket already exists."""
    with file_lock(TICKETS_FILE):
        tickets = _load_tickets()

        for t in tickets:
            if (t["student_email"] == student_email and
                t["type"] == ticket_type and
                t["course_title"] == course_title and
                t["status"] == "pending"):
                return None  # Duplicate

        # Derive next ID from max existing ID (safer than len(tickets)+1)
        next_id = max((t["id"] for t in tickets), default=0) + 1

        ticket = {
            "id": next_id,
            "type": ticket_type,
            "student_name": student_name,
            "student_email": student_email,
            "course_title": course_title,
            "price": price,
            "payment_method": payment_method,
            "date_paid": date_paid,
            "fb_sender_id": fb_sender_id,
            "extra_info": extra_info,
            "status": "pending",
            "created_at": datetime.now(PHT).isoformat(),
            "resolved_at": None,
            "followup_history": [],
        }
        tickets.append(ticket)
        _save_tickets(tickets)
        return ticket


def create_dm_ticket(student_name, student_email, course_title, price, 
                     payment_method="", fb_sender_id=""):
    """Create a DM-based ticket (payment verified via Xendit)."""
    return create_ticket(
        ticket_type="dm_verified",
        student_name=student_name,
        student_email=student_email,
        course_title=course_title,
        price=price,
        payment_method=payment_method,
        fb_sender_id=fb_sender_id,
    )


def create_no_payment_ticket(student_name, student_email, fb_sender_id=""):
    """Create a ticket for student who claims payment but no record found."""
    return create_ticket(
        ticket_type="dm_no_payment",
        student_name=student_name,
        student_email=student_email,
        fb_sender_id=fb_sender_id,
    )


def create_enrollment_ticket(student_name, student_email, course_title, price,
                              payment_method="", date_paid=""):
    """Create a ticket for student who paid but didn't complete enrollment."""
    return create_ticket(
        ticket_type="enrollment_incomplete",
        student_name=student_name,
        student_email=student_email,
        course_title=course_title,
        price=price,
        payment_method=payment_method,
        date_paid=date_paid,
    )


def create_support_email_ticket(student_name, student_email, subject, preview="", email_date=""):
    """Create a ticket for a support inbox email that needs manual handling."""
    return create_ticket(
        ticket_type="support_email",
        student_name=student_name,
        student_email=student_email,
        course_title=subject,
        payment_method="support_email",
        date_paid=email_date,
        extra_info=preview,
    )


def resolve_ticket(ticket_id):
    """Mark a ticket as resolved."""
    with file_lock(TICKETS_FILE):
        tickets = _load_tickets()
        for t in tickets:
            if t["id"] == ticket_id:
                if t["status"] == "done":
                    return t, "already_done"
                t["status"] = "done"
                t["resolved_at"] = datetime.now(PHT).isoformat()
                _save_tickets(tickets)
                add_enrollment_resolution(t)
                return t, "resolved"
        return None, "not_found"


def resolve_all_pending_tickets(ticket_type=None):
    """Resolve every pending ticket, optionally filtered by type."""
    resolved = []

    with file_lock(TICKETS_FILE):
        tickets = _load_tickets()
        for ticket in tickets:
            if ticket.get("status") != "pending":
                continue
            if ticket_type and ticket.get("type") != ticket_type:
                continue

            ticket["status"] = "done"
            ticket["resolved_at"] = datetime.now(PHT).isoformat()
            resolved.append(dict(ticket))

        if resolved:
            _save_tickets(tickets)

    for ticket in resolved:
        add_enrollment_resolution(ticket)

    return resolved


def get_ticket(ticket_id):
    """Return a ticket by ID."""
    tickets = _load_tickets()
    for ticket in tickets:
        if ticket["id"] == ticket_id:
            return ticket
    return None


def find_matching_ticket(ticket_type, student_email, course_title="", status=None):
    """Find a ticket by the same matching fields used for duplicate detection."""
    tickets = _load_tickets()
    for ticket in tickets:
        if ticket.get("type") != ticket_type:
            continue
        if ticket.get("student_email") != student_email:
            continue
        if ticket.get("course_title") != course_title:
            continue
        if status and ticket.get("status") != status:
            continue
        return ticket
    return None


def record_followup_attempt(ticket_id, contact_name, phone_number, message_text,
                            provider, result_status, provider_message_id="",
                            provider_response=None):
    """Append follow-up metadata to a ticket."""
    with file_lock(TICKETS_FILE):
        tickets = _load_tickets()
        for ticket in tickets:
            if ticket["id"] != ticket_id:
                continue

            history = ticket.setdefault("followup_history", [])
            history.append(
                {
                    "contact_name": contact_name,
                    "phone_number": phone_number,
                    "message_text": message_text,
                    "provider": provider,
                    "status": result_status,
                    "provider_message_id": provider_message_id,
                    "provider_response": provider_response or {},
                    "sent_at": datetime.now(PHT).isoformat(),
                }
            )
            _save_tickets(tickets)
            return ticket

    return None


def get_pending_tickets(ticket_type=None):
    """Get all pending tickets, optionally filtered by type."""
    tickets = _load_tickets()
    pending = [t for t in tickets if t["status"] == "pending"]
    if ticket_type:
        pending = [t for t in pending if t["type"] == ticket_type]
    return pending


def get_ticket_stats():
    """Get ticket statistics."""
    tickets = _load_tickets()
    return {
        "total": len(tickets),
        "pending": len([t for t in tickets if t["status"] == "pending"]),
        "done": len([t for t in tickets if t["status"] == "done"]),
        "dm_verified": len([t for t in tickets if t["type"] == "dm_verified" and t["status"] == "pending"]),
        "dm_no_payment": len([t for t in tickets if t["type"] == "dm_no_payment" and t["status"] == "pending"]),
        "enrollment_incomplete": len([t for t in tickets if t["type"] == "enrollment_incomplete" and t["status"] == "pending"]),
        "support_email": len([t for t in tickets if t["type"] == "support_email" and t["status"] == "pending"]),
    }


def format_pending_tickets_telegram():
    """Format pending tickets for Telegram display."""
    pending = get_pending_tickets()
    
    if not pending:
        return "🎫 *No Pending Tickets*\n\nWalang pending student issues. All clear! ✅"
    
    type_icons = {
        "dm_verified": "🟡",
        "dm_no_payment": "🔴",
        "enrollment_incomplete": "🟠",
        "support_email": "📬",
    }
    type_labels = {
        "dm_verified": "DM - Payment Verified",
        "dm_no_payment": "DM - No Payment Record",
        "enrollment_incomplete": "Paid but Not Enrolled",
        "support_email": "Support Email",
    }
    
    msg = f"🎫 *Pending Tickets ({len(pending)})*\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    
    for t in pending:
        icon = type_icons.get(t["type"], "⚪")
        label = type_labels.get(t["type"], t["type"])
        msg += f"{icon} *Ticket #{t['id']}* - {label}\n"
        msg += f"   👤 {t['student_name']}\n"
        msg += f"   📧 {t['student_email']}\n"
        if t["course_title"]:
            if t["type"] == "support_email":
                msg += f"   📝 {t['course_title']}\n"
            else:
                msg += f"   📚 {t['course_title']}\n"
        if t["price"]:
            msg += f"   💰 {t['price']}\n"
        if t["type"] == "support_email" and t.get("extra_info"):
            msg += f"   💬 {str(t['extra_info'])[:120]}\n"
        followups = t.get("followup_history", [])
        if followups:
            latest = followups[-1]
            masked_phone = _mask_phone_number(latest.get("phone_number", ""))
            msg += f"   📲 Follow-up: {latest.get('status', 'sent')} to {masked_phone or 'N/A'}\n"
            msg += f"   🕐 Last SMS: {latest.get('sent_at', '')[:16]}\n"
        msg += f"   🕐 {t['created_at'][:16]}\n\n"
    
    msg += "━━━━━━━━━━━━━━━━━━\n"
    msg += "✅ /done 1 - Resolve ticket #1\n"
    msg += "✅ /done 1 2 3 - Resolve multiple\n"
    
    return msg


def format_pending_tickets_report():
    """Format pending tickets for markdown report."""
    pending = get_pending_tickets()
    
    if not pending:
        return "### Pending Student Tickets\nNo pending tickets. ✅\n"
    
    md = f"### Pending Student Tickets ({len(pending)})\n\n"
    md += "| # | Type | Student | Email | Course | Price | Created |\n"
    md += "|---|------|---------|-------|--------|-------|--------|\n"
    
    for t in pending:
        type_label = {
            "dm_verified": "DM ✅",
            "dm_no_payment": "DM ❌",
            "enrollment_incomplete": "Enrollment ⚠️",
            "support_email": "Support 📬",
        }.get(t["type"], t["type"])

        followups = t.get("followup_history", [])
        student_label = t["student_name"]
        if followups:
            latest = followups[-1]
            student_label += f" (SMS {latest.get('status', 'sent')} {latest.get('sent_at', '')[:10]})"

        md += f"| {t['id']} | {type_label} | {student_label} | {t['student_email']} | {t.get('course_title', 'N/A')} | {t.get('price', 'N/A')} | {t['created_at'][:10]} |\n"
    
    return md
