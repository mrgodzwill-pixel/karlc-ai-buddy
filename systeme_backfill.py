"""
Systeme.io API backfill helpers.

Imports older enrolled students into the same local store used by live webhook
events so Telegram lookups and course summaries work with historical data too.
"""

import logging
import re
from datetime import datetime, timezone

import systeme_api
from config import (
    SYSTEME_TAG_MIKROTIK_BASIC,
    SYSTEME_TAG_MIKROTIK_DUAL_ISP,
    SYSTEME_TAG_MIKROTIK_HYBRID,
    SYSTEME_TAG_MIKROTIK_TRAFFIC,
    SYSTEME_TAG_MIKROTIK_10G,
    SYSTEME_TAG_MIKROTIK_OSPF,
    SYSTEME_TAG_FTTH,
    SYSTEME_TAG_SOLAR,
    SYSTEME_TAG_PISOWIFI,
    SYSTEME_TAG_BUNDLE4,
)
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


def _parse_sortable_timestamp(value):
    text = _string(value)
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except Exception:
        return text


def _merge_course_entries(existing_courses, new_courses):
    merged = [dict(course) for course in existing_courses or [] if isinstance(course, dict)]
    index = {}
    for idx, course in enumerate(merged):
        key = (_lower(course.get("name")), _lower(course.get("kind")))
        index[key] = idx

    status_rank = {"sold": 1, "enrolled": 2}

    for course in new_courses or []:
        if not isinstance(course, dict):
            continue
        key = (_lower(course.get("name")), _lower(course.get("kind")))
        existing_idx = index.get(key)
        if existing_idx is None:
            merged.append(dict(course))
            index[key] = len(merged) - 1
            continue

        current = merged[existing_idx]
        if status_rank.get(_lower(course.get("status")), 0) >= status_rank.get(_lower(current.get("status")), 0):
            current["status"] = course.get("status", current.get("status", ""))
        if _parse_sortable_timestamp(course.get("date")) >= _parse_sortable_timestamp(current.get("date")):
            current["date"] = course.get("date", current.get("date", ""))
            current["source_event"] = course.get("source_event", current.get("source_event", ""))
        if course.get("id") and not current.get("id"):
            current["id"] = course.get("id")

    return merged


def _merge_snapshot(existing, incoming):
    if existing is None:
        return dict(incoming)

    merged = dict(existing)
    merged["contact_id"] = merged.get("contact_id") or incoming.get("contact_id", "")
    merged["name"] = merged.get("name") or incoming.get("name", "")
    merged["first_name"] = merged.get("first_name") or incoming.get("first_name", "")
    merged["surname"] = merged.get("surname") or incoming.get("surname", "")
    merged["phone"] = merged.get("phone") or incoming.get("phone", "")
    merged["fields"] = {**incoming.get("fields", {}), **merged.get("fields", {})}

    tags = list(merged.get("tags", []))
    for tag in incoming.get("tags", []):
        if tag not in tags:
            tags.append(tag)
    merged["tags"] = tags

    merged["courses"] = _merge_course_entries(merged.get("courses", []), incoming.get("courses", []))
    merged["sales"] = list(merged.get("sales", [])) or list(incoming.get("sales", []))

    existing_last = _parse_sortable_timestamp(merged.get("last_event_at"))
    incoming_last = _parse_sortable_timestamp(incoming.get("last_event_at"))
    if incoming_last >= existing_last:
        merged["last_event_at"] = incoming.get("last_event_at", merged.get("last_event_at", ""))

    full_name = " ".join(part for part in [merged.get("first_name", ""), merged.get("surname", "")] if _string(part))
    if full_name:
        merged["name"] = full_name

    return merged


def _tag_course_mapping():
    mapping = {}

    def add(tag_name, course_name, kind="course"):
        normalized = _lower(tag_name)
        if normalized:
            mapping[normalized] = {"name": course_name, "kind": kind}
            automatic_variant = _lower(f"XENDIT_{tag_name}")
            mapping[automatic_variant] = {"name": course_name, "kind": kind}

    add(SYSTEME_TAG_MIKROTIK_BASIC, "MikroTik QuickStart: Configure From Scratch")
    add(SYSTEME_TAG_MIKROTIK_DUAL_ISP, "New Dual ISP Load Balancing with Auto Fail-over (CPU Friendly)")
    add(SYSTEME_TAG_MIKROTIK_HYBRID, "Hybrid Access Combo: IPoE + PPPoE")
    add(SYSTEME_TAG_MIKROTIK_TRAFFIC, "MikroTik Traffic Control Basics")
    add(SYSTEME_TAG_MIKROTIK_10G, "10G Core Part 1: ISP Aggregator")
    add(SYSTEME_TAG_MIKROTIK_OSPF, "10G Core Part 2: OSPF & Advanced Routing")
    add(SYSTEME_TAG_FTTH, "PLC & FBT Combo: Budget-Friendly FTTH Design")
    add(SYSTEME_TAG_SOLAR, "DIY Hybrid Solar Setup")
    add(SYSTEME_TAG_PISOWIFI, "10G Core Part 3: Centralized Pisowifi Setup")
    add(SYSTEME_TAG_BUNDLE4, "Complete MikroTik Mastery Bundle", kind="course_bundle")
    return mapping


