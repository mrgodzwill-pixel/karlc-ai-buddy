"""
Systeme.io API backfill helpers.

Imports older enrolled students into the same local store used by live webhook
events so Telegram lookups and course summaries work with historical data too.
"""

import logging
import re
from datetime import datetime, timezone

import systeme_api
from systeme_students import upsert_systeme_student_snapshot

logger = logging.getLogger(__name__)


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _field_map(fields):
    if isinstance(fields, dict):
        return {str(key): value for key, value in fields.items()}

    mapped = {}
    for item in fields or []:
        if not isinstance(item, dict):
            continue
        key = item.get("slug") or item.get("fieldName") or item.get("name")
        if not key:
            continue
        mapped[str(key)] = item.get("value")
    return mapped


def _string(value):
    return str(value or "").strip()


def _lower(value):
    return _string(value).lower()


def _coerce_id(value):
    if value in (None, ""):
        return ""

    if isinstance(value, dict):
        for key in ("id", "@id", "contactId", "courseId"):
            nested = value.get(key)
            if nested not in (None, ""):
                return _coerce_id(nested)
        return ""

    text = _string(value)
    if not text:
        return ""
    if "/" in text:
        text = text.rstrip("/").split("/")[-1]
    match = re.search(r"(\d+)$", text)
    return match.group(1) if match else text


