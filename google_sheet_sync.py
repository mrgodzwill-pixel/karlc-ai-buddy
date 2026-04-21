"""
Google Sheets write-back helpers for the Systeme student baseline sheet.

This module is optional at runtime. It only activates when both:
- a target spreadsheet ID is configured
- a Google service account JSON credential is configured
"""

from __future__ import annotations

import logging
import re
import time
from typing import Iterable

from config import (
    SYSTEME_STUDENTS_SHEET_ID,
    SYSTEME_STUDENTS_SHEET_NAME,
    SYSTEME_SHEET_EXCLUDED_TAGS,
    get_google_service_account_info,
)
from systeme_students import load_student_store

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_HEADERS = [["email", "courses", "tags", "name", "phone"]]
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_BATCH_SIZE = 200
_BULLET_PREFIXES = ("Ã¢ÂÂ¢", "â¢", "•", "-")


def available():
    return bool(SYSTEME_STUDENTS_SHEET_ID and get_google_service_account_info())


def _authorized_session():
    info = get_google_service_account_info()
    if not SYSTEME_STUDENTS_SHEET_ID:
        raise RuntimeError("SYSTEME_STUDENTS_SHEET_ID is not configured.")
    if not info:
        raise RuntimeError(
            "Google Sheets write-back is not configured. Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_JSON_B64."
        )

    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "google-auth is not installed. Add `google-auth` to requirements and redeploy."
        ) from exc

    credentials = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return AuthorizedSession(credentials)


def _values_url(a1_range: str):
    return (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SYSTEME_STUDENTS_SHEET_ID}/values/"
        f"{a1_range}"
    )


def _append_url(a1_range: str):
    return (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SYSTEME_STUDENTS_SHEET_ID}/values/"
        f"{a1_range}:append"
    )


def _batch_update_url():
    return f"https://sheets.googleapis.com/v4/spreadsheets/{SYSTEME_STUDENTS_SHEET_ID}/values:batchUpdate"


def _sheet_range(a1_suffix: str):
    return f"{SYSTEME_STUDENTS_SHEET_NAME}!{a1_suffix}"


