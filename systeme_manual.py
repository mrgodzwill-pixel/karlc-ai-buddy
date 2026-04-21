"""
Manual Systeme.io contact creation and enrollment helpers.
"""

import re
from datetime import datetime, timezone

import systeme_api
from config import (
    COURSES,
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


def _fallback_contact_name(email=""):
    local = str(email or "").strip().split("@")[0]
    local = re.sub(r"[^a-zA-Z0-9]+", " ", local).strip()
    return local.title()[:64].strip() or "Student"


def _sanitize_name_fields(name="", email=""):
    first_name, surname, full_name = _split_name(name)
    full_name = str(full_name or "").strip()
    if not full_name:
        full_name = _fallback_contact_name(email)
        first_name, surname, _ = _split_name(full_name)

    full_name = full_name[:64].strip()
    first_name = str(first_name or "").strip()[:64]
    surname = str(surname or "").strip()[:64]
    if not first_name:
        first_name = _fallback_contact_name(email)[:64]
    return first_name, surname, full_name


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


def _course_key_from_query(course_query):
    query = _normalize(course_query)
    if not query:
        return ""

    exact_title_map = {
        _normalize("MikroTik QuickStart: Configure From Scratch"): "mikrotik_basic",
        _normalize("New Dual ISP Load Balancing with Auto Fail-over (CPU Friendly)"): "mikrotik_dual_isp",
        _normalize("Hybrid Access Combo: IPoE + PPPoE"): "mikrotik_hybrid",
        _normalize("MikroTik Traffic Control Basics"): "mikrotik_traffic",
        _normalize("10G Core Part 1: ISP Aggregator"): "mikrotik_10g",
        _normalize("10G Core Part 2: OSPF & Advanced Routing"): "mikrotik_ospf",
        _normalize("PLC & FBT Combo: Budget-Friendly FTTH Design"): "ftth",
        _normalize("DIY Hybrid Solar Setup"): "solar",
        _normalize("10G Core Part 3: Centralized Pisowifi Setup"): "pisowifi",
        _normalize("Complete MikroTik Mastery Bundle"): "bundle4",
    }
    if query in exact_title_map:
        return exact_title_map[query]

    aliases = _course_aliases()
    canonical = aliases.get(query, "")
    if canonical:
        for course_key, course in COURSES.items():
            if _normalize(course.get("name")) == _normalize(canonical):
                return course_key

    for course_key, course in COURSES.items():
        canonical_name = _normalize(course.get("name"))
        if query == canonical_name or query in canonical_name:
            return course_key
        keywords = [_normalize(keyword) for keyword in course.get("keywords", [])]
        if query in keywords:
            return course_key
        if any(query in keyword for keyword in keywords):
            return course_key

    return ""


def _special_course_keys(course_query):
    query = _normalize(course_query)
    specials = {
        "pisowifi": [
            "pisowifi",
            "centralized pisowifi",
            "10g core part 3",
            "10g core part 3 centralized pisowifi setup",
        ],
        "bundle4": [
            "bundle4",
            "complete mikrotik mastery bundle",
            "mikrotik mastery bundle",
            "bundle",
        ],
    }
    for course_key, keywords in specials.items():
        normalized_keywords = [_normalize(keyword) for keyword in keywords]
        if query in normalized_keywords:
            return course_key
        if any(query and query in keyword for keyword in normalized_keywords):
            return course_key
    return ""


def _fallback_old_tag_name(course_query):
    query = _normalize(course_query)
    if "bundle" in query or "3-in-1" in query or "3 in 1" in query or "3in1" in query:
        return "OLD_BUNDLE"
    return "OLD_COURSE"


def _configured_tag_name(course_key):
    env_map = {
        "mikrotik_basic": SYSTEME_TAG_MIKROTIK_BASIC,
        "mikrotik_dual_isp": SYSTEME_TAG_MIKROTIK_DUAL_ISP,
        "mikrotik_hybrid": SYSTEME_TAG_MIKROTIK_HYBRID,
        "mikrotik_traffic": SYSTEME_TAG_MIKROTIK_TRAFFIC,
        "mikrotik_10g": SYSTEME_TAG_MIKROTIK_10G,
        "mikrotik_ospf": SYSTEME_TAG_MIKROTIK_OSPF,
        "ftth": SYSTEME_TAG_FTTH,
        "solar": SYSTEME_TAG_SOLAR,
        "pisowifi": SYSTEME_TAG_PISOWIFI,
        "bundle4": SYSTEME_TAG_BUNDLE4,
    }
    return str(env_map.get(course_key) or "").strip()


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


def _resolve_tag_for_course(course_query):
    course_query = str(course_query or "").strip()
    if not course_query:
        raise ValueError("Course is required.")

    course_key = _course_key_from_query(course_query) or _special_course_keys(course_query)
    configured = _configured_tag_name(course_key) if course_key else ""
    candidate_names = []
    if configured:
        candidate_names.append(configured)
    if course_key:
        candidate_names.append(course_query)
    else:
        candidate_names.append(_fallback_old_tag_name(course_query))
    if course_key:
        course_name = str(COURSES.get(course_key, {}).get("name") or "").strip()
        if course_name:
            candidate_names.append(course_name)

    seen = set()
    for candidate in candidate_names:
        normalized = _normalize(candidate)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        tag = systeme_api.find_tag_by_name(candidate)
        if tag:
            return tag, candidate

    expected = configured or _fallback_old_tag_name(course_query)
    created = systeme_api.create_tag(expected)
    if created:
        return created, expected

    raise RuntimeError(
        "Could not find or create the Systeme tag needed for that course."
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

    first_name, surname, full_name = _sanitize_name_fields(name, email=email)
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

    first_name, surname, full_name = _sanitize_name_fields(name, email=email)
    contact = systeme_api.create_contact(
        email,
        first_name=first_name,
        surname=surname,
        full_name=full_name,
        phone_number=phone_number,
    )
    if not isinstance(contact, dict):
        raise RuntimeError("Systeme contact lookup/creation failed.")

    tag, expected_tag_name = _resolve_tag_for_course(course_query)
    tag_id = _coerce_id(tag.get("id"))
    if not tag_id:
        raise RuntimeError("Matched Systeme tag has no usable ID.")

    tag_assignment = systeme_api.assign_tag_to_contact(_coerce_id(contact.get("id")), tag_id)

    course_entry = {
        "id": "",
        "name": str(course_query).strip(),
        "kind": "course",
        "status": "sold",
        "date": _now_iso(),
        "source_event": "manual.tag_assigned",
    }
    snapshot = _snapshot_from_contact(
        contact,
        name=full_name,
        phone_number=phone_number,
        courses=[course_entry],
        event_type="manual.tag_assigned",
    )
    upsert_systeme_student_snapshot(snapshot, source_event="manual.tag_assigned", event_timestamp=course_entry["date"])

    resolved = None
    if ticket and resolve_ticket_on_success:
        resolved, _ = resolve_ticket(ticket["id"])

    return {
        "contact": contact,
        "course": {"name": course_query},
        "tag": tag,
        "tag_assignment": tag_assignment or {},
        "expected_tag_name": expected_tag_name,
        "email": email,
        "name": full_name or email,
        "phone_number": phone_number,
        "ticket": resolved or ticket,
        "ticket_id": ticket_id,
    }
