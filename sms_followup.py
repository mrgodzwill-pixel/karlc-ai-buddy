"""
Semaphore-powered SMS follow-ups for unresolved student tickets.
"""

import re

import requests

from config import SEMAPHORE_API_KEY, SEMAPHORE_ENABLED, SEMAPHORE_SENDER_NAME

SEMAPHORE_MESSAGES_URL = "https://api.semaphore.co/api/v4/messages"


def normalize_ph_phone_number(phone_number):
    """Normalize Philippine mobile numbers to 639XXXXXXXXX."""
    digits = re.sub(r"\D", "", str(phone_number or ""))
    if not digits:
        raise ValueError("Phone number is required.")

    if digits.startswith("0"):
        digits = f"63{digits[1:]}"
    elif digits.startswith("9"):
        digits = f"63{digits}"
    elif digits.startswith("63"):
        pass
    else:
        raise ValueError("Use a PH mobile number like 09171234567.")

    if not re.fullmatch(r"639\d{9}", digits):
        raise ValueError("Invalid PH mobile number format.")

    return digits


def _clean_course_label(course_title):
    title = str(course_title or "").strip()
    if not title:
        return "your Karl C course"
    if len(title) > 40 or re.search(r"-\d{6,}", title):
        return "your Karl C course"
    return title


def build_followup_message(ticket, contact_name):
    """Build a concise Taglish follow-up message based on ticket type."""
    first_name = (str(contact_name or "").strip().split() or ["Boss"])[0]
    course_label = _clean_course_label(ticket.get("course_title", ""))

    if ticket.get("type") == "enrollment_incomplete":
        return (
            f"Hi {first_name}, Karl C here. Nareceive namin payment mo for {course_label}, "
            "pero invalid or incomplete ang email na nagamit sa enrollment. "
            "Please email us at course@karlcomboy.com with your correct email para ma-activate namin. Salamat!"
        )

    return (
        f"Hi {first_name}, Karl C here. May kailangan lang kaming i-confirm sa student concern mo. "
        "Please email us at course@karlcomboy.com with your correct email and helpful details. Salamat!"
    )


def send_followup_sms(ticket, contact_name, phone_number):
    """Send an SMS follow-up through Semaphore."""
    if not SEMAPHORE_ENABLED:
        raise RuntimeError("Semaphore is not configured. Set SEMAPHORE_API_KEY first.")

    normalized_number = normalize_ph_phone_number(phone_number)
    message_text = build_followup_message(ticket, contact_name)

    payload = {
        "apikey": SEMAPHORE_API_KEY,
        "number": normalized_number,
        "message": message_text,
    }
    if SEMAPHORE_SENDER_NAME:
        payload["sendername"] = SEMAPHORE_SENDER_NAME

    response = requests.post(SEMAPHORE_MESSAGES_URL, data=payload, timeout=15)
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Semaphore returned non-JSON response ({response.status_code}).") from exc

    if response.status_code >= 400:
        error_text = data.get("message") or data.get("error") or str(data)
        raise RuntimeError(f"Semaphore error: {error_text}")

    entries = data if isinstance(data, list) else [data]
    if not entries:
        raise RuntimeError("Semaphore returned an empty response.")

    first_entry = entries[0]
    status = str(first_entry.get("status", "queued"))
    if str(first_entry.get("recipient", "")) != normalized_number:
        first_entry["recipient"] = normalized_number

    return {
        "recipient": normalized_number,
        "message_text": message_text,
        "status": status,
        "provider_message_id": str(first_entry.get("message_id", "")),
        "provider_response": first_entry,
    }
