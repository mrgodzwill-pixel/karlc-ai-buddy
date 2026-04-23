"""
Ticket System for Karl C AI Buddy
Tracks student issues (DMs, enrollment problems) with pending/done states.
"""

import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from config import DATA_DIR, TICKET_RESOLVED_RETENTION_DAYS
from course_mapping import canonical_course_name
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


def _ticket_timestamp(ticket, *fields):
    for field in fields:
        raw = str(ticket.get(field) or "").strip()
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=PHT)
        return parsed.astimezone(PHT)
    return None


def _normalise_course_title(value=""):
    canonical = canonical_course_name(value, allow_old_fallback=True)
    return str(canonical or value or "").strip().lower()


def _normalise_price_value(price=""):
    raw = str(price or "").strip().lower()
    if not raw:
        return ""
    cleaned = raw.replace("php", "").replace("₱", "").replace(",", "").strip()
    try:
        amount = float(cleaned)
    except ValueError:
        return raw
    if amount.is_integer():
        return f"{int(amount)}"
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def _normalise_date_value(value=""):
    raw = str(value or "").strip()
    if not raw or raw.lower() == "unknown":
        return ""

    parsed = _ticket_timestamp({"value": raw}, "value")
    if parsed is None:
        try:
            parsed = parsedate_to_datetime(raw)
        except Exception:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=PHT)
            parsed = parsed.astimezone(PHT)
    if parsed is not None:
        return parsed.date().isoformat()

    lowered = raw.lower()
    return lowered[:10] if len(lowered) >= 10 else lowered


def _normalise_enrollment_key(student_email="", course_title="", price="", date_paid=""):
    return (
        str(student_email or "").strip().lower(),
        _normalise_course_title(course_title),
        _normalise_price_value(price),
        _normalise_date_value(date_paid),
    )


