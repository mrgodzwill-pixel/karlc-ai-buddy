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
from config import COURSES, DATA_DIR, OWNER_EMAIL, SYSTEME_SENDER
from course_mapping import canonical_course_name, canonical_course_names_from_tags
from storage import save_json
import systeme_api
import xendit_api
from systeme_students import load_student_store
import systeme_sheet_import
from xendit_payments import (
    extract_amount as _extract_amount,
    extract_course_from_subject as _extract_course_from_subject,
    extract_payer_email as _extract_payer_email,
    extract_payment_record,
    list_recent_payments,
    subject_looks_paid as _xendit_subject_looks_paid,
    sync_payment_records,
)
from xendit_sync import sync_recent_invoice_payments
from systeme_students import list_recent_enrolments as list_recent_systeme_enrolments

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
_ENROLMENT_QUERY_TEMPLATES = (
    f"from:{SYSTEME_SENDER} newer_than:{{days_back}}d",
    f"to:{SYSTEME_SENDER} newer_than:{{days_back}}d",
    f'"{SYSTEME_SENDER}" newer_than:{{days_back}}d',
)


def _amount_to_number(value):
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"([\d,]+(?:\.\d{1,2})?)", text.replace("₱", ""))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _course_keys_by_amount():
    mapping = {}
    for course in COURSES.values():
        price = course.get("price")
        if price in ("", None):
            continue
        try:
            price_value = float(price)
        except (TypeError, ValueError):
            continue
        course_key = _normalise_course_key(course.get("name", ""))
        if not course_key:
            continue
        mapping.setdefault(price_value, set()).add(course_key)

    # Historical / live prices that are still seen in Xendit, even if the
    # current config catalog has moved on. These aliases are only used for
    # enrollment comparison rescue, not for public price display.
    legacy_amounts = {
        1500.0: {"10G Core Part 3: Centralized Pisowifi Setup"},
        3500.0: {"New Dual ISP Load Balancing with Auto Fail-over (CPU Friendly)"},
        3997.0: {"Complete MikroTik Mastery Bundle"},
    }
    for price_value, course_names in legacy_amounts.items():
        for course_name in course_names:
            course_key = _normalise_course_key(course_name)
            if course_key:
                mapping.setdefault(price_value, set()).add(course_key)
    return mapping


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


