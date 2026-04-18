"""
Enrollment Checker - Compares Xendit payments vs Systeme.io enrollments.
Identifies students who paid but haven't completed enrollment.

Gmail access is via IMAP + a Gmail App Password (see gmail_imap.py).
On Railway/Render set GMAIL_USER and GMAIL_APP_PASSWORD to enable.
"""

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape

import gmail_imap
from config import DATA_DIR, OWNER_EMAIL, SYSTEME_SENDER
from storage import save_json
from xendit_payments import (
    extract_amount as _extract_amount,
    extract_course_from_subject as _extract_course_from_subject,
    extract_payer_email as _extract_payer_email,
    extract_payment_record,
    subject_looks_paid as _xendit_subject_looks_paid,
    sync_payment_records,
)

PHT = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)
XENDIT_SENDER = "notifications@xendit.co"
_SYSTEM_EMAIL_DOMAINS = {
    "xendit.co",
    "xendit.com",
    "xendit.id",
    "xendit.ph",
    "systeme.io",
    "karlcomboy.com",
}
_XENDIT_SEARCH_LIMIT = 200
_XENDIT_QUERY_TEMPLATES = (
    f"from:{XENDIT_SENDER} newer_than:{{days_back}}d",
    f'from:{XENDIT_SENDER} subject:"INVOICE PAID" newer_than:{{days_back}}d',
    f'from:{XENDIT_SENDER} subject:"Successful Payment" newer_than:{{days_back}}d',
    f'from:{XENDIT_SENDER} subject:"Payment received" newer_than:{{days_back}}d',
    f'from:{XENDIT_SENDER} subject:"Payment completed" newer_than:{{days_back}}d',
    f'from:{XENDIT_SENDER} subject:"Pembayaran Berhasil" newer_than:{{days_back}}d',
)


def _is_system_email(email_addr):
    if not email_addr:
        return True

    email_addr = email_addr.lower()
    excluded = {
        SYSTEME_SENDER,
        OWNER_EMAIL,
        os.environ.get("GMAIL_USER", "").lower(),
    }
    if email_addr in excluded:
        return True

    domain = email_addr.rsplit("@", 1)[-1]
    return domain in _SYSTEM_EMAIL_DOMAINS or domain.startswith("xendit.")


def _normalise_email_body(text):
    """Normalise plain text / HTML email bodies for label-based extraction."""
    text = unescape(text or "")
    text = re.sub(r'(?i)<br\s*/?>', "\n", text)
    text = re.sub(r'(?i)</(p|div|tr|td|th|li|table)>', " ", text)
    text = re.sub(r'<[^>]+>', " ", text)
    text = re.sub(r'\s+', " ", text)
    return text.strip()


def _extract_enrolment_email(text):
    """Extract the Systeme student email from the explicit `Email` field only."""
    text = _normalise_email_body(text)
    match = re.search(
        r'Email[:\s]*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
        text or "",
        re.IGNORECASE,
    )
    if not match:
        return None

    email_addr = match.group(1).lower()
    if _is_system_email(email_addr):
        return None

    return email_addr


def _unavailable_report():
    return {
        "total_payments": 0,
        "total_enrolments": 0,
        "matched": 0,
        "unmatched": 0,
        "matched_students": [],
        "unmatched_students": [],
        "payments": [],
        "enrolments": [],
        "unavailable": True,
        "checked_at": datetime.now(PHT).isoformat(),
    }


def _search_xendit_messages(days_back=7):
    """Search for Xendit payment confirmations using multiple Gmail queries."""
    combined = {}

    for template in _XENDIT_QUERY_TEMPLATES:
        query = template.format(days_back=days_back)
        messages = gmail_imap.search(query, limit=_XENDIT_SEARCH_LIMIT)
        if messages is None:
            return None

        print(f"[Enrollment] Xendit query '{query}' returned {len(messages)} messages")
        for message in messages:
            key = (
                message.get("date", ""),
                message.get("from", ""),
                message.get("subject", ""),
            )
            combined.setdefault(key, message)

    if combined:
        return list(combined.values())

    fallback_query = f"from:{XENDIT_SENDER} newer_than:{days_back}d"
    messages = gmail_imap.search(fallback_query, limit=_XENDIT_SEARCH_LIMIT)
    if messages is None:
        return None

    print(f"[Enrollment] Fallback Xendit query '{fallback_query}' returned {len(messages)} messages")
    for message in messages:
        key = (
            message.get("date", ""),
            message.get("from", ""),
            message.get("subject", ""),
        )
        combined.setdefault(key, message)

    return list(combined.values())


