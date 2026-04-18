"""
Support inbox watcher for messages sent to the support address.
"""

import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime

import gmail_imap
from config import DATA_DIR, GMAIL_USER, OWNER_EMAIL, SUPPORT_EMAIL, SYSTEME_SENDER
from storage import file_lock, load_json, save_json

PHT = timezone(timedelta(hours=8))
XENDIT_SENDER = "notifications@xendit.co"
SUPPORT_SEEN_FILE = os.path.join(DATA_DIR, "support_inbox_seen.json")


def _parse_date(value):
    if not value:
        return datetime.now(PHT)
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=PHT)
        return dt.astimezone(PHT)
    except Exception:
        return datetime.now(PHT)


def _message_id(message):
    raw = " | ".join(
        [
            str(message.get("from", "")).strip(),
            str(message.get("subject", "")).strip(),
            str(message.get("date", "")).strip(),
            str(message.get("body", ""))[:200].strip(),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def _clean_preview(text, limit=120):
    preview = re.sub(r"<[^>]+>", " ", str(text or ""))
    preview = re.sub(r"\s+", " ", preview).strip()
    if len(preview) <= limit:
        return preview
    return f"{preview[:limit - 3]}..."


def _ignored_sender(sender_text):
    sender = str(sender_text or "").lower()
    ignored = {
        SYSTEME_SENDER,
        XENDIT_SENDER,
        OWNER_EMAIL,
        GMAIL_USER.lower(),
    }
    return any(email and email in sender for email in ignored)


def _search_queries(days_back=7):
    return [
        f"to:{SUPPORT_EMAIL} newer_than:{days_back}d",
        f'"{SUPPORT_EMAIL}" newer_than:{days_back}d',
    ]


def _parse_sender(from_text):
    name, email = parseaddr(str(from_text or ""))
    name = (name or "").strip()
    email = (email or "").strip().lower()
    if not name:
        name = email or str(from_text or "Unknown").strip()
    return name, email


def get_recent_support_emails(days_back=7, limit=10):
    """Return recent emails addressed to the support mailbox."""
    if not gmail_imap.available():
        return None

    messages_by_id = {}
    for query in _search_queries(days_back=days_back):
        messages = gmail_imap.search(query, limit=max(limit * 3, 30))
        if messages is None:
            return None
        for message in messages:
            if _ignored_sender(message.get("from", "")):
                continue
            msg_id = _message_id(message)
            messages_by_id[msg_id] = {
                "id": msg_id,
                "from": message.get("from", ""),
                "subject": message.get("subject", "(no subject)"),
                "date": message.get("date", ""),
                "preview": _clean_preview(message.get("body", "")),
            }

    recent = sorted(
        messages_by_id.values(),
        key=lambda item: _parse_date(item.get("date", "")),
        reverse=True,
    )
    return recent[:limit]


def sync_support_email_tickets(emails):
    """Ensure support emails are represented as actionable tickets."""
    from ticket_system import (
        create_support_email_ticket,
        find_matching_ticket,
    )

    synced = []
    created = []
    for email in emails:
        name, sender_email = _parse_sender(email.get("from", ""))
        ticket_key_email = sender_email or str(email.get("from", "")).strip().lower() or "unknown-support-email"
        subject = str(email.get("subject", "(no subject)")).strip() or "(no subject)"
        preview = str(email.get("preview", "")).strip()
        message_date = str(email.get("date", "")).strip()

        ticket = create_support_email_ticket(
            student_name=name,
            student_email=ticket_key_email,
            subject=subject,
            preview=preview,
            email_date=message_date,
        )
        if ticket:
            created.append(ticket)
        else:
            ticket = find_matching_ticket(
                "support_email",
                ticket_key_email,
                subject,
                status="pending",
            )

        updated_email = dict(email)
        if ticket:
            updated_email["ticket_id"] = ticket["id"]
        synced.append(updated_email)

    return synced, created


def _load_seen_state():
    return load_json(SUPPORT_SEEN_FILE, {"initialized": False, "seen_ids": []})


def _save_seen_state(state):
    save_json(SUPPORT_SEEN_FILE, state)


def get_new_support_emails(days_back=7, limit=20):
    """Return support emails not seen in a previous watcher run."""
    emails = get_recent_support_emails(days_back=days_back, limit=limit)
    if emails is None:
        return None

    with file_lock(SUPPORT_SEEN_FILE):
        state = _load_seen_state()
        seen_ids = set(state.get("seen_ids", []))

        if not state.get("initialized"):
            state["initialized"] = True
            state["seen_ids"] = [email["id"] for email in emails][:500]
            _save_seen_state(state)
            return []

        new_emails = [email for email in emails if email["id"] not in seen_ids]
        combined_ids = [email["id"] for email in emails] + list(seen_ids)
        state["seen_ids"] = combined_ids[:500]
        _save_seen_state(state)

    return new_emails


def format_support_emails_telegram(emails, title=None):
    """Format recent support emails for Telegram."""
    if title is None:
        title = f"📬 *Recent Support Emails ({SUPPORT_EMAIL})*"

    if not emails:
        return (
            f"{title}\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Wala pang recent support emails na nakita. ✅"
        )

    msg = f"{title}\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    for i, email in enumerate(emails, 1):
        timestamp = _parse_date(email.get("date", "")).strftime("%Y-%m-%d %H:%M")
        msg += f"*Email #{i}*\n"
        if email.get("ticket_id"):
            msg += f"🎫 Ticket: #{email['ticket_id']}\n"
        msg += f"👤 From: {email.get('from', 'Unknown')[:80]}\n"
        msg += f"📝 Subject: {email.get('subject', '(no subject)')[:80]}\n"
        msg += f"🕐 {timestamp} PHT\n"
        if email.get("preview"):
            msg += f"💬 {email['preview']}\n"
        msg += "\n"

    msg += "Use `/done <ticket-id>` after you manually resolve the case."
    return msg
