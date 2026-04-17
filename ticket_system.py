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


def _load_tickets():
    return load_json(TICKETS_FILE, [])


def _save_tickets(tickets):
    save_json(TICKETS_FILE, tickets)


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
                return t, "resolved"
        return None, "not_found"


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
    }
    type_labels = {
        "dm_verified": "DM - Payment Verified",
        "dm_no_payment": "DM - No Payment Record",
        "enrollment_incomplete": "Paid but Not Enrolled",
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
            msg += f"   📚 {t['course_title']}\n"
        if t["price"]:
            msg += f"   💰 {t['price']}\n"
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
        }.get(t["type"], t["type"])
        
        md += f"| {t['id']} | {type_label} | {t['student_name']} | {t['student_email']} | {t.get('course_title', 'N/A')} | {t.get('price', 'N/A')} | {t['created_at'][:10]} |\n"
    
    return md