def _record_to_payment_row(record):
    raw_course = record.get("course") or record.get("description") or record.get("subject", "")
    course = canonical_course_name(raw_course, allow_old_fallback=True) or str(raw_course or "").strip()
    return {
        "payer_name": record.get("payer_name", ""),
        "email": record.get("email", ""),
        "phone": record.get("phone", "") or record.get("phone_normalized", ""),
        "course": course,
        "course_raw": raw_course,
        "amount": record.get("amount", "N/A"),
        "payment_method": record.get("payment_method", ""),
        "subject": record.get("subject", ""),
        "date": record.get("paid_at") or record.get("date", ""),
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


def _search_enrolment_messages(days_back=7):
    """Search for enrollment confirmation emails using a few mailbox-safe queries."""
    combined = {}

    for template in _ENROLMENT_QUERY_TEMPLATES:
        query = template.format(days_back=days_back)
        messages = gmail_imap.search(query, limit=_XENDIT_SEARCH_LIMIT)
        if messages is None:
            return None

        print(f"[Enrollment] Systeme query '{query}' returned {len(messages)} messages")
        for message in messages:
            key = (
                message.get("date", ""),
                message.get("from", ""),
                message.get("subject", ""),
            )
            combined.setdefault(key, message)

    return list(combined.values())


def _normalise_course_key(course_name):
    canonical = canonical_course_name(course_name, allow_old_fallback=True) or str(course_name or "").strip()
    return re.sub(r"\s+", " ", canonical).strip().lower()


_COURSE_KEYS_BY_AMOUNT = _course_keys_by_amount()
_COURSE_PRICE_BY_KEY = {
    course_key: price_value
    for price_value, course_keys in _COURSE_KEYS_BY_AMOUNT.items()
    for course_key in course_keys
}


def _infer_enrolled_course_key_from_amount(amount, enrolled_course_keys, tolerance=2.0):
    amount_value = _amount_to_number(amount)
    if amount_value is None:
        return ""

    candidates = {
        course_key
        for course_key, price_value in _COURSE_PRICE_BY_KEY.items()
        if abs(price_value - amount_value) <= tolerance
    }
    if enrolled_course_keys:
        candidates &= set(enrolled_course_keys)
    if len(candidates) == 1:
        return next(iter(candidates))
    return ""


def _store_known_systeme_courses():
    """Return known Systeme student emails mapped to enrolled course keys."""
    courses_by_email = {}
    for student in load_student_store().get("students", []):
        email = str(student.get("email") or "").strip().lower()
        if not email:
            continue
        course_keys = {
            _normalise_course_key(course.get("name", ""))
            for course in student.get("courses", [])
            if isinstance(course, dict)
            and str(course.get("status") or "").lower() == "enrolled"
            and str(course.get("name") or "").strip()
        }
        course_keys.discard("")
        if course_keys:
            courses_by_email.setdefault(email, set()).update(course_keys)
    return courses_by_email


def _contact_course_keys(contact):
    """Best-effort extraction of course keys from Systeme contact tags."""
    if not isinstance(contact, dict):
        return set()

    tag_names = []
    for tag in contact.get("tags") or []:
        if isinstance(tag, dict):
            tag_name = str(tag.get("name") or tag.get("label") or "").strip()
        else:
            tag_name = str(tag or "").strip()
        if tag_name:
            tag_names.append(tag_name)

    return {
        _normalise_course_key(name)
        for name in canonical_course_names_from_tags(tag_names, allow_old_fallback=True)
        if _normalise_course_key(name)
    }


def _confirm_systeme_contact_payments(candidate_payments):
    """Check Systeme API directly for contacts that already have the same course access."""
    confirmed = set()
    if not systeme_api.available():
        return confirmed

    contact_cache = {}
    for payment in candidate_payments or []:
        email = str(payment.get("email") or "").strip().lower()
        course_key = _normalise_course_key(payment.get("course", ""))
        if not email:
            continue

        if email not in contact_cache:
            try:
                contact_cache[email] = systeme_api.find_contact_by_email(email, timeout=15)
            except Exception:
                logger.exception("Systeme API contact lookup failed for %s", email)
                contact_cache[email] = None

        contact_course_keys = _contact_course_keys(contact_cache.get(email))
        if course_key:
            if course_key in contact_course_keys:
                confirmed.add((email, course_key))
        elif contact_course_keys:
            confirmed.add((email, course_key))

    if confirmed:
        logger.info("Systeme API directly confirmed %s payment-course match(es)", len(confirmed))
    return confirmed


def _extract_enrolment_course(message):
    subject = str((message or {}).get("subject") or "").strip()
    body = str((message or {}).get("body") or "").strip()
    for candidate in (subject, body):
        canonical = canonical_course_name(candidate, allow_old_fallback=False)
        if canonical:
            return canonical
    return ""


def _enrolled_course_map(enrolments, store_courses_by_email):
    enrolled_by_email = {
        email: set(course_keys)
        for email, course_keys in (store_courses_by_email or {}).items()
        if email and course_keys
    }
    generic_emails = set()

    for enrolment in enrolments or []:
        email = str(enrolment.get("email") or "").strip().lower()
        if not email:
            continue
        course_key = _normalise_course_key(enrolment.get("course", ""))
        if course_key:
            enrolled_by_email.setdefault(email, set()).add(course_key)
        else:
            generic_emails.add(email)

    return enrolled_by_email, generic_emails


def _payment_is_enrolled(payment, enrolled_by_email, generic_emails):
    email = str(payment.get("email") or "").strip().lower()
    if not email:
        return False
    payment_course_key = _normalise_course_key(payment.get("course", ""))
    enrolled_course_keys = enrolled_by_email.get(email, set())
    raw_course = str(payment.get("course_raw") or payment.get("course", "")).strip()
    strict_canonical_course = canonical_course_name(raw_course, allow_old_fallback=False)
    amount_value = _amount_to_number(payment.get("amount", ""))

    # If Xendit's saved course text is weak/generic but the paid amount maps
    # uniquely to one of the student's enrolled courses, treat it as matched.
    inferred_course_key = ""
    if enrolled_course_keys:
        inferred_course_key = _infer_enrolled_course_key_from_amount(
            payment.get("amount", ""),
            enrolled_course_keys,
        )
        if inferred_course_key and not strict_canonical_course:
            return True

    if payment_course_key:
        if payment_course_key in enrolled_course_keys:
            return True

        expected_price = _COURSE_PRICE_BY_KEY.get(payment_course_key)
        if (
            inferred_course_key
            and inferred_course_key in enrolled_course_keys
            and payment_course_key != inferred_course_key
            and amount_value is not None
            and expected_price is not None
            and abs(expected_price - amount_value) > 2.0
        ):
            return True
        return payment_course_key in enrolled_course_keys
    return bool(enrolled_course_keys) or email in generic_emails


def _payment_match_key(payment):
    return (
        str(payment.get("email") or "").strip().lower(),
        _normalise_course_key(payment.get("course", "")),
    )


def _total_known_enrolments(enrolled_by_email, generic_emails):
    total = sum(len(course_keys) for course_keys in enrolled_by_email.values())
    generic_only = {email for email in generic_emails if not enrolled_by_email.get(email)}
    return total + len(generic_only)


def compare_payments_vs_enrolments(days_back=7):
    """Compare Xendit payments with Systeme.io enrollments."""
    print(f"[Enrollment] Comparing last {days_back} days...")

    if systeme_sheet_import.available():
        try:
            sheet_result = systeme_sheet_import.run_configured_import()
            if sheet_result.get("ok"):
                print(
                    f"[Enrollment] Systeme baseline sheet import refreshed {sheet_result.get('students_imported', 0)} student row(s)"
                )
            else:
                print(f"[Enrollment] Systeme baseline sheet import skipped: {sheet_result.get('message', 'not configured')}")
        except Exception:
            logger.exception("Systeme baseline sheet import failed before enrollment comparison")

    if not gmail_imap.available():
        print("[Enrollment] Gmail IMAP is required to read Systeme enrollment confirmations")
        return _unavailable_report()

    checked_at = datetime.now(PHT).isoformat()
    payments = []
    if xendit_api.available():
        api_records = sync_recent_invoice_payments(days_back=days_back)
        if api_records is not None:
            print(f"[Enrollment] Xendit API sync returned {len(api_records)} paid invoice record(s)")
        else:
            print("[Enrollment] Xendit API sync failed; falling back to Gmail parsing if available")

    recent_store_records = list_recent_payments(days_back=days_back, require_email=True)
    if recent_store_records:
        payments = [_record_to_payment_row(record) for record in recent_store_records]
        print(f"[Enrollment] Local Xendit store has {len(payments)} recent payment record(s) with payer emails")

    if not payments:
        xendit_msgs = _search_xendit_messages(days_back=days_back)
        if xendit_msgs is None:
            xendit_msgs = []

        print(f"[Enrollment] Xendit combined Gmail search returned {len(xendit_msgs)} unique messages")

        _, parsed_xendit_records = sync_payment_records(xendit_msgs, checked_at=checked_at)

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
                print(f"[Enrollment] Skipped Xendit msg (no payer email): {subject[:80]}")
                if len(missing_email_subjects) < 5:
                    missing_email_subjects.append(subject[:80] or "(no subject)")
                continue
            payments.append(_record_to_payment_row(record))

        print(
            f"[Enrollment] Parsed {len(parsed_xendit_records)} paid Xendit Gmail records; "
            f"{len(payments)} include payer emails for enrollment matching"
        )
        if not payments:
            if skipped_subjects:
                print(f"[Enrollment] Sample non-payment Xendit subjects: {skipped_subjects}")
            if missing_email_subjects:
                print(f"[Enrollment] Sample paid-like Xendit subjects missing payer email: {missing_email_subjects}")

    enrolments = []
    seen_enrolment_keys = set()
    direct_enrolments = list_recent_systeme_enrolments(days_back=days_back)
    if direct_enrolments:
        print(f"[Enrollment] Direct Systeme store has {len(direct_enrolments)} recent enrolment record(s)")
        for entry in direct_enrolments:
            student_email = str(entry.get("email") or "").strip().lower()
            course_name = canonical_course_name(entry.get("course", ""), allow_old_fallback=True) or str(
                entry.get("course") or ""
            ).strip()
            enrolment_key = (student_email, _normalise_course_key(course_name))
            if not student_email or enrolment_key in seen_enrolment_keys:
                continue
            seen_enrolment_keys.add(enrolment_key)
            enrolments.append(
                {
                    "email": student_email,
                    "course": course_name,
                    "date": entry.get("date", ""),
                    "subject": entry.get("course", ""),
                }
            )
    else:
        # Search enrollment / verification emails from Systeme.io.
        # Default sender is course@karlcomboy.com (configurable via SYSTEME_SENDER).
        enrolment_msgs = _search_enrolment_messages(days_back=days_back)
        if enrolment_msgs is None:
            enrolment_msgs = []

        for m in enrolment_msgs:
            student_email = _extract_enrolment_email(m.get("body", ""))
            course_name = _extract_enrolment_course(m)
            enrolment_key = (student_email, _normalise_course_key(course_name))
            if not student_email or enrolment_key in seen_enrolment_keys:
                continue
            seen_enrolment_keys.add(enrolment_key)
            enrolments.append({
                "email": student_email,
                "course": course_name,
                "date": m.get("date", ""),
                "subject": m.get("subject", ""),
            })

    print(f"[Enrollment] Found {len(enrolments)} enrolment confirmations")

    store_courses_by_email = _store_known_systeme_courses()
    if store_courses_by_email:
        print(
            f"[Enrollment] Stored Systeme student store contributes "
            f"{sum(len(courses) for courses in store_courses_by_email.values())} known enrolled course row(s)"
        )
    enrolled_by_email, generic_emails = _enrolled_course_map(enrolments, store_courses_by_email)

    matched, unmatched = [], []
    for p in payments:
        (matched if _payment_is_enrolled(p, enrolled_by_email, generic_emails) else unmatched).append(p)

    if unmatched:
        confirmed_unmatched = _confirm_systeme_contact_payments(unmatched)
        if confirmed_unmatched:
            matched.extend([p for p in unmatched if _payment_match_key(p) in confirmed_unmatched])
            unmatched = [p for p in unmatched if _payment_match_key(p) not in confirmed_unmatched]
            print(
                f"[Enrollment] Systeme API confirmed {len(confirmed_unmatched)} additional payment-course match(es); "
                f"updated unmatched count is {len(unmatched)}"
            )

    report = {
        "total_payments": len(payments),
        "total_enrolments": _total_known_enrolments(enrolled_by_email, generic_emails),
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
    if report.get("collapsed_unmatched_duplicates"):
        msg += (
            f"ℹ️ Repeated payment rows collapsed into ticket cases: "
            f"{report['collapsed_unmatched_duplicates']}\n\n"
        )
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