def compare_payments_vs_enrolments(days_back=7):
    """Compare Xendit payments with Systeme.io enrollments."""
    print(f"[Enrollment] Comparing last {days_back} days...")

    if not gmail_imap.available():
        print("[Enrollment] GMAIL_USER/GMAIL_APP_PASSWORD not set - skipping enrollment check")
        return _unavailable_report()

    xendit_msgs = _search_xendit_messages(days_back=days_back)
    if xendit_msgs is None:
        # IMAP configured but connect failed; treat as unavailable.
        return _unavailable_report()

    checked_at = datetime.now(PHT).isoformat()
    print(f"[Enrollment] Xendit combined search returned {len(xendit_msgs)} unique messages")

    _, parsed_xendit_records = sync_payment_records(xendit_msgs, checked_at=checked_at)

    payments = []
    skipped_subjects = []
    missing_email_subjects = []
    for m in xendit_msgs:
        subject = m.get("subject", "")
        if not _xendit_subject_looks_paid(subject):
            if len(skipped_subjects) < 5:
                skipped_subjects.append(subject[:80] or "(no subject)")
            continue
        record = extract_payment_record(m)
        payer_email = (record or {}).get("email")
        if not payer_email:
            # Log once so Karl can see in Railway logs which subjects were
            # matched but failed body extraction (helps tune regex if needed).
            print(f"[Enrollment] Skipped Xendit msg (no payer email): {subject[:80]}")
            if len(missing_email_subjects) < 5:
                missing_email_subjects.append(subject[:80] or "(no subject)")
            continue
        payments.append({
            "payer_name": record.get("payer_name", ""),
            "email": payer_email,
            "phone": record.get("phone", ""),
            "course": record.get("course") or _extract_course_from_subject(subject),
            "amount": record.get("amount") or _extract_amount(m.get("body", "")),
            "payment_method": record.get("payment_method", ""),
            "subject": subject,
            "date": m.get("date", ""),
        })

    print(
        f"[Enrollment] Parsed {len(parsed_xendit_records)} paid Xendit records; "
        f"{len(payments)} include payer emails for enrollment matching"
    )
    if not payments:
        if skipped_subjects:
            print(f"[Enrollment] Sample non-payment Xendit subjects: {skipped_subjects}")
        if missing_email_subjects:
            print(f"[Enrollment] Sample paid-like Xendit subjects missing payer email: {missing_email_subjects}")

    # Search enrollment / verification emails from Systeme.io.
    # Default sender is course@karlcomboy.com (configurable via SYSTEME_SENDER).
    enrolment_msgs = gmail_imap.search(
        f"from:{SYSTEME_SENDER} newer_than:{days_back}d",
        limit=_XENDIT_SEARCH_LIMIT,
    )
    if enrolment_msgs is None:
        enrolment_msgs = []

    enrolments = []
    seen_emails = set()
    for m in enrolment_msgs:
        student_email = _extract_enrolment_email(m.get("body", ""))
        if not student_email or student_email in seen_emails:
            continue
        seen_emails.add(student_email)
        enrolments.append({
            "email": student_email,
            "date": m.get("date", ""),
            "subject": m.get("subject", ""),
        })

    print(f"[Enrollment] Found {len(enrolments)} enrolment confirmations")

    enrolled_emails = {e["email"] for e in enrolments}
    matched, unmatched = [], []
    for p in payments:
        (matched if p["email"] in enrolled_emails else unmatched).append(p)

    report = {
        "total_payments": len(payments),
        "total_enrolments": len(enrolments),
        "matched": len(matched),
        "unmatched": len(unmatched),
        "matched_students": matched,
        "unmatched_students": unmatched,
        "payments": payments,
        "enrolments": enrolments,
        "checked_at": checked_at,
    }

    report_file = os.path.join(DATA_DIR, "enrollment_report.json")
    try:
        save_json(report_file, report)
    except Exception:
        logger.exception("Failed to save enrollment_report.json")

    return report


def format_comparison_telegram(report):
    """Format the comparison report for Telegram."""
    if report.get("unavailable"):
        return (
            "📊 *Enrollment Comparison*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "ℹ️ Gmail auto-check is not available right now.\n"
            "Set `GMAIL_USER` and `GMAIL_APP_PASSWORD` in Railway to enable.\n\n"
            "Please verify enrollments manually for now."
        )

    msg = "📊 *Enrollment Comparison Report*\n"
    msg += f"🕐 {report['checked_at'][:16]} PHT\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"

    msg += f"💰 Xendit Payments: {report['total_payments']}\n"
    msg += f"✅ Systeme.io Enrollments: {report['total_enrolments']}\n"
    msg += f"🟢 Matched: {report['matched']}\n"
    msg += f"🔴 Unmatched: {report['unmatched']}\n\n"
    if report.get("suppressed"):
        msg += f"🟡 Manually Resolved / Suppressed: {report['suppressed']}\n\n"

    if report["unmatched_students"]:
        msg += "⚠️ *UNMATCHED - Paid but NOT Enrolled:*\n\n"
        for i, s in enumerate(report["unmatched_students"], 1):
            msg += f"🔴 *#{i}*\n"
            msg += f"   📧 {s['email']}\n"
            msg += f"   📚 {s['course']}\n"
            msg += f"   💰 {s['amount']}\n\n"
        msg += "⚡ These students need manual enrollment verification!\n"
    elif report.get("suppressed"):
        msg += "✅ *No active unmatched students right now.*\n"
        msg += "Previously resolved manual-enrollment cases are being suppressed from alerts.\n"
    else:
        msg += "✅ *All payments matched with enrollments!*\n"
        msg += "Walang student na nag-bayad pero hindi naka-enroll. 🎉\n"

    if report["matched_students"]:
        msg += "\n🟢 *Matched Students:*\n"
        for s in report["matched_students"]:
            msg += f"  ✅ {s['email']} - {s['course']}\n"

    return msg


def format_comparison_markdown(report):
    """Format the comparison report for markdown."""
    md = "### Enrollment Comparison\n\n"
    md += "| Metric | Count |\n"
    md += "|--------|-------|\n"
    md += f"| Xendit Payments | {report['total_payments']} |\n"
    md += f"| Systeme.io Enrollments | {report['total_enrolments']} |\n"
    md += f"| Matched | {report['matched']} |\n"
    md += f"| Unmatched | {report['unmatched']} |\n\n"

    if report["unmatched_students"]:
        md += "#### Unmatched Students (Paid but NOT Enrolled)\n\n"
        md += "| Email | Course | Amount |\n"
        md += "|-------|--------|--------|\n"
        for s in report["unmatched_students"]:
            md += f"| {s['email']} | {s['course']} | {s['amount']} |\n"

    return md
