"""
Manual Systeme.io contact creation and enrollment helpers.
"""

import re
from datetime import datetime, timezone

import systeme_api
from config import COURSES
from systeme_students import upsert_systeme_student_snapshot
from ticket_system import get_ticket, resolve_ticket


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize(text):
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _split_name(full_name):
    parts = [part for part in str(full_name or "").strip().split() if part]
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", parts[0]
    return parts[0], " ".join(parts[1:]), " ".join(parts)


def _coerce_id(value):
    if value in (None, ""):
        return ""
    if isinstance(value, dict):
        for key in ("id", "@id", "contactId", "courseId"):
            nested = value.get(key)
            if nested not in (None, ""):
                return _coerce_id(nested)
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if "/" in text:
        text = text.rstrip("/").split("/")[-1]
    match = re.search(r"(\d+)$", text)
    return match.group(1) if match else text


def _course_aliases():
    aliases = {}
    for course in COURSES.values():
        canonical = str(course.get("name") or "").strip()
        if not canonical:
            continue
        aliases[_normalize(canonical)] = canonical
        for keyword in course.get("keywords", []):
            normalized = _normalize(keyword)
            if normalized and normalized not in aliases:
                aliases[normalized] = canonical
    return aliases


def _match_course(course_query, api_courses):
    query = _normalize(course_query)
    if not query:
        raise ValueError("Course is required.")

    courses = [course for course in (api_courses or []) if isinstance(course, dict)]
    if not courses:
        raise ValueError("Systeme course list is empty. Check `SYSTEME_API_KEY` and try again.")

    aliases = _course_aliases()
    canonical_hint = aliases.get(query, "")

    def course_name(course):
        return str(course.get("name") or course.get("title") or course.get("label") or "").strip()

    exact = [course for course in courses if _normalize(course_name(course)) == query]
    if exact:
        return exact[0]

    if canonical_hint:
        canonical_norm = _normalize(canonical_hint)
        exact_canonical = [course for course in courses if _normalize(course_name(course)) == canonical_norm]
        if exact_canonical:
            return exact_canonical[0]

    contains = [course for course in courses if query in _normalize(course_name(course))]
    if contains:
        contains.sort(key=lambda item: len(course_name(item)))
        return contains[0]

    if canonical_hint:
        canonical_norm = _normalize(canonical_hint)
        hinted = [course for course in courses if canonical_norm in _normalize(course_name(course))]
        if hinted:
            hinted.sort(key=lambda item: len(course_name(item)))
            return hinted[0]

    query_tokens = set(query.replace("-", " ").split())
    scored = []
    for course in courses:
        name = course_name(course)
        normalized = _normalize(name)
        tokens = set(normalized.replace("-", " ").split())
        score = len(query_tokens & tokens)
        if score:
            scored.append((score, len(name), course))

    if scored:
        scored.sort(key=lambda item: (-item[0], item[1]))
        return scored[0][2]

    available = ", ".join(course_name(course) for course in courses[:8])
    raise ValueError(
        "Could not match that course in Systeme. "
        f"Sample available courses: {available}"
    )


def _ticket_payload(ticket_id):
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise ValueError(f"Ticket #{ticket_id} not found.")
    return {
        "ticket": ticket,
        "email": str(ticket.get("student_email") or "").strip().lower(),
        "name": str(ticket.get("student_name") or "").strip(),
        "phone_number": str(ticket.get("phone_number") or "").strip(),
        "course_query": str(ticket.get("course_title") or "").strip(),
    }


def _snapshot_from_contact(contact, *, name="", phone_number="", courses=None, event_type="manual.contact_added"):
    first_name, surname, full_name = _split_name(name or contact.get("name") or "")
    return {
        "email": str(contact.get("email") or "").strip().lower(),
        "contact_id": _coerce_id(contact.get("id")),
        "name": full_name or str(contact.get("name") or "").strip(),
        "first_name": first_name,
        "surname": surname,
        "phone": str(phone_number or contact.get("phone_number") or contact.get("phone") or "").strip(),
        "tags": [],
        "fields": contact.get("fields") or {},
        "courses": list(courses or []),
        "sales": [],
        "last_event_at": _now_iso(),
        "source_event": event_type,
    }


