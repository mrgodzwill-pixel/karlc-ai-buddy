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
from course_mapping import canonical_course_name
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
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
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


def _format_amount(value, currency="PHP"):
    if value in ("", None):
        return "N/A"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)

    if amount.is_integer():
        amount_text = f"{int(amount):,}"
    else:
        amount_text = f"{amount:,.2f}"

    return f"{(currency or 'PHP').upper()} {amount_text}"


def _combine_name_parts(*parts):
    values = [str(part or "").strip() for part in parts if str(part or "").strip()]
    return " ".join(values)


def _first_non_empty(*values):
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _looks_like_mobile_number(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("63") and len(digits) == 12 and digits[2:3] == "9":
        return True
    if digits.startswith("0") and len(digits) == 11 and digits[1:2] == "9":
        return True
    if digits.startswith("9") and len(digits) == 10:
        return True
    return False


def _phone_candidate(value):
    text = str(value or "").strip()
    return text if _looks_like_mobile_number(text) else ""


def _customer_to_payer_name(customer):
    if not customer:
        return ""

    individual = customer.get("individual_detail") or {}
    business = customer.get("business_detail") or {}
    return _combine_name_parts(
        individual.get("given_names"),
        individual.get("surname"),
    ) or str(business.get("business_name") or "").strip()


def _record_to_customer_shape(data):
    if not isinstance(data, dict):
        return {}

    customer = dict(data)
    name = _combine_name_parts(
        customer.get("first_name"),
        customer.get("last_name"),
    ) or str(customer.get("name") or "").strip()
    if name:
        customer.setdefault("individual_detail", {})
        if isinstance(customer["individual_detail"], dict):
            if not customer["individual_detail"].get("given_names"):
                customer["individual_detail"]["given_names"] = name
    top_level_given_names = str(customer.get("given_names") or "").strip()
    top_level_surname = str(customer.get("surname") or "").strip()
    if top_level_given_names or top_level_surname:
        customer.setdefault("individual_detail", {})
        if isinstance(customer["individual_detail"], dict):
            customer["individual_detail"].setdefault("given_names", top_level_given_names)
            customer["individual_detail"].setdefault("surname", top_level_surname)
    return customer


def _payment_record_date(record):
    return (
        record.get("paid_at")
        or record.get("date")
        or record.get("updated_at")
        or record.get("created_at")
        or record.get("created")
        or ""
    )


def _record_key(record):
    raw_key = "|".join([
        str(record.get("xendit_invoice_id", "") or ""),
        str(record.get("xendit_payment_id", "") or ""),
        str(record.get("payment_request_id", "") or ""),
        str(record.get("external_id", "") or ""),
        str(record.get("invoice_id", "") or ""),
        str(record.get("subject", "") or ""),
        str(_payment_record_date(record) or ""),
        str(record.get("email", "") or ""),
        str(record.get("phone_normalized", "") or ""),
        str(record.get("amount", "") or ""),
    ])
    return hashlib.sha1(raw_key.encode("utf-8")).hexdigest()


def _finalize_record(record):
    final = dict(record or {})
    final["email"] = str(final.get("email") or "").strip().lower()
    final["payer_name"] = str(final.get("payer_name") or "").strip()
    final["phone"] = str(final.get("phone") or "").strip()
    if not final.get("phone_normalized"):
        final["phone_normalized"] = _normalise_phone_for_lookup(final.get("phone"))
    final["record_id"] = _record_key(final)
    return final


def _strict_course_key(value):
    canonical = canonical_course_name(value, allow_old_fallback=False)
    return str(canonical or "").strip().lower()


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
    return _finalize_record(record)


def build_record_from_invoice_data(invoice, source="xendit_invoice_api"):
    """Build a payment record from a legacy Xendit invoice object or webhook."""
    invoice = dict(invoice or {})
    status = str(invoice.get("status") or "").upper()
    if status not in {"PAID", "SETTLED"}:
        return None

    description = str(invoice.get("description") or "").strip()
    items = invoice.get("items") or []
    course = description
    if not course and items:
        course = ", ".join(
            str(item.get("name") or "").strip()
            for item in items
            if str(item.get("name") or "").strip()
        )

    amount_value = invoice.get("paid_amount")
    if amount_value in ("", None):
        amount_value = invoice.get("amount")
    currency = invoice.get("currency") or "PHP"
    invoice_customer = _record_to_customer_shape(invoice.get("customer"))
    payer_name = _first_non_empty(
        _customer_to_payer_name(invoice_customer),
        invoice.get("payer_name"),
        invoice.get("customer_name"),
    )
    payer_email = _first_non_empty(
        invoice.get("payer_email"),
        invoice_customer.get("email"),
        invoice.get("email"),
    )
    payer_phone = _first_non_empty(
        invoice_customer.get("mobile_number"),
        invoice_customer.get("phone_number"),
        invoice.get("mobile_number"),
        invoice.get("phone_number"),
    )

    record = {
        "status": "paid",
        "payer_name": payer_name,
        "email": payer_email,
        "phone": payer_phone,
        "phone_normalized": _normalise_phone_for_lookup(payer_phone),
        "course": course,
        "amount": _format_amount(amount_value, currency=currency),
        "payment_method": invoice.get("payment_method", "") or invoice.get("bank_code", ""),
        "payment_channel": invoice.get("payment_channel", ""),
        "payment_destination": invoice.get("payment_destination", ""),
        "invoice_id": "",
        "xendit_invoice_id": invoice.get("id", ""),
        "xendit_payment_id": invoice.get("payment_id", ""),
        "external_id": invoice.get("external_id", ""),
        "subject": description or invoice.get("external_id", ""),
        "date": invoice.get("paid_at") or invoice.get("updated") or invoice.get("created", ""),
        "paid_at": invoice.get("paid_at", ""),
        "created_at": invoice.get("created", ""),
        "updated_at": invoice.get("updated", ""),
        "currency": currency,
        "source": source,
        "description": description,
        "raw_status": status,
    }
    return _finalize_record(record)


def build_record_from_payment_data(payment_data, customer=None, source="xendit_payment_webhook"):
    """Build a payment record from a Xendit Payments API webhook payload."""
    payment_data = dict(payment_data or {})
    status = str(payment_data.get("status") or "").upper()
    if status not in {"SUCCEEDED", "PAID", "SETTLED"}:
        return None

    payment_details = payment_data.get("payment_details") or {}
    metadata = payment_data.get("metadata") or {}
    payload_customer = _record_to_customer_shape(payment_data.get("customer"))
    description = str(payment_data.get("description") or "").strip()
    payer_name = _first_non_empty(
        _customer_to_payer_name(customer),
        _customer_to_payer_name(payload_customer),
        payment_data.get("payer_name"),
        payment_details.get("payer_name"),
        metadata.get("payer_name"),
        metadata.get("customer_name"),
        metadata.get("full_name"),
        metadata.get("student_name"),
    )
    payer_email = _first_non_empty(
        (customer or {}).get("email"),
        payload_customer.get("email"),
        payment_data.get("payer_email"),
        payment_data.get("customer_email"),
        payment_data.get("email"),
        metadata.get("payer_email"),
        metadata.get("customer_email"),
        metadata.get("email"),
        metadata.get("student_email"),
    )
    payer_phone = _first_non_empty(
        (customer or {}).get("mobile_number"),
        (customer or {}).get("phone_number"),
        payload_customer.get("mobile_number"),
        payload_customer.get("phone_number"),
        payment_data.get("mobile_number"),
        payment_data.get("phone_number"),
        payment_data.get("payer_phone"),
        payment_data.get("customer_phone"),
        metadata.get("mobile_number"),
        metadata.get("phone_number"),
        metadata.get("payer_phone"),
        metadata.get("customer_phone"),
        metadata.get("phone"),
        metadata.get("student_phone"),
        _phone_candidate(payment_details.get("payer_account_number")),
    )
    currency = payment_data.get("currency") or "PHP"
    amount_value = payment_data.get("request_amount")
    if amount_value in ("", None):
        amount_value = payment_data.get("amount")

    record = {
        "status": "paid",
        "payer_name": payer_name,
        "email": payer_email,
        "phone": payer_phone,
        "phone_normalized": _normalise_phone_for_lookup(payer_phone),
        "course": (
            description
            or metadata.get("course", "")
            or metadata.get("course_name", "")
            or metadata.get("item_name", "")
            or metadata.get("product_name", "")
        ),
        "amount": _format_amount(amount_value, currency=currency),
        "payment_method": payment_data.get("payment_method", "") or payment_data.get("channel_code", ""),
        "payment_channel": payment_data.get("channel_code", ""),
        "invoice_id": metadata.get("invoice_id", "") or payment_data.get("invoice_id", ""),
        "xendit_invoice_id": metadata.get("invoice_id", "") or payment_data.get("invoice_id", ""),
        "xendit_payment_id": payment_data.get("payment_id", "") or payment_data.get("id", ""),
        "payment_request_id": payment_data.get("payment_request_id", ""),
        "external_id": payment_data.get("reference_id", "") or metadata.get("external_id", ""),
        "subject": description or payment_data.get("reference_id", "") or metadata.get("external_id", ""),
        "date": payment_data.get("updated") or payment_data.get("created", ""),
        "paid_at": payment_details.get("capture_timestamp", "") or payment_data.get("updated", ""),
        "created_at": payment_data.get("created", ""),
        "updated_at": payment_data.get("updated", ""),
        "currency": currency,
        "source": source,
        "description": description,
        "customer_id": payment_data.get("customer_id", ""),
        "raw_status": status,
    }
    return _finalize_record(record)


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
            if field == "course":
                existing_course_key = _strict_course_key(merged.get("course", ""))
                new_course_key = _strict_course_key(value)
                if existing_course_key and not new_course_key:
                    continue
            merged[field] = value
    return _finalize_record(merged)


def _timestamps_close(left, right, max_hours=48):
    if not left or not right:
        return False
    return abs((left - right).total_seconds()) <= max_hours * 3600


def _records_match(existing, new_record):
    for field in ("xendit_payment_id", "payment_request_id", "xendit_invoice_id", "invoice_id"):
        if existing.get(field) and new_record.get(field) and existing.get(field) == new_record.get(field):
            return True

    if (
        existing.get("external_id")
        and new_record.get("external_id")
        and existing.get("external_id") == new_record.get("external_id")
    ):
        if existing.get("amount") == new_record.get("amount") or not existing.get("amount") or not new_record.get("amount"):
            return True

    existing_email = str(existing.get("email") or "").lower()
    new_email = str(new_record.get("email") or "").lower()
    if existing_email and new_email and existing_email == new_email:
        existing_time = _parse_timestamp(_payment_record_date(existing))
        new_time = _parse_timestamp(_payment_record_date(new_record))
        existing_course_key = _strict_course_key(existing.get("course", ""))
        new_course_key = _strict_course_key(new_record.get("course", ""))
        if existing_course_key and new_course_key and existing_course_key != new_course_key:
            return False
        if _timestamps_close(existing_time, new_time) and (
            existing.get("amount") == new_record.get("amount")
            or not existing.get("amount")
            or not new_record.get("amount")
        ):
            return True

    return existing.get("record_id") and existing.get("record_id") == new_record.get("record_id")


def upsert_payment_records(records, checked_at=None):
    """Merge generic payment records into the local payment store."""
    checked_at = checked_at or datetime.now(PHT).isoformat()
    store = load_payment_store()
    payments = list(store.get("payments", []))

    inserted_or_updated = []
    for raw_record in records or []:
        if not raw_record:
            continue
        record = _finalize_record(raw_record)

        for idx, existing in enumerate(payments):
            if _records_match(existing, record):
                payments[idx] = _merge_record(existing, record)
                inserted_or_updated.append(payments[idx])
                break
        else:
            payments.append(record)
            inserted_or_updated.append(record)

    payments.sort(
        key=lambda item: _parse_timestamp(_payment_record_date(item)) or datetime.min.replace(tzinfo=PHT),
        reverse=True,
    )

    store = {
        "checked_at": checked_at,
        "payments": payments,
    }
    _save_payment_store(store)
    return store, inserted_or_updated


def sync_payment_records(messages, checked_at=None):
    """Merge parsed Xendit messages into the local payment store."""
    checked_at = checked_at or datetime.now(PHT).isoformat()

    parsed_records = []
    for message in messages or []:
        record = extract_payment_record(message)
        if not record:
            continue
        parsed_records.append(record)
    store, _ = upsert_payment_records(parsed_records, checked_at=checked_at)
    return store, parsed_records


def find_payment_by_email(email):
    """Find the most recent stored payment for an email."""
    email = str(email or "").strip().lower()
    if not email:
        return None

    payments = load_payment_store().get("payments", [])
    matches = [item for item in payments if item.get("email", "").lower() == email]
    matches.sort(
        key=lambda item: _parse_timestamp(_payment_record_date(item)) or datetime.min.replace(tzinfo=PHT),
        reverse=True,
    )
    return matches[0] if matches else None


def list_recent_payments(days_back=7, require_email=False):
    """Return recent stored Xendit payments sorted newest-first."""
    cutoff = datetime.now(PHT) - timedelta(days=days_back)
    recent = []
    for record in load_payment_store().get("payments", []):
        if str(record.get("status") or "").lower() not in {"paid", "settled", "succeeded"}:
            continue

        if require_email and not str(record.get("email") or "").strip():
            continue

        record_time = _parse_timestamp(_payment_record_date(record))
        if record_time is None or record_time < cutoff:
            continue

        recent.append(dict(record))

    recent.sort(
        key=lambda item: _parse_timestamp(_payment_record_date(item)) or datetime.min.replace(tzinfo=PHT),
        reverse=True,
    )
    return recent


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
        record.get("description", ""),
        record.get("subject", ""),
        record.get("external_id", ""),
        record.get("payment_channel", ""),
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
        scored.append(
            (
                score,
                _parse_timestamp(_payment_record_date(record)) or datetime.min.replace(tzinfo=PHT),
                reasons,
                record,
            )
        )

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
        date_label = _payment_record_date(item) or "unknown date"
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
