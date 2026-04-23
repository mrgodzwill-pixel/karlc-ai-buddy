"""
Manual Systeme.io contact creation and enrollment helpers.
"""

import re
from datetime import datetime, timezone

import systeme_api
from course_mapping import canonical_course_name, official_tag_name_for_course
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
from ticket_system import get_ticket, resolve_ticket, update_ticket_contact_details
from xendit_payments import load_payment_store


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize(text):
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _amount_digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _phone_digits(value):
    return re.sub(r"\D", "", str(value or ""))


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


def _course_query_variants(course_query):
    raw = str(course_query or "").strip()
    variants = []

    def add(value):
        normalized = _normalize(value)
        if normalized and normalized not in variants:
            variants.append(normalized)

    add(raw)
    cleaned = re.sub(r"\s*-\s*invoice\s+for\s+.+$", "", raw, flags=re.IGNORECASE).strip()
    add(cleaned)
    cleaned = re.sub(r"^invoice\s+paid\s*:\s*", "", cleaned, flags=re.IGNORECASE).strip()
    add(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:.")
    add(cleaned)
    return variants


def _course_key_from_query(course_query):
    queries = _course_query_variants(course_query)
    if not queries:
        return ""

    exact_title_map = {
        _normalize("MikroTik QuickStart: Configure From Scratch"): "mikrotik_basic",
        _normalize("Step-by-step kung paano mag-setup ng MikroTik RouterOS from scratch."): "mikrotik_basic",
        _normalize("Step-by-step kung paano mag-setup ng MikroTik RouterOS from scratch"): "mikrotik_basic",
        _normalize("MikroTik RouterOS from scratch"): "mikrotik_basic",
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
    for query in queries:
        if query in exact_title_map:
            return exact_title_map[query]

    aliases = _course_aliases()
    for query in queries:
        canonical = aliases.get(query, "")
        if canonical:
            for course_key, course in COURSES.items():
                if _normalize(course.get("name")) == _normalize(canonical):
                    return course_key

    for query in queries:
        for course_key, course in COURSES.items():
            canonical_name = _normalize(course.get("name"))
            if query == canonical_name or query in canonical_name or canonical_name in query:
                return course_key
            keywords = [_normalize(keyword) for keyword in course.get("keywords", [])]
            if query in keywords:
                return course_key
            if any(query in keyword or keyword in query for keyword in keywords):
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
        return "BUNDLE_PAID"
    return "1KW_PAID"


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

    expected = official_tag_name_for_course(course_query)
    tag = systeme_api.find_tag_by_name(expected, exact_only=True)
    if tag:
        return tag, expected

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
    ticket_type = str(ticket.get("type") or "").strip()
    if ticket_type and ticket_type != "enrollment_incomplete":
        raise ValueError(
            f"Ticket #{ticket_id} is `{ticket_type}`, not an enrollment ticket. "
            "Use `/systeme_enroll` only for Paid but Not Enrolled tickets."
        )
    raw_course = str(ticket.get("course_title") or "").strip()
    normalized_course = canonical_course_name(raw_course) or raw_course
    email = str(ticket.get("student_email") or "").strip().lower()
    name = str(ticket.get("student_name") or "").strip()
    phone_number = str(ticket.get("phone_number") or "").strip()

    recovered = _recover_ticket_payment_details(ticket, normalized_course)
    if recovered:
        email = email or str(recovered.get("email") or "").strip().lower()
        name = name or str(recovered.get("payer_name") or "").strip()
        phone_number = phone_number or str(recovered.get("phone") or recovered.get("phone_normalized") or "").strip()
        normalized_course = normalized_course or canonical_course_name(
            recovered.get("course") or recovered.get("description") or recovered.get("subject", "")
        )
        update_ticket_contact_details(
            ticket_id,
            student_name=name,
            student_email=email,
            course_title=normalized_course or raw_course,
            price=ticket.get("price") or recovered.get("amount", ""),
            payment_method=ticket.get("payment_method") or recovered.get("payment_method", ""),
            date_paid=ticket.get("date_paid") or recovered.get("paid_at") or recovered.get("date", ""),
            phone_number=phone_number,
        )
        ticket = get_ticket(ticket_id) or ticket

    if not email:
        raise ValueError(
            f"Ticket #{ticket_id} has no saved email and I couldn't recover one from Xendit yet."
        )
    if not normalized_course:
        raise ValueError(
            f"Ticket #{ticket_id} has no recognizable course title yet."
        )

    return {
        "ticket": ticket,
        "email": email,
        "name": name,
        "phone_number": phone_number,
        "course_query": normalized_course,
        "raw_course_query": raw_course,
    }


def _recover_ticket_payment_details(ticket, course_query=""):
    """Best-effort recovery of missing enrollment ticket details from Xendit data."""
    current_email = str(ticket.get("student_email") or "").strip().lower()
    current_name = str(ticket.get("student_name") or "").strip()
    current_phone = str(ticket.get("phone_number") or "").strip()
    current_amount = str(ticket.get("price") or "").strip()
    course_query = str(course_query or ticket.get("course_title") or "").strip()
    canonical_course = canonical_course_name(course_query, allow_old_fallback=True) or course_query
    canonical_course_norm = _normalize(canonical_course)
    name_norm = _normalize(current_name)
    amount_digits = _amount_digits(current_amount)
    phone_digits = _phone_digits(current_phone)

    best = None
    best_score = 0

    for payment in load_payment_store().get("payments", []):
        payment_email = str(payment.get("email") or "").strip().lower()
        if not payment_email:
            continue

        score = 0
        payment_course = canonical_course_name(
            payment.get("course") or payment.get("description") or payment.get("subject", ""),
            allow_old_fallback=True,
        ) or str(payment.get("course") or "").strip()
        payment_course_norm = _normalize(payment_course)

        if current_email and payment_email == current_email:
            score += 500
        if canonical_course_norm and payment_course_norm == canonical_course_norm:
            score += 180
        elif canonical_course_norm and payment_course_norm and (
            canonical_course_norm in payment_course_norm or payment_course_norm in canonical_course_norm
        ):
            score += 120

        payment_amount_digits = _amount_digits(payment.get("amount"))
        if amount_digits and payment_amount_digits and amount_digits == payment_amount_digits:
            score += 120

        payment_phone_digits = _phone_digits(payment.get("phone") or payment.get("phone_normalized"))
        if phone_digits and payment_phone_digits and (
            phone_digits == payment_phone_digits
            or phone_digits.endswith(payment_phone_digits)
            or payment_phone_digits.endswith(phone_digits)
        ):
            score += 140

        payer_name_norm = _normalize(payment.get("payer_name"))
        if name_norm and payer_name_norm:
            if name_norm == payer_name_norm:
                score += 140
            elif name_norm in payer_name_norm or payer_name_norm in name_norm:
                score += 100

        if score > best_score:
            best = payment
            best_score = score

    if not best:
        return None

    # Without an email already on the ticket, require a stronger match than
    # just a fuzzy course title so we don't tag the wrong student.
    minimum_score = 180 if current_email else 260
    if best_score < minimum_score:
        return None
    return best


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
        "kind": "course_bundle" if "bundle" in str(course_query).lower() else "course",
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