def _normalise_pending_ticket_key(ticket_type="", student_email="", course_title=""):
    return (
        str(ticket_type or "").strip().lower(),
        str(student_email or "").strip().lower(),
        _normalise_course_title(course_title),
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


def _student_to_pending_ticket_key(student, ticket_type="enrollment_incomplete"):
    return _normalise_pending_ticket_key(
        ticket_type,
        student.get("email", student.get("student_email", "")),
        student.get("course", student.get("course_title", "")),
    )


def _ticket_to_pending_key(ticket):
    return _normalise_pending_ticket_key(
        ticket.get("type", ""),
        ticket.get("student_email", ""),
        ticket.get("course_title", ""),
    )


def _mask_phone_number(phone_number):
    phone = str(phone_number or "").strip()
    if len(phone) <= 4:
        return phone
    return f"{'•' * (len(phone) - 4)}{phone[-4:]}"


def _is_missing_value(value):
    normalized = str(value or "").strip().lower()
    return normalized in {"", "unknown", "n/a", "na", "unknown-support-email"}


def _merge_ticket_details(ticket, student_name="", student_email="", course_title="", price="",
                          payment_method="", date_paid="", fb_sender_id="", extra_info="",
                          phone_number=""):
    changed = False

    current_name = str(ticket.get("student_name") or "").strip()
    current_email = str(ticket.get("student_email") or "").strip()
    incoming_name = str(student_name or "").strip()
    if incoming_name and (
        _is_missing_value(current_name)
        or current_name.lower() == current_email.lower()
    ) and current_name != incoming_name:
        ticket["student_name"] = incoming_name
        changed = True

    incoming_email = str(student_email or "").strip()
    if incoming_email and _is_missing_value(ticket.get("student_email")):
        ticket["student_email"] = incoming_email
        changed = True

    incoming_course = str(course_title or "").strip()
    if incoming_course and _is_missing_value(ticket.get("course_title")):
        ticket["course_title"] = incoming_course
        changed = True

    incoming_price = str(price or "").strip()
    if incoming_price and _is_missing_value(ticket.get("price")):
        ticket["price"] = incoming_price
        changed = True

    incoming_payment_method = str(payment_method or "").strip()
    if incoming_payment_method and _is_missing_value(ticket.get("payment_method")):
        ticket["payment_method"] = incoming_payment_method
        changed = True

    incoming_date_paid = str(date_paid or "").strip()
    if incoming_date_paid and _is_missing_value(ticket.get("date_paid")):
        ticket["date_paid"] = incoming_date_paid
        changed = True

    incoming_fb_sender_id = str(fb_sender_id or "").strip()
    if incoming_fb_sender_id and _is_missing_value(ticket.get("fb_sender_id")):
        ticket["fb_sender_id"] = incoming_fb_sender_id
        changed = True

    incoming_extra = str(extra_info or "").strip()
    current_extra = str(ticket.get("extra_info") or "").strip()
    if incoming_extra and (not current_extra or len(current_extra) < len(incoming_extra)):
        ticket["extra_info"] = incoming_extra
        changed = True

    incoming_phone = str(phone_number or "").strip()
    if incoming_phone and _is_missing_value(ticket.get("phone_number")):
        ticket["phone_number"] = incoming_phone
        changed = True

    return changed


def dedupe_enrollment_ticket_candidates(students):
    """Collapse repeated enrollment rows into the same manual-enrollment case.

    The enrollment report can contain repeated unmatched payment rows for the
    same student/course, while tickets intentionally dedupe to one pending case.
    This helper keeps the first row order stable and merges in any missing
    details from later duplicates so report counts line up with ticket counts.
    """
    deduped = []
    indexes = {}

    def _pick(existing, incoming, *fields):
        for field in fields:
            incoming_value = str(incoming.get(field) or "").strip()
            if incoming_value and _is_missing_value(existing.get(field)):
                existing[field] = incoming_value

    for student in students or []:
        student_copy = dict(student)
        key = _student_to_pending_ticket_key(student_copy)
        if key in indexes:
            existing = deduped[indexes[key]]
            _pick(existing, student_copy, "payer_name", "name", "email", "course", "course_raw")
            _pick(existing, student_copy, "amount", "price", "payment_method", "phone")
            _pick(existing, student_copy, "date_paid", "date")
            continue

        indexes[key] = len(deduped)
        deduped.append(student_copy)

    return deduped


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
                  payment_method="", date_paid="", fb_sender_id="", extra_info="",
                  phone_number=""):
    """Create a new ticket, skipping if a matching pending ticket already exists."""
    with file_lock(TICKETS_FILE):
        tickets = _load_tickets()
        pending_key = _normalise_pending_ticket_key(ticket_type, student_email, course_title)

        for t in tickets:
            if t.get("status") != "pending":
                continue
            if _ticket_to_pending_key(t) == pending_key:
                if _merge_ticket_details(
                    t,
                    student_name=student_name,
                    student_email=student_email,
                    course_title=course_title,
                    price=price,
                    payment_method=payment_method,
                    date_paid=date_paid,
                    fb_sender_id=fb_sender_id,
                    extra_info=extra_info,
                    phone_number=phone_number,
                ):
                    _save_tickets(tickets)
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
            "phone_number": phone_number,
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
                              payment_method="", date_paid="", phone_number=""):
    """Create a ticket for student who paid but didn't complete enrollment."""
    return create_ticket(
        ticket_type="enrollment_incomplete",
        student_name=student_name,
        student_email=student_email,
        course_title=course_title,
        price=price,
        payment_method=payment_method,
        date_paid=date_paid,
        phone_number=phone_number,
    )


def create_support_email_ticket(student_name, student_email, subject, preview="", email_date="",
                                phone_number=""):
    """Create a ticket for a support inbox email that needs manual handling."""
    return create_ticket(
        ticket_type="support_email",
        student_name=student_name,
        student_email=student_email,
        course_title=subject,
        payment_method="support_email",
        date_paid=email_date,
        extra_info=preview,
        phone_number=phone_number,
    )