def _extract_email(record):
    if not isinstance(record, dict):
        return ""

    for key in ("email", "contactEmail", "contact_email", "studentEmail", "student_email"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()

    for key in ("contact", "student", "customer"):
        value = record.get(key)
        if isinstance(value, dict):
            email = _extract_email(value)
            if email:
                return email

    return ""


def _extract_name_parts(contact):
    contact = contact or {}
    fields = _field_map(contact.get("fields") or {})

    first_name = _string(
        fields.get("first_name")
        or fields.get("firstname")
        or fields.get("given_name")
        or fields.get("firstName")
        or contact.get("first_name")
        or contact.get("firstName")
    )
    surname = _string(
        fields.get("surname")
        or fields.get("last_name")
        or fields.get("lastname")
        or fields.get("lastName")
        or contact.get("surname")
        or contact.get("last_name")
        or contact.get("lastName")
    )
    full_name = _string(
        contact.get("name")
        or " ".join(part for part in [first_name, surname] if part).strip()
    )

    return {
        "name": full_name,
        "first_name": first_name,
        "surname": surname,
    }


def _extract_phone(contact):
    contact = contact or {}
    fields = _field_map(contact.get("fields") or {})
    return _string(
        fields.get("phone_number")
        or fields.get("phone")
        or fields.get("mobile")
        or fields.get("mobile_phone")
        or contact.get("phone_number")
        or contact.get("phone")
    )


def _extract_tags(contact):
    tags = []
    for tag in contact.get("tags") or []:
        if isinstance(tag, dict):
            name = _string(tag.get("name") or tag.get("label"))
        else:
            name = _string(tag)
        if name and name not in tags:
            tags.append(name)
    return tags


def _extract_contact_id(record):
    if not isinstance(record, dict):
        return ""

    for key in ("contactId", "contact_id", "studentId", "student_id", "customerId", "customer_id"):
        value = _coerce_id(record.get(key))
        if value:
            return value

    for key in ("contact", "student", "customer"):
        value = _coerce_id(record.get(key))
        if value:
            return value

    return _coerce_id(record.get("id"))


def _extract_course_id(record):
    if not isinstance(record, dict):
        return ""

    for key in ("courseId", "course_id", "schoolCourseId", "school_course_id"):
        value = _coerce_id(record.get(key))
        if value:
            return value

    for key in ("course", "schoolCourse"):
        value = _coerce_id(record.get(key))
        if value:
            return value

    return ""


def _extract_course_name(record):
    if not isinstance(record, dict):
        return ""

    for key in ("name", "title", "label"):
        value = _string(record.get(key))
        if value:
            return value

    for key in ("course", "schoolCourse"):
        value = record.get(key)
        if isinstance(value, dict):
            name = _extract_course_name(value)
            if name:
                return name

    return ""


def _extract_timestamp(record):
    if not isinstance(record, dict):
        return ""
    for key in (
        "createdAt",
        "created_at",
        "updatedAt",
        "updated_at",
        "enrolledAt",
        "enrolled_at",
        "date",
    ):
        value = _string(record.get(key))
        if value:
            return value
    return ""


def _contact_snapshot(contact):
    email = _extract_email(contact)
    if not email:
        return None

    names = _extract_name_parts(contact)
    return {
        "email": email,
        "contact_id": _coerce_id(contact.get("id")),
        "name": names["name"],
        "first_name": names["first_name"],
        "surname": names["surname"],
        "phone": _extract_phone(contact),
        "tags": _extract_tags(contact),
        "fields": _field_map(contact.get("fields") or {}),
        "courses": [],
        "sales": [],
        "last_event_at": _extract_timestamp(contact) or _now_iso(),
    }


def _course_entry(enrollment, course_lookup):
    course_id = _extract_course_id(enrollment)
    course_name = _extract_course_name(enrollment)

    if not course_name and course_id:
        course = course_lookup.get(course_id, {})
        course_name = _extract_course_name(course)

    if not course_name:
        return None

    return {
        "id": course_id,
        "name": course_name,
        "kind": "course",
        "status": "enrolled",
        "date": _extract_timestamp(enrollment) or _now_iso(),
        "source_event": "api.backfill",
    }


def _find_snapshot(snapshots, *, email="", contact_id=""):
    email = _lower(email)
    contact_id = _string(contact_id)

    if email and email in snapshots:
        return snapshots[email]

    if contact_id:
        for snapshot in snapshots.values():
            if _string(snapshot.get("contact_id")) == contact_id:
                return snapshot

    return None


def run_systeme_backfill(contact_limit=100, contact_max_pages=50, enrollment_limit=100, enrollment_max_pages=50):
    """Import historical enrolled students from Systeme.io Public API."""
    if not systeme_api.available():
        return {
            "ok": False,
            "reason": "not_configured",
            "message": "Systeme Public API key is not configured yet.",
        }

    courses = systeme_api.list_courses(limit=100, max_pages=10) or []
    contacts = systeme_api.list_contacts(limit=contact_limit, max_pages=contact_max_pages)
    enrollments = systeme_api.list_enrollments(limit=enrollment_limit, max_pages=enrollment_max_pages)

    if contacts is None:
        return {
            "ok": False,
            "reason": "contacts_failed",
            "message": "Systeme contact backfill failed. Check API key/auth.",
        }

    if enrollments is None:
        return {
            "ok": False,
            "reason": "enrollments_failed",
            "message": "Systeme enrollment backfill failed. Check API key/auth.",
        }

    course_lookup = {_coerce_id(course.get("id")): course for course in courses if isinstance(course, dict)}
    contact_lookup = {}
    snapshots = {}

    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        snapshot = _contact_snapshot(contact)
        if not snapshot:
            continue
        snapshots[snapshot["email"]] = snapshot
        if snapshot.get("contact_id"):
            contact_lookup[_string(snapshot["contact_id"])] = contact

    imported_students = 0
    skipped_without_email = 0
    enrollments_linked = 0

    for enrollment in enrollments:
        if not isinstance(enrollment, dict):
            continue

        contact_id = _extract_contact_id(enrollment)
        email = _extract_email(enrollment)
        if not email and contact_id and contact_id in contact_lookup:
            email = _extract_email(contact_lookup.get(contact_id))

        snapshot = _find_snapshot(snapshots, email=email, contact_id=contact_id)
        if snapshot is None and contact_id and contact_id in contact_lookup:
            snapshot = _contact_snapshot(contact_lookup[contact_id])
            if snapshot:
                snapshots[snapshot["email"]] = snapshot

        if snapshot is None:
            if not email:
                skipped_without_email += 1
                continue
            snapshot = {
                "email": email,
                "contact_id": contact_id,
                "name": "",
                "first_name": "",
                "surname": "",
                "phone": "",
                "tags": [],
                "fields": {},
                "courses": [],
                "sales": [],
                "last_event_at": _extract_timestamp(enrollment) or _now_iso(),
            }
            snapshots[email] = snapshot

        course = _course_entry(enrollment, course_lookup)
        if course:
            snapshot.setdefault("courses", []).append(course)
            enrollments_linked += 1

        if contact_id and not snapshot.get("contact_id"):
            snapshot["contact_id"] = contact_id

        event_at = _extract_timestamp(enrollment)
        if event_at:
            snapshot["last_event_at"] = event_at

    for snapshot in snapshots.values():
        if not snapshot.get("courses"):
            continue
        imported = upsert_systeme_student_snapshot(
            snapshot,
            source_event="api.backfill",
            event_timestamp=snapshot.get("last_event_at") or _now_iso(),
        )
        if imported:
            imported_students += 1

    return {
        "ok": True,
        "checked_at": _now_iso(),
        "contacts_scanned": len(contacts),
        "courses_scanned": len(courses),
        "enrollments_scanned": len(enrollments),
        "enrollments_linked": enrollments_linked,
        "students_imported": imported_students,
        "skipped_without_email": skipped_without_email,
    }