def add_contact(email="", name="", phone_number="", ticket_id=None):
    if not systeme_api.available():
        raise ValueError("Systeme Public API key is not configured yet.")

    if ticket_id is not None:
        payload = _ticket_payload(ticket_id)
        email = email or payload["email"]
        name = name or payload["name"]
        phone_number = phone_number or payload["phone_number"]

    email = str(email or "").strip().lower()
    if not email:
        raise ValueError("Email is required.")

    first_name, surname, full_name = _split_name(name)
    contact = systeme_api.create_contact(
        email,
        first_name=first_name,
        surname=surname,
        full_name=full_name,
        phone_number=phone_number,
    )
    if not isinstance(contact, dict):
        raise RuntimeError("Systeme contact creation returned no contact record.")

    snapshot = _snapshot_from_contact(
        contact,
        name=full_name,
        phone_number=phone_number,
        courses=[],
        event_type="manual.contact_added",
    )
    upsert_systeme_student_snapshot(snapshot, source_event="manual.contact_added", event_timestamp=snapshot["last_event_at"])

    return {
        "contact": contact,
        "email": email,
        "name": full_name or email,
        "phone_number": phone_number,
        "ticket_id": ticket_id,
    }


def enroll_student(email="", course_query="", name="", phone_number="", ticket_id=None, resolve_ticket_on_success=True):
    if not systeme_api.available():
        raise ValueError("Systeme Public API key is not configured yet.")

    ticket = None
    if ticket_id is not None:
        payload = _ticket_payload(ticket_id)
        ticket = payload["ticket"]
        email = email or payload["email"]
        course_query = course_query or payload["course_query"]
        name = name or payload["name"]
        phone_number = phone_number or payload["phone_number"]

    email = str(email or "").strip().lower()
    course_query = str(course_query or "").strip()
    if not email:
        raise ValueError("Email is required.")
    if not course_query:
        raise ValueError("Course is required.")

    first_name, surname, full_name = _split_name(name)
    contact = systeme_api.create_contact(
        email,
        first_name=first_name,
        surname=surname,
        full_name=full_name,
        phone_number=phone_number,
    )
    if not isinstance(contact, dict):
        raise RuntimeError("Systeme contact lookup/creation failed.")

    courses = systeme_api.list_courses(limit=100, max_pages=20) or []
    matched_course = _match_course(course_query, courses)
    course_id = _coerce_id(matched_course.get("id"))
    if not course_id:
        raise RuntimeError("Matched Systeme course has no usable ID.")

    enrollment = None
    already_enrolled = False
    try:
        enrollment = systeme_api.create_enrollment(_coerce_id(contact.get("id")), course_id)
    except systeme_api.SystemeAPIRequestError as exc:
        text = str(exc).lower()
        if exc.status_code == 409 or "already" in text or "exists" in text or "duplicate" in text:
            already_enrolled = True
        else:
            raise

    course_entry = {
        "id": course_id,
        "name": str(matched_course.get("name") or matched_course.get("title") or course_query).strip(),
        "kind": "course",
        "status": "enrolled",
        "date": (
            str((enrollment or {}).get("created_at") or (enrollment or {}).get("createdAt") or _now_iso()).strip()
        ),
        "source_event": "manual.enrollment_created" if not already_enrolled else "manual.enrollment_existing",
    }
    snapshot = _snapshot_from_contact(
        contact,
        name=full_name,
        phone_number=phone_number,
        courses=[course_entry],
        event_type=course_entry["source_event"],
    )
    upsert_systeme_student_snapshot(snapshot, source_event=course_entry["source_event"], event_timestamp=course_entry["date"])

    resolved = None
    if ticket and resolve_ticket_on_success:
        resolved, _ = resolve_ticket(ticket["id"])

    return {
        "contact": contact,
        "course": matched_course,
        "enrollment": enrollment or {},
        "already_enrolled": already_enrolled,
        "email": email,
        "name": full_name or email,
        "phone_number": phone_number,
        "ticket": resolved or ticket,
        "ticket_id": ticket_id,
    }