def _clean_list_value(value: str):
    cleaned = str(value or "").replace("\u00a0", " ").strip()
    for marker in ("Ã¢ÂÂ¢", "â¢", "•"):
        cleaned = cleaned.replace(marker, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    while True:
        original = cleaned
        for prefix in _BULLET_PREFIXES:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
        if cleaned == original:
            break
    return cleaned.strip(" ,")


def _expand_list_values(values: Iterable[str]):
    expanded = []
    for raw in values or []:
        for raw_line in str(raw or "").splitlines():
            for raw_part in str(raw_line).split(","):
                cleaned = _clean_list_value(raw_part)
                if cleaned:
                    expanded.append(cleaned)
    return expanded


def _normalize_list(values: Iterable[str], *, excluded_values=None):
    excluded = {str(value).strip().lower() for value in (excluded_values or set()) if str(value).strip()}
    ordered = []
    seen = set()
    for value in _expand_list_values(values):
        if not value:
            continue
        key = value.lower()
        if key in excluded:
            continue
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ", ".join(ordered)


def _student_row_values(student: dict):
    courses = [course.get("name", "") for course in student.get("courses", []) if course.get("name")]
    tags = student.get("tags", [])
    return [
        str(student.get("email") or "").strip().lower(),
        _normalize_list(courses),
        _normalize_list(tags, excluded_values=SYSTEME_SHEET_EXCLUDED_TAGS),
        str(student.get("name") or "").strip(),
        str(student.get("phone") or "").strip(),
    ]


def _request_json(method: str, url: str, *, params=None, json=None, retries=5):
    session = _authorized_session()

    for attempt in range(retries):
        response = session.request(method, url, params=params, json=json)
        if response.status_code in _RETRYABLE_STATUS_CODES and attempt < retries - 1:
            wait_seconds = min(2 ** attempt, 8)
            logger.warning(
                "Google Sheets API %s %s returned %s; retrying in %ss",
                method,
                url,
                response.status_code,
                wait_seconds,
            )
            time.sleep(wait_seconds)
            continue

        response.raise_for_status()
        if not response.text.strip():
            return {}
        return response.json()

    return {}


def _get_sheet_values(a1_range: str):
    data = _request_json("GET", _values_url(a1_range))
    return data.get("values", [])


def _update_values(a1_range: str, values):
    return _request_json(
        "PUT",
        _values_url(a1_range),
        params={"valueInputOption": "RAW"},
        json={"range": a1_range, "majorDimension": "ROWS", "values": values},
    )


def _append_values(a1_range: str, values):
    return _request_json(
        "POST",
        _append_url(a1_range),
        params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
        json={"range": a1_range, "majorDimension": "ROWS", "values": values},
    )


def _batch_update_values(updates):
    if not updates:
        return {}
    return _request_json(
        "POST",
        _batch_update_url(),
        json={"valueInputOption": "RAW", "data": updates},
    )


def _ensure_headers():
    a1 = _sheet_range("A1:E1")
    existing = _get_sheet_values(a1)
    if existing and existing[0][:5] == _HEADERS[0]:
        return False
    _update_values(a1, _HEADERS)
    return True


def _find_existing_row(email: str):
    values = _get_sheet_values(_sheet_range("A:A"))
    for idx, row in enumerate(values[1:], start=2):
        if str((row[0] if row else "") or "").strip().lower() == email:
            return idx
    return None


def _pad_row(row, width=5):
    padded = list(row or [])
    if len(padded) < width:
        padded.extend([""] * (width - len(padded)))
    return padded[:width]


def sync_student_record(student: dict, allow_append=True):
    email = str((student or {}).get("email") or "").strip().lower()
    if not email:
        return {"ok": False, "message": "Student email is required."}
    if not available():
        return {"ok": False, "message": "Google Sheet write-back is not configured."}

    _ensure_headers()
    row_values = [_student_row_values(student)]
    row_number = _find_existing_row(email)
    if row_number:
        _update_values(_sheet_range(f"A{row_number}:E{row_number}"), row_values)
        return {"ok": True, "row": row_number, "action": "updated", "email": email}
    if not allow_append:
        return {"ok": False, "message": "Student email was not found in the sheet.", "email": email}
    _append_values(_sheet_range("A:E"), row_values)
    return {"ok": True, "row": None, "action": "appended", "email": email}


def sync_student_by_email(email: str, allow_append=True):
    email = str(email or "").strip().lower()
    if not email:
        return {"ok": False, "message": "Email is required."}

    for student in load_student_store().get("students", []):
        if str(student.get("email") or "").strip().lower() == email:
            return sync_student_record(student, allow_append=allow_append)

    return {"ok": False, "message": "Student not found in local store.", "email": email}


def sync_xendit_payment_record(record: dict):
    email = str((record or {}).get("email") or "").strip().lower()
    if not email:
        return {"ok": False, "message": "Payment record has no email."}

    existing_row = _find_existing_row(email)
    if not existing_row:
        return {"ok": False, "message": "No matching email row in sheet.", "email": email}

    current_row = _get_sheet_values(_sheet_range(f"A{existing_row}:E{existing_row}"))
    current_values = (current_row[0] if current_row else []) + ["", "", "", "", ""]
    name = str(record.get("payer_name") or current_values[3] or "").strip()
    phone = str(record.get("phone") or record.get("phone_normalized") or current_values[4] or "").strip()

    updated = [
        current_values[0] or email,
        current_values[1],
        current_values[2],
        name,
        phone,
    ]
    _update_values(_sheet_range(f"A{existing_row}:E{existing_row}"), [updated])
    return {"ok": True, "row": existing_row, "action": "updated_xendit", "email": email}


def sync_all_students():
    if not available():
        return {"ok": False, "message": "Google Sheet write-back is not configured."}

    students = load_student_store().get("students", [])
    updated = 0
    appended = 0
    errors = []
    updates = []
    appends = []

    try:
        values = _get_sheet_values(_sheet_range("A:E"))
        headers_missing = not values or _pad_row(values[0]) != _HEADERS[0]
        if headers_missing:
            _update_values(_sheet_range("A1:E1"), _HEADERS)
            values = _get_sheet_values(_sheet_range("A:E"))
    except Exception as exc:
        logger.exception("Failed reading Google Sheet before bulk sync")
        return {"ok": False, "updated": 0, "appended": 0, "errors": [str(exc)], "students_seen": len(students)}

    email_to_row = {}
    existing_rows = {}
    for idx, row in enumerate(values[1:], start=2):
        current = _pad_row(row)
        email = str(current[0] or "").strip().lower()
        if not email:
            continue
        email_to_row[email] = idx
        existing_rows[idx] = current

    for student in students:
        email = str(student.get("email") or "").strip().lower()
        if not email:
            errors.append("Student email is required.")
            continue

        desired = _student_row_values(student)
        row_number = email_to_row.get(email)
        if row_number:
            current = existing_rows.get(row_number, ["", "", "", "", ""])
            if current != desired:
                updates.append(
                    {
                        "range": _sheet_range(f"A{row_number}:E{row_number}"),
                        "majorDimension": "ROWS",
                        "values": [desired],
                    }
                )
                updated += 1
        else:
            appends.append(desired)
            appended += 1

    try:
        for start in range(0, len(updates), _BATCH_SIZE):
            _batch_update_values(updates[start : start + _BATCH_SIZE])
        for start in range(0, len(appends), _BATCH_SIZE):
            _append_values(_sheet_range("A:E"), appends[start : start + _BATCH_SIZE])
    except Exception as exc:
        logger.exception("Failed bulk syncing students to Google Sheet")
        errors.append(str(exc))

    return {
        "ok": not errors,
        "updated": updated,
        "appended": appended,
        "errors": errors[:10],
        "students_seen": len(students),
    }