def _courses_from_contact_tags(contact):
    mapping = _tag_course_mapping()
    event_at = _extract_timestamp(contact) or _now_iso()
    courses = []
    seen = set()

    for tag in contact.get("tags") or []:
        if isinstance(tag, dict):
            tag_name = _string(tag.get("name") or tag.get("label"))
            tag_id = _coerce_id(tag.get("id"))
        else:
            tag_name = _string(tag)
            tag_id = ""

        mapped = mapping.get(_lower(tag_name))
        if not mapped:
            continue

        key = (_lower(mapped["name"]), mapped["kind"])
        if key in seen:
            continue
        seen.add(key)

        courses.append(
            {
                "id": tag_id,
                "name": mapped["name"],
                "kind": mapped["kind"],
                "status": "enrolled",
                "date": event_at,
                "source_event": "api.backfill.tag",
            }
        )

    return courses


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


def run_systeme_backfill(contact_limit=100, contact_max_pages=500, enrollment_limit=100, enrollment_max_pages=500):
    """Import historical enrolled students from Systeme.io Public API."""
    if not systeme_api.available():
        return {
            "ok": False,
            "reason": "not_configured",
            "message": "Systeme Public API key is not configured yet.",
        }

    logger.info(
        "Starting Systeme backfill: contact_limit=%s contact_max_pages=%s enrollment_limit=%s enrollment_max_pages=%s",
        contact_limit,
        contact_max_pages,
        enrollment_limit,
        enrollment_max_pages,
    )

    logger.info("Fetching Systeme courses...")
    courses = systeme_api.list_courses(limit=100, max_pages=10, timeout=45) or []
    logger.info("Fetched Systeme courses: %s", len(courses))

    logger.info("Fetching Systeme contacts...")
    contacts = systeme_api.list_contacts(limit=contact_limit, max_pages=contact_max_pages, timeout=45)
    logger.info("Fetched Systeme contacts: %s", 0 if contacts is None else len(contacts))

    logger.info("Fetching Systeme enrollments...")
    enrollments = systeme_api.list_enrollments(limit=enrollment_limit, max_pages=enrollment_max_pages, timeout=45)
    logger.info("Fetched Systeme enrollments: %s", 0 if enrollments is None else len(enrollments))

    if contacts is None:
        return {
            "ok": False,
            "reason": "contacts_failed",
            "message": "Systeme contact backfill failed or timed out. Try `/systeme_sync` again in a bit.",
        }

    if enrollments is None:
        return {
            "ok": False,
            "reason": "enrollments_failed",
            "message": "Systeme enrollment backfill failed or timed out. Try `/systeme_sync` again in a bit.",
        }

    course_lookup = {_coerce_id(course.get("id")): course for course in courses if isinstance(course, dict)}
    contact_lookup = {}
    snapshots = {}

    tagged_contacts = 0

    logger.info("Building Systeme contact snapshots from %s contacts", len(contacts))
    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        snapshot = _contact_snapshot(contact)
        if not snapshot:
            continue
        tag_courses = _courses_from_contact_tags(contact)
        if tag_courses:
            snapshot["courses"] = tag_courses
            tagged_contacts += 1
        snapshots[snapshot["email"]] = _merge_snapshot(snapshots.get(snapshot["email"]), snapshot)
        if snapshot.get("contact_id"):
            contact_lookup[_string(snapshot["contact_id"])] = contact

    imported_students = 0
    skipped_without_email = 0
    enrollments_linked = 0

    logger.info("Linking %s Systeme enrollments into snapshots", len(enrollments))
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
            snapshots[email] = _merge_snapshot(snapshots.get(email), snapshot)
            snapshot = snapshots[email]

        course = _course_entry(enrollment, course_lookup)
        if course:
            snapshot.setdefault("courses", []).append(course)
            enrollments_linked += 1

        if contact_id and not snapshot.get("contact_id"):
            snapshot["contact_id"] = contact_id

        event_at = _extract_timestamp(enrollment)
        if event_at:
            snapshot["last_event_at"] = event_at

    logger.info("Writing %s Systeme student snapshots into local store", len(snapshots))
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

    logger.info(
        "Finished Systeme backfill: contacts=%s courses=%s enrollments=%s tagged_contacts=%s imported_students=%s skipped_without_email=%s",
        len(contacts),
        len(courses),
        len(enrollments),
        tagged_contacts,
        imported_students,
        skipped_without_email,
    )

    return {
        "ok": True,
        "checked_at": _now_iso(),
        "contacts_scanned": len(contacts),
        "courses_scanned": len(courses),
        "enrollments_scanned": len(enrollments),
        "enrollments_linked": enrollments_linked,
        "contacts_with_course_tags": tagged_contacts,
        "students_imported": imported_students,
        "skipped_without_email": skipped_without_email,
        "hit_contact_page_cap": len(contacts) >= contact_limit * contact_max_pages,
        "hit_enrollment_page_cap": len(enrollments) >= enrollment_limit * enrollment_max_pages,
    }