def update_ticket_contact_details(ticket_id, student_name="", student_email="", course_title="",
                                  price="", payment_method="", date_paid="", fb_sender_id="",
                                  extra_info="", phone_number=""):
    """Update stored ticket contact details without changing its status."""
    with file_lock(TICKETS_FILE):
        tickets = _load_tickets()
        for ticket in tickets:
            if ticket.get("id") != ticket_id:
                continue
            if _merge_ticket_details(
                ticket,
                student_name=student_name,
                student_email=student_email,
                course_title=course_title,
                price=price,
                payment_method=payment_method,
                date_paid=date_paid,
                fb_sender_id=fb_sender_id,
                extra_info=extra_info,
                phone_number=phone_number,
            ):
                _save_tickets(tickets)
            return ticket
    return None


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


def resolve_matching_enrollment_tickets(students):
    """Auto-resolve pending enrollment tickets that are now matched.

    This keeps `/tickets` aligned with the latest `/enrollment` result when a
    student was previously unmatched but later became properly enrolled under
    the same email + course.
    """
    match_keys = {
        _student_to_pending_ticket_key(student)
        for student in students or []
        if _student_to_pending_ticket_key(student)
    }
    if not match_keys:
        return []

    resolved = []
    with file_lock(TICKETS_FILE):
        tickets = _load_tickets()
        for ticket in tickets:
            if ticket.get("type") != "enrollment_incomplete":
                continue
            if ticket.get("status") != "pending":
                continue
            if _ticket_to_pending_key(ticket) not in match_keys:
                continue

            ticket["status"] = "done"
            ticket["resolved_at"] = datetime.now(PHT).isoformat()
            resolved.append(dict(ticket))

        if resolved:
            _save_tickets(tickets)

    for ticket in resolved:
        add_enrollment_resolution(ticket)

    return resolved


def prune_resolved_tickets(retention_days=None):
    """Delete resolved tickets after the configured retention window.

    Tickets are stored with full details in `tickets.json` when created. Once
    they are resolved, we keep them around for a short retention window so they
    remain visible/auditable, then prune them later. Enrollment suppression is
    preserved separately via `resolved_enrollment_overrides.json`.
    """
    if retention_days is None:
        retention_days = TICKET_RESOLVED_RETENTION_DAYS

    try:
        retention_days = max(0, int(retention_days))
    except (TypeError, ValueError):
        retention_days = TICKET_RESOLVED_RETENTION_DAYS

    cutoff = datetime.now(PHT) - timedelta(days=retention_days)
    removed = []
    kept = []

    with file_lock(TICKETS_FILE):
        tickets = _load_tickets()
        for ticket in tickets:
            if ticket.get("status") != "done":
                kept.append(ticket)
                continue

            resolved_at = _ticket_timestamp(ticket, "resolved_at", "created_at")
            if resolved_at is None or resolved_at > cutoff:
                kept.append(ticket)
                continue

            removed.append(ticket)

        if removed:
            _save_tickets(kept)

    return removed


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
    pending_key = _normalise_pending_ticket_key(ticket_type, student_email, course_title)
    for ticket in tickets:
        if _ticket_to_pending_key(ticket) != pending_key:
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
            _merge_ticket_details(
                ticket,
                student_name=contact_name,
                phone_number=phone_number,
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
        if t.get("phone_number"):
            msg += f"   📱 {t['phone_number']}\n"
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
    if any(t["type"] == "enrollment_incomplete" for t in pending):
        msg += "📇 /systeme_add 12 - Create Systeme contact from enrollment ticket\n"
        msg += "🎓 /systeme_enroll 12 - Add/enroll that ticket directly in Systeme\n"
    
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
        if t.get("phone_number"):
            student_label += f" ({t['phone_number']})"
        if followups:
            latest = followups[-1]
            student_label += f" (SMS {latest.get('status', 'sent')} {latest.get('sent_at', '')[:10]})"

        md += f"| {t['id']} | {type_label} | {student_label} | {t['student_email']} | {t.get('course_title', 'N/A')} | {t.get('price', 'N/A')} | {t['created_at'][:10]} |\n"
    
    return md
