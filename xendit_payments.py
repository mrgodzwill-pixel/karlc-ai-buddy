"""
Local Xendit payment parsing, storage, and lookup helpers.

This module turns Gmail Xendit payment emails into reusable local records so
the rest of the app can:
- compare payments vs enrollments
- manually verify student payments without re-querying Gmail every time
- answer Telegram natural-language payment questions by payer name/email/phone
"""

import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

from config import DATA_DIR
from storage import file_lock, load_json, save_json

PHT = timezone(timedelta(hours=8))
XENDIT_PAYMENTS_FILE = os.path.join(DATA_DIR, "xendit_payments.json")

XENDIT_SUCCESS_SUBJECT_KEYWORDS = (
    "INVOICE PAID",
    "SUCCESSFUL PAYMENT",
    "PAYMENT RECEIVED",
    "PAYMENT COMPLETED",
    "PEMBAYARAN BERHASIL",
    "PEMBAYARAN SUKSES",
    "PAID",
)

_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_PATTERN = re.compile(r"(?:\+?63|0)?9\d{9}")
_GENERIC_QUERY_WORDS = {
    "a", "ang", "ano", "anong", "ask", "asked", "ba", "bayad", "boss",
    "can", "check", "checked", "checking", "did", "do", "does", "email",
    "find", "for", "from", "have", "has", "how", "if", "invoice", "is",
    "kay", "latest", "lookup", "manual", "may", "me", "mobile", "na",
    "nagbayad", "nag-bayad", "nag", "ni", "number", "of", "on", "or",
    "paid", "payment", "payments", "payer", "phone", "please", "po",
    "query", "recent", "report", "sa", "search", "show", "si", "status",
    "student", "tell", "the", "today", "verify", "verification", "what",
    "who", "which", "xendit", "with", "yesterday", "yung",
}
_COURSE_HINT_WORDS = {
    "quickstart", "course", "courses", "dual", "isp", "hybrid", "traffic",
    "control", "core", "10g", "ospf", "ftth", "solar", "bundle",
}
_NAME_PREFIX_PATTERNS = (
    re.compile(r"\b(?:for|kay|ni|si)\s+([a-zA-Z][a-zA-Z0-9@.+\-\s]{2,})", re.IGNORECASE),
    re.compile(r"\b(?:payer|student|customer|name)\s+([a-zA-Z][a-zA-Z0-9@.+\-\s]{2,})", re.IGNORECASE),
)

_PAYER_NAME_LABELS = (
    "Payer Name",
    "Customer Name",
    "Full Name",
)
_PAYER_EMAIL_LABELS = (
    "Payer Email",
    "Email Address",
)
_PAYER_PHONE_LABELS = (
    "Payer Mobile Number",
    "Payer Phone Number",
    "Customer Mobile Number",
    "Customer Phone Number",
    "Mobile Number",
    "Phone Number",
)
_PAYMENT_METHOD_LABELS = (
    "Payment Method",
    "Paid Via",
    "Payment Channel",
    "Channel",
)
_INVOICE_ID_LABELS = (
    "Invoice ID",
    "Reference ID",
    "External ID",
)
_KNOWN_LABELS = {
    label.lower()
    for label in (
        _PAYER_NAME_LABELS
        + _PAYER_EMAIL_LABELS
        + _PAYER_PHONE_LABELS
        + _PAYMENT_METHOD_LABELS
        + _INVOICE_ID_LABELS
        + ("Amount", "Total", "Payment Amount")
    )
}


def subject_looks_paid(subject):
    """Return True if the subject line looks like a successful payment."""
    subject_upper = (subject or "").upper()
    return any(keyword in subject_upper for keyword in XENDIT_SUCCESS_SUBJECT_KEYWORDS)


def _parse_timestamp(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except Exception:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=PHT)

    return dt.astimezone(PHT)


def _normalise_email_body_lines(text):
    """Normalize HTML/plain email content into clean, line-oriented text."""
    text = unescape(text or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|td|th|li|table|tbody|thead|ul|ol|h[1-6])>", "\n", text)
    text = re.sub(r"(?i)<(p|div|tr|td|th|li|table|tbody|thead|ul|ol|h[1-6])[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r", "\n")
    lines = []
    for raw_line in text.split("\n"):
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return lines


