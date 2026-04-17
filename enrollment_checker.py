"""
Enrollment Checker - Compares Xendit payments vs Systeme.io enrollments.
Identifies students who paid but haven't completed enrollment.

Gmail access is via IMAP + a Gmail App Password (see gmail_imap.py).
On Railway/Render set GMAIL_USER and GMAIL_APP_PASSWORD to enable.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import gmail_imap
from config import DATA_DIR, OWNER_EMAIL

PHT = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)


def _extract_payer_email(text):
    """Extract payer email from Xendit invoice email body."""
    patterns = [
        r'Payer Email[:\s]*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
        r'Email[:\s]*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).lower()
    return None


def _extract_course_from_subject(subject):
    """Extract course name from Xendit invoice subject.

    Subject format: "INVOICE PAID: karlcw-course-name-price-id"
    """
    subject_lower = subject.lower()

    course_map = {
        "quickstart": "MikroTik Basic (QuickStart)",
        "dual-isp": "MikroTik Dual-ISP",
        "hybrid-access": "MikroTik Hybrid",
        "traffic-control": "MikroTik Traffic Control",
        "core10g": "MikroTik 10G Core Part 1",
        "ospf": "MikroTik 10G Core Part 2 (OSPF)",
        "ftth": "Hybrid FTTH (PLC + FBT)",
        "solar": "DIY Hybrid Solar",
        "bundle": "Course Bundle",
    }

    for key, name in course_map.items():
        if key in subject_lower:
            return name

    return subject.split(":")[-1].strip() if ":" in subject else subject


def _extract_amount(text):
    """Extract payment amount from email body."""
    patterns = [
        r'(?:PHP|₱)\s*([\d,]+(?:\.\d{2})?)',
        r'Amount[:\s]*(?:PHP|₱)?\s*([\d,]+(?:\.\d{2})?)',
        r'Total[:\s]*(?:PHP|₱)?\s*([\d,]+(?:\.\d{2})?)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return f"PHP {match.group(1)}"
    return "N/A"


def _extract_enrolment_email(text):
    """Extract student email from New Enrolment email body."""
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    # Filter out known system / owner emails so the student email remains.
    system_emails = {"noreply@xendit.co", "noreply@systeme.io"}
    if OWNER_EMAIL:
        system_emails.add(OWNER_EMAIL)
    student_emails = [e.lower() for e in emails if e.lower() not in system_emails]
    return student_emails[0] if student_emails else None


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


def compare_payments_vs_enrolments(days_back=7):
    """Compare Xendit payments with Systeme.io enrollments."""
    print(f"[Enrollment] Comparing last {days_back} days...")

    if not gmail_imap.available():
        print("[Enrollment] GMAIL_USER/GMAIL_APP_PASSWORD not set - skipping enrollment check")
        return _unavailable_report()

    # Search Xendit invoice emails
    xendit_msgs = gmail_imap.search(
        f"from:noreply@xendit.co INVOICE PAID newer_than:{days_back}d",
        limit=30,
    )
    if xendit_msgs is None:
        # IMAP configured but connect failed; treat as unavailable.
        return _unavailable_report()

    payments = []
    for m in xendit_msgs:
        subject = m.get("subject", "")
        if "INVOICE PAID" not in subject.upper():
            continue
        body = m.get("body", "")
        payer_email = _extract_payer_email(body)
        if not payer_email:
            continue
        payments.append({
            "email": payer_email,
            "course": _extract_course_from_subject(subject),
            "amount": _extract_amount(body),
            "subject": subject,
            "date": m.get("date", ""),
        })

    print(f"[Enrollment] Found {len(payments)} Xendit invoices (with payer emails)")

    # Search enrollment confirmation emails.
    # If OWNER_EMAIL is set, scope the search to it; otherwise search any sender.
    sender_filter = f"from:{OWNER_EMAIL} " if OWNER_EMAIL else ""
    enrolment_msgs = gmail_imap.search(
        f"{sender_filter}New Enrolment newer_than:{days_back}d",
        limit=30,
    )
    if enrolment_msgs is None:
        enrolment_msgs = []

    enrolments = []
    for m in enrolment_msgs:
        subject_lower = m.get("subject", "").lower()
        if "enrol" not in subject_lower and "new" not in subject_lower:
            continue
        student_email = _extract_enrolment_email(m.get("body", ""))
        if student_email:
            enrolments.append({
                "email": student_email,
                "date": m.get("date", ""),
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
        "checked_at": datetime.now(PHT).isoformat(),
    }

    report_file = os.path.join(DATA_DIR, "enrollment_report.json")
    try:
        with open(report_file, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
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

    if report["unmatched_students"]:
        msg += "⚠️ *UNMATCHED - Paid but NOT Enrolled:*\n\n"
        for i, s in enumerate(report["unmatched_students"], 1):
            msg += f"🔴 *#{i}*\n"
            msg += f"   📧 {s['email']}\n"
            msg += f"   📚 {s['course']}\n"
            msg += f"   💰 {s['amount']}\n\n"
        msg += "⚡ These students need manual enrollment verification!\n"
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
