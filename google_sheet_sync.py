"""
Google Sheets write-back helpers for the Systeme student baseline sheet.

This module is optional at runtime. It only activates when both:
- a target spreadsheet ID is configured
- a Google service account JSON credential is configured
"""

from __future__ import annotations

import logging
from typing import Iterable

from config import (
    SYSTEME_STUDENTS_SHEET_ID,
    SYSTEME_STUDENTS_SHEET_NAME,
    get_google_service_account_info,
)
from systeme_students import load_student_store

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_HEADERS = [["email", "courses", "tags", "name", "phone"]]


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


def _sheet_range(a1_suffix: str):
    return f"{SYSTEME_STUDENTS_SHEET_NAME}!{a1_suffix}"


def _normalize_bullets(values: Iterable[str]):
    ordered = []
    seen = set()
    for raw in values or []:
        value = str(raw or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return "\n".join(f"• {value}" for value in ordered)


def _student_row_values(student: dict):
    courses = [course.get("name", "") for course in student.get("courses", []) if course.get("name")]
    tags = student.get("tags", [])
    return [
        str(student.get("email") or "").strip().lower(),
        _normalize_bullets(courses),
        _normalize_bullets(tags),
        str(student.get("name") or "").strip(),
        str(student.get("phone") or "").strip(),
    ]


def _get_sheet_values(a1_range: str):
    session = _authorized_session()
    response = session.get(_values_url(a1_range))
    response.raise_for_status()
    return response.json().get("values", [])


def _update_values(a1_range: str, values):
    session = _authorized_session()
    response = session.put(
        _values_url(a1_range),
        params={"valueInputOption": "RAW"},
        json={"range": a1_range, "majorDimension": "ROWS", "values": values},
    )
    response.raise_for_status()
    return response.json()


def _append_values(a1_range: str, values):
    session = _authorized_session()
    response = session.post(
        _append_url(a1_range),
        params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
        json={"range": a1_range, "majorDimension": "ROWS", "values": values},
    )
    response.raise_for_status()
    return response.json()


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

    for student in students:
        try:
            result = sync_student_record(student, allow_append=True)
            if result.get("ok"):
                if result.get("action") == "updated":
                    updated += 1
                elif result.get("action") == "appended":
                    appended += 1
            else:
                errors.append(result.get("message", "Unknown sync error"))
        except Exception as exc:
            logger.exception("Failed syncing student %s to Google Sheet", student.get("email", ""))
            errors.append(str(exc))

    return {
        "ok": not errors,
        "updated": updated,
        "appended": appended,
        "errors": errors[:10],
        "students_seen": len(students),
    }