def _normalise_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _normalise_name(value):
    value = _normalise_text(value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _clean_extracted_value(value):
    value = re.sub(r"\s+", " ", str(value or "")).strip(" :-")
    return value.strip()


def _looks_like_label(line):
    line = str(line or "").strip()
    if not line:
        return False
    if line.endswith(":"):
        return True
    return line.lower() in _KNOWN_LABELS


def _extract_labeled_value(text, labels):
    lines = _normalise_email_body_lines(text)
    if not lines:
        return None

    labels_lower = tuple(label.lower() for label in labels)
    for idx, line in enumerate(lines):
        line_lower = line.lower()
        for label, label_lower in zip(labels, labels_lower):
            if not line_lower.startswith(label_lower):
                continue

            remainder = _clean_extracted_value(line[len(label):])
            if remainder:
                return remainder

            if idx + 1 < len(lines) and not _looks_like_label(lines[idx + 1]):
                return _clean_extracted_value(lines[idx + 1])

    return None


def _normalise_phone_for_lookup(phone_number):
    digits = re.sub(r"\D", "", str(phone_number or ""))
    if not digits:
        return ""

    if digits.startswith("00"):
        digits = digits[2:]

    if digits.startswith("0") and len(digits) == 11:
        digits = f"63{digits[1:]}"
    elif digits.startswith("9") and len(digits) == 10:
        digits = f"63{digits}"

    return digits


def _phone_search_tokens(phone_number):
    normalized = _normalise_phone_for_lookup(phone_number)
    digits = re.sub(r"\D", "", str(phone_number or ""))
    tokens = {token for token in (normalized, digits) if token}
    if normalized.startswith("63") and len(normalized) == 12:
        tokens.add(f"0{normalized[2:]}")
        tokens.add(normalized[2:])
    if digits.startswith("0") and len(digits) == 11:
        tokens.add(digits[1:])
    return tokens


def extract_payer_email(text):
    """Extract the payer email from explicit Xendit payer fields only."""
    value = _extract_labeled_value(text, _PAYER_EMAIL_LABELS)
    if not value:
        return None

    match = _EMAIL_PATTERN.search(value)
    return match.group(0).lower() if match else None


def extract_payer_name(text):
    """Extract the payer's name from explicit Xendit labels."""
    value = _extract_labeled_value(text, _PAYER_NAME_LABELS)
    return _clean_extracted_value(value) if value else None


def extract_payer_phone(text):
    """Extract the payer phone number from Xendit labels."""
    value = _extract_labeled_value(text, _PAYER_PHONE_LABELS)
    if not value:
        return None

    digits = re.sub(r"\D", "", value)
    return _clean_extracted_value(value) if len(digits) >= 7 else None


def extract_payment_method(text):
    value = _extract_labeled_value(text, _PAYMENT_METHOD_LABELS)
    return _clean_extracted_value(value) if value else ""


def extract_invoice_id(text):
    value = _extract_labeled_value(text, _INVOICE_ID_LABELS)
    return _clean_extracted_value(value) if value else ""


def extract_course_from_subject(subject):
    """Extract course name from Xendit invoice subject."""
    subject_lower = (subject or "").lower()

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


def extract_amount(text):
    """Extract payment amount from email body."""
    value = _extract_labeled_value(text, ("Amount", "Total", "Payment Amount"))
    if value:
        match = re.search(r"(?:PHP|₱)?\s*([\d,]+(?:\.\d{2})?)", value, re.IGNORECASE)
        if match:
            return f"PHP {match.group(1)}"

    normalized = "\n".join(_normalise_email_body_lines(text))
    patterns = [
        r"(?:PHP|₱)\s*([\d,]+(?:\.\d{2})?)",
        r"Amount[:\s]*(?:PHP|₱)?\s*([\d,]+(?:\.\d{2})?)",
        r"Total[:\s]*(?:PHP|₱)?\s*([\d,]+(?:\.\d{2})?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            return f"PHP {match.group(1)}"
    return "N/A"


def _body_preview(text, max_chars=220):
    preview = " ".join(_normalise_email_body_lines(text))
    return preview[:max_chars]


def _record_key(record):
    raw_key = "|".join([
        str(record.get("invoice_id", "") or ""),
        str(record.get("subject", "") or ""),
        str(record.get("date", "") or ""),
        str(record.get("email", "") or ""),
        str(record.get("phone_normalized", "") or ""),
        str(record.get("amount", "") or ""),
    ])
    return hashlib.sha1(raw_key.encode("utf-8")).hexdigest()


def extract_payment_record(message):
    """Parse a Gmail Xendit message into a reusable local payment record."""
    subject = message.get("subject", "")
    if not subject_looks_paid(subject):
        return None

    body = message.get("body", "")
    payer_email = extract_payer_email(body)
    payer_name = extract_payer_name(body)
    payer_phone = extract_payer_phone(body)
    record = {
        "status": "paid",
        "payer_name": payer_name or "",
        "email": payer_email or "",
        "phone": payer_phone or "",
        "phone_normalized": _normalise_phone_for_lookup(payer_phone),
        "course": extract_course_from_subject(subject),
        "amount": extract_amount(body),
        "payment_method": extract_payment_method(body),
        "invoice_id": extract_invoice_id(body),
        "subject": subject,
        "date": message.get("date", ""),
        "source": "gmail_imap",
        "body_preview": _body_preview(body),
    }
    record["record_id"] = _record_key(record)
    return record


def load_payment_store():
    """Load the local Xendit payment store."""
    with file_lock(XENDIT_PAYMENTS_FILE):
        data = load_json(XENDIT_PAYMENTS_FILE, {"checked_at": "", "payments": []})

    if isinstance(data, list):
        data = {"checked_at": "", "payments": data}

    data.setdefault("checked_at", "")
    data.setdefault("payments", [])
    return data


def _save_payment_store(store):
    with file_lock(XENDIT_PAYMENTS_FILE):
        save_json(XENDIT_PAYMENTS_FILE, store)


def _merge_record(existing, new_record):
    merged = dict(existing or {})
    for field, value in (new_record or {}).items():
        if value not in ("", None):
            merged[field] = value
    merged["record_id"] = _record_key(merged)
    return merged


def sync_payment_records(messages, checked_at=None):
    """Merge parsed Xendit messages into the local payment store."""
    checked_at = checked_at or datetime.now(PHT).isoformat()
    store = load_payment_store()
    merged = {item.get("record_id"): item for item in store.get("payments", []) if item.get("record_id")}

    parsed_records = []
    for message in messages or []:
        record = extract_payment_record(message)
        if not record:
            continue
        parsed_records.append(record)
        record_id = record["record_id"]
        merged[record_id] = _merge_record(merged.get(record_id, {}), record)

    payments = list(merged.values())
    payments.sort(key=lambda item: _parse_timestamp(item.get("date")) or datetime.min.replace(tzinfo=PHT), reverse=True)

    store = {
        "checked_at": checked_at,
        "payments": payments,
    }
    _save_payment_store(store)
    return store, parsed_records


def find_payment_by_email(email):
    """Find the most recent stored payment for an email."""
    email = str(email or "").strip().lower()
    if not email:
        return None

    payments = load_payment_store().get("payments", [])
    matches = [item for item in payments if item.get("email", "").lower() == email]
    matches.sort(key=lambda item: _parse_timestamp(item.get("date")) or datetime.min.replace(tzinfo=PHT), reverse=True)
    return matches[0] if matches else None


def _extract_query_tokens(user_message):
    cleaned = re.sub(r"[^a-zA-Z0-9@\s.+-]", " ", str(user_message or "").lower())
    tokens = []
    for token in cleaned.split():
        if "@" in token:
            continue
        if len(token) < 2:
            continue
        if token in _GENERIC_QUERY_WORDS or token in _COURSE_HINT_WORDS:
            continue
        if token.isdigit() and len(token) < 7:
            continue
        tokens.append(token)
    return tokens


def extract_lookup_criteria(user_message):
    """Extract likely payment identifiers from a natural-language query."""
    user_message = str(user_message or "")
    emails = [match.lower() for match in _EMAIL_PATTERN.findall(user_message)]
    phones = []
    for match in _PHONE_PATTERN.findall(user_message):
        normalized = _normalise_phone_for_lookup(match)
        if normalized:
            phones.append(normalized)

    names = []
    for pattern in _NAME_PREFIX_PATTERNS:
        for match in pattern.findall(user_message):
            cleaned = re.sub(r"\b(payment|paid|xendit|invoice|status)\b.*$", "", match, flags=re.IGNORECASE)
            cleaned = _clean_extracted_value(cleaned)
            if cleaned and cleaned.lower() not in _GENERIC_QUERY_WORDS:
                names.append(cleaned)

    tokens = _extract_query_tokens(user_message)
    if not names and tokens:
        token_text = " ".join(tokens)
        if token_text:
            names.append(token_text)

    # Preserve order but remove duplicates.
    def _unique(values):
        seen = set()
        ordered = []
        for value in values:
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(value)
        return ordered

    return {
        "emails": _unique(emails),
        "phones": _unique(phones),
        "names": _unique(names),
        "tokens": tokens,
    }


def _score_payment(record, criteria):
    score = 0
    reasons = []
    email = record.get("email", "").lower()
    phone_tokens = _phone_search_tokens(record.get("phone") or record.get("phone_normalized"))
    payer_name = record.get("payer_name", "")
    payer_name_norm = _normalise_name(payer_name)
    haystack = _normalise_text(" ".join([
        payer_name,
        record.get("email", ""),
        record.get("phone", ""),
        record.get("phone_normalized", ""),
        record.get("course", ""),
        record.get("subject", ""),
    ]))

    for query_email in criteria.get("emails", []):
        if email and email == query_email.lower():
            score += 120
            reasons.append(f"email={query_email}")

    for query_phone in criteria.get("phones", []):
        query_tokens = _phone_search_tokens(query_phone)
        if phone_tokens and phone_tokens.intersection(query_tokens):
            score += 110
            reasons.append(f"phone={query_phone}")

    for query_name in criteria.get("names", []):
        query_name_norm = _normalise_name(query_name)
        if query_name_norm and query_name_norm in payer_name_norm:
            score += 95
            reasons.append(f"name~{query_name}")

    for token in criteria.get("tokens", []):
        if token in haystack:
            score += 8

    return score, reasons


def search_payment_records(user_message, limit=5):
    """Search stored Xendit payments using a natural-language query."""
    criteria = extract_lookup_criteria(user_message)
    store = load_payment_store()
    payments = store.get("payments", [])

    scored = []
    for record in payments:
        score, reasons = _score_payment(record, criteria)
        if score <= 0:
            continue
        scored.append((score, _parse_timestamp(record.get("date")) or datetime.min.replace(tzinfo=PHT), reasons, record))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    matches = []
    for score, _, reasons, record in scored[:limit]:
        enriched = dict(record)
        enriched["match_score"] = score
        enriched["match_reasons"] = reasons
        matches.append(enriched)

    return {
        "checked_at": store.get("checked_at", ""),
        "criteria": criteria,
        "matches": matches,
    }


def format_payment_lookup_summary(user_message, limit=5):
    """Format a natural-language lookup against the local Xendit store."""
    lookup = search_payment_records(user_message, limit=limit)
    checked_at = _parse_timestamp(lookup.get("checked_at"))
    checked_label = checked_at.strftime("%Y-%m-%d %H:%M") + " PHT" if checked_at else "unknown"

    criteria = lookup.get("criteria", {})
    identifiers = []
    identifiers.extend(criteria.get("emails", []))
    identifiers.extend(criteria.get("phones", []))
    identifiers.extend(criteria.get("names", []))

    lines = [f"Stored Xendit payments last synced: {checked_label}"]
    if identifiers:
        lines.append(f"Lookup identifiers: {', '.join(identifiers[:3])}")

    matches = lookup.get("matches", [])
    if not matches:
        lines.append("No matching stored Xendit payment found.")
        return {
            "count": 0,
            "summary": "\n".join(lines),
            "matches": [],
            "checked_at": lookup.get("checked_at", ""),
            "criteria": criteria,
        }

    lines.append(f"Matches found: {len(matches)}")
    for item in matches:
        payer_name = item.get("payer_name") or "Unknown payer"
        email = item.get("email") or "no email"
        phone = item.get("phone") or item.get("phone_normalized") or "no phone"
        course = item.get("course") or item.get("subject") or "Unknown course"
        amount = item.get("amount") or "N/A"
        date_label = item.get("date") or "unknown date"
        lines.append(
            f"• {payer_name} | {email} | {phone} | {course} | {amount} | {date_label}"
        )

    return {
        "count": len(matches),
        "summary": "\n".join(lines),
        "matches": matches,
        "checked_at": lookup.get("checked_at", ""),
        "criteria": criteria,
    }
