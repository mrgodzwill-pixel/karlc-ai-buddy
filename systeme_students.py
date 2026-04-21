"""
Local Systeme.io student/contact/enrollment store.

This module lets the bot learn which students exist in Systeme.io and which
courses they purchased or were enrolled into, based on incoming webhook events.
"""

import os
from datetime import datetime, timedelta, timezone

from config import DATA_DIR
from storage import file_lock, load_json, save_json
from xendit_payments import extract_lookup_criteria

PHT = timezone(timedelta(hours=8))
SYSTEME_STUDENTS_FILE = os.path.join(DATA_DIR, "systeme_students.json")


def _parse_timestamp(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=PHT)
    return dt.astimezone(PHT)


def _now_iso():
    return datetime.now(PHT).isoformat()


def load_student_store():
    return load_json(SYSTEME_STUDENTS_FILE, {"checked_at": "", "students": []})


def _save_student_store(store):
    save_json(SYSTEME_STUDENTS_FILE, store)


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


def _clean_phone(phone_number):
    return str(phone_number or "").strip()


def _phone_tokens(phone_number):
    digits = "".join(ch for ch in str(phone_number or "") if ch.isdigit())
    tokens = {digits} if digits else set()
    if digits.startswith("0") and len(digits) == 11:
        tokens.add(f"63{digits[1:]}")
        tokens.add(digits[1:])
    elif digits.startswith("63") and len(digits) == 12:
        tokens.add(f"0{digits[2:]}")
        tokens.add(digits[2:])
    elif digits.startswith("9") and len(digits) == 10:
        tokens.add(f"63{digits}")
        tokens.add(f"0{digits}")
    return {token for token in tokens if token}


def _first_non_empty(*values):
    for value in values:
        if isinstance(value, str):
            if value.strip():
                return value.strip()
        elif value not in (None, "", [], {}):
            return value
    return ""


def _extract_contact(payload, event_type):
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    if isinstance(data.get("contact"), dict):
        return data.get("contact"), "contact"
    if isinstance(payload.get("contact"), dict):
        return payload.get("contact"), "contact"
    if isinstance(data.get("customer"), dict):
        return data.get("customer"), "customer"
    if isinstance(payload.get("customer"), dict):
        return payload.get("customer"), "customer"
    return {}, ""


def _contact_email(contact):
    return str(contact.get("email") or "").strip().lower()


def _contact_name(contact):
    fields = _field_map(contact.get("fields") or {})
    first_name = _first_non_empty(
        fields.get("first_name"),
        fields.get("firstname"),
        fields.get("given_name"),
        fields.get("given_names"),
        fields.get("firstName"),
    )
    surname = _first_non_empty(
        fields.get("surname"),
        fields.get("last_name"),
        fields.get("lastname"),
        fields.get("lastName"),
    )
    full_name = _first_non_empty(
        contact.get("name"),
        " ".join(part for part in [first_name, surname] if part).strip(),
    )
    return {
        "name": full_name,
        "first_name": first_name,
        "surname": surname,
    }


def _contact_phone(contact):
    fields = _field_map(contact.get("fields") or {})
    return _clean_phone(
        _first_non_empty(
            fields.get("phone_number"),
            fields.get("phone"),
            fields.get("mobile"),
            fields.get("mobile_phone"),
            contact.get("phone_number"),
            contact.get("phone"),
        )
    )


def _contact_tags(contact):
    tags = []
    for tag in contact.get("tags") or []:
        if not isinstance(tag, dict):
            continue
        name = str(tag.get("name") or "").strip()
        if name:
            tags.append(name)
    return tags


def _course_entries(payload, event_type, event_at):
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    results = []

    def add_entry(source, kind, status):
        if not isinstance(source, dict):
            return
        name = str(source.get("name") or source.get("description") or "").strip()
        if not name:
            return
        entry = {
            "id": str(source.get("id") or "").strip(),
            "name": name,
            "kind": kind,
            "status": status,
            "date": event_at,
            "source_event": event_type,
        }
        if isinstance(source.get("description"), str) and source.get("description"):
            entry["description"] = source.get("description")
        results.append(entry)

    if isinstance(data.get("course"), dict):
        add_entry(data.get("course"), "course", "enrolled")
    if isinstance(data.get("course_bundle"), dict):
        add_entry(data.get("course_bundle"), "course_bundle", "enrolled")

    order_item = data.get("order_item") if isinstance(data.get("order_item"), dict) else {}
    for resource in order_item.get("resources") or []:
        if not isinstance(resource, dict):
            continue
        resource_type = str(resource.get("type") or "").strip().lower()
        resource_data = resource.get("data") if isinstance(resource.get("data"), dict) else {}
        if resource_type == "membership_course":
            add_entry(resource_data, "course", "sold")
        elif resource_type == "course_bundle":
            add_entry(resource_data, "course_bundle", "sold")

    if not results and isinstance(data.get("offer_price_plan"), dict):
        add_entry(data.get("offer_price_plan"), "offer", "sold")

    return results


def _sale_entry(payload, event_type, event_at):
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if event_type not in {"customer.sale.completed", "NEW_SALE"}:
        return None

    customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    offer_price_plan = data.get("offer_price_plan") if isinstance(data.get("offer_price_plan"), dict) else {}
    order = data.get("order") if isinstance(data.get("order"), dict) else {}

    return {
        "id": str(order.get("id") or payload.get("id") or "").strip(),
        "date": _first_non_empty(order.get("created_at"), event_at),
        "payment_processor": str(customer.get("payment_processor") or "").strip(),
        "offer_name": str(
            offer_price_plan.get("inner_name")
            or offer_price_plan.get("name")
            or ""
        ).strip(),
        "amount": offer_price_plan.get("direct_charge_amount"),
        "currency": str(offer_price_plan.get("currency") or "").strip(),
        "source_event": event_type,
    }


def _update_course_list(existing_courses, new_courses):
    merged = [dict(item) for item in existing_courses or []]
    index = {}
    for idx, item in enumerate(merged):
        key = (
            str(item.get("kind") or "").lower(),
            str(item.get("id") or "").lower(),
            str(item.get("name") or "").strip().lower(),
        )
        index[key] = idx

    status_rank = {"sold": 1, "enrolled": 2}

    for course in new_courses:
        key = (
            str(course.get("kind") or "").lower(),
            str(course.get("id") or "").lower(),
            str(course.get("name") or "").strip().lower(),
        )
        existing_idx = index.get(key)
        if existing_idx is None:
            merged.append(dict(course))
            index[key] = len(merged) - 1
            continue

        current = merged[existing_idx]
        if status_rank.get(str(course.get("status") or "").lower(), 0) >= status_rank.get(
            str(current.get("status") or "").lower(),
            0,
        ):
            current["status"] = course.get("status", current.get("status", ""))
        new_date = _parse_timestamp(course.get("date"))
        old_date = _parse_timestamp(current.get("date"))
        if new_date and (old_date is None or new_date >= old_date):
            current["date"] = course.get("date", current.get("date", ""))
            current["source_event"] = course.get("source_event", current.get("source_event", ""))
        if course.get("description") and not current.get("description"):
            current["description"] = course.get("description")

    merged.sort(
        key=lambda item: _parse_timestamp(item.get("date")) or datetime.min.replace(tzinfo=PHT),
        reverse=True,
    )
    return merged


def upsert_systeme_student(payload, event_type="", event_timestamp="", message_id=""):
    event_type = str(event_type or payload.get("type") or "").strip()
    event_at = str(event_timestamp or payload.get("created_at") or _now_iso()).strip()

    contact, _ = _extract_contact(payload, event_type)
    email = _contact_email(contact)
    if not email:
        return None

    name_parts = _contact_name(contact)
    phone_number = _contact_phone(contact)
    tags = _contact_tags(contact)
    course_entries = _course_entries(payload, event_type, event_at)
    sale_entry = _sale_entry(payload, event_type, event_at)
    fields = _field_map(contact.get("fields") or {})

    with file_lock(SYSTEME_STUDENTS_FILE):
        store = load_student_store()
        students = store.setdefault("students", [])
        student = None
        for item in students:
            if str(item.get("email") or "").lower() == email:
                student = item
                break

        if student is None:
            student = {
                "email": email,
                "contact_id": "",
                "name": "",
                "first_name": "",
                "surname": "",
                "phone": "",
                "tags": [],
                "fields": {},
                "courses": [],
                "sales": [],
                "events": [],
                "last_event_at": "",
            }
            students.append(student)

        student["contact_id"] = str(
            _first_non_empty(contact.get("id"), student.get("contact_id", ""))
        )
        student["name"] = _first_non_empty(name_parts.get("name"), student.get("name", ""))
        student["first_name"] = _first_non_empty(name_parts.get("first_name"), student.get("first_name", ""))
        student["surname"] = _first_non_empty(name_parts.get("surname"), student.get("surname", ""))
        student["phone"] = _first_non_empty(phone_number, student.get("phone", ""))
        student["fields"] = {**student.get("fields", {}), **fields}

        combined_tags = list(student.get("tags", []))
        for tag in tags:
            if tag not in combined_tags:
                combined_tags.append(tag)
        student["tags"] = combined_tags

        student["courses"] = _update_course_list(student.get("courses", []), course_entries)

        if sale_entry:
            sales = list(student.get("sales", []))
            sale_id = str(sale_entry.get("id") or "").strip()
            if sale_id and not any(str(item.get("id") or "") == sale_id for item in sales):
                sales.append(sale_entry)
            elif not sale_id:
                sales.append(sale_entry)
            sales.sort(
                key=lambda item: _parse_timestamp(item.get("date")) or datetime.min.replace(tzinfo=PHT),
                reverse=True,
            )
            student["sales"] = sales[:50]

        if event_type:
            events = list(student.get("events", []))
            signature = (event_type, event_at, str(message_id or ""))
            if not any(
                (
                    item.get("type"),
                    item.get("date"),
                    str(item.get("message_id") or ""),
                ) == signature
                for item in events
            ):
                events.append(
                    {
                        "type": event_type,
                        "date": event_at,
                        "message_id": str(message_id or ""),
                    }
                )
            events.sort(
                key=lambda item: _parse_timestamp(item.get("date")) or datetime.min.replace(tzinfo=PHT),
                reverse=True,
            )
            student["events"] = events[:50]

        latest = _parse_timestamp(student.get("last_event_at")) or datetime.min.replace(tzinfo=PHT)
        current = _parse_timestamp(event_at)
        if current and current >= latest:
            student["last_event_at"] = event_at

        store["checked_at"] = _now_iso()
        students.sort(key=lambda item: str(item.get("email") or ""))
        _save_student_store(store)
        return dict(student)


def upsert_systeme_student_snapshot(snapshot, source_event="api.backfill", event_timestamp="", message_id=""):
    """Merge a normalized student snapshot into the local Systeme store."""
    snapshot = dict(snapshot or {})
    email = str(snapshot.get("email") or "").strip().lower()
    if not email:
        return None

    event_type = str(source_event or snapshot.get("source_event") or "api.backfill").strip()
    event_at = str(event_timestamp or snapshot.get("last_event_at") or _now_iso()).strip()
    tags = [str(tag).strip() for tag in snapshot.get("tags", []) if str(tag).strip()]
    fields = _field_map(snapshot.get("fields") or {})
    courses = [dict(course) for course in snapshot.get("courses", []) if isinstance(course, dict)]
    sales = [dict(sale) for sale in snapshot.get("sales", []) if isinstance(sale, dict)]

    with file_lock(SYSTEME_STUDENTS_FILE):
        store = load_student_store()
        students = store.setdefault("students", [])
        student = None
        for item in students:
            if str(item.get("email") or "").lower() == email:
                student = item
                break

        if student is None:
            student = {
                "email": email,
                "contact_id": "",
                "name": "",
                "first_name": "",
                "surname": "",
                "phone": "",
                "tags": [],
                "fields": {},
                "courses": [],
                "sales": [],
                "events": [],
                "last_event_at": "",
            }
            students.append(student)

        student["contact_id"] = str(
            _first_non_empty(snapshot.get("contact_id"), student.get("contact_id", ""))
        )
        student["name"] = _first_non_empty(snapshot.get("name"), student.get("name", ""))
        student["first_name"] = _first_non_empty(snapshot.get("first_name"), student.get("first_name", ""))
        student["surname"] = _first_non_empty(snapshot.get("surname"), student.get("surname", ""))
        student["phone"] = _first_non_empty(snapshot.get("phone"), student.get("phone", ""))
        student["fields"] = {**student.get("fields", {}), **fields}

        combined_tags = list(student.get("tags", []))
        for tag in tags:
            if tag not in combined_tags:
                combined_tags.append(tag)
        student["tags"] = combined_tags

        student["courses"] = _update_course_list(student.get("courses", []), courses)

        if sales:
            merged_sales = list(student.get("sales", []))
            for sale_entry in sales:
                sale_id = str(sale_entry.get("id") or "").strip()
                if sale_id and any(str(item.get("id") or "") == sale_id for item in merged_sales):
                    continue
                merged_sales.append(sale_entry)
            merged_sales.sort(
                key=lambda item: _parse_timestamp(item.get("date")) or datetime.min.replace(tzinfo=PHT),
                reverse=True,
            )
            student["sales"] = merged_sales[:50]

        if event_type:
            events = list(student.get("events", []))
            signature = (event_type, event_at, str(message_id or ""))
            if not any(
                (
                    item.get("type"),
                    item.get("date"),
                    str(item.get("message_id") or ""),
                ) == signature
                for item in events
            ):
                events.append(
                    {
                        "type": event_type,
                        "date": event_at,
                        "message_id": str(message_id or ""),
                    }
                )
            events.sort(
                key=lambda item: _parse_timestamp(item.get("date")) or datetime.min.replace(tzinfo=PHT),
                reverse=True,
            )
            student["events"] = events[:50]

        latest = _parse_timestamp(student.get("last_event_at")) or datetime.min.replace(tzinfo=PHT)
        current = _parse_timestamp(event_at)
        if current and current >= latest:
            student["last_event_at"] = event_at

        store["checked_at"] = _now_iso()
        students.sort(key=lambda item: str(item.get("email") or ""))
        _save_student_store(store)
        return dict(student)


def list_recent_enrolments(days_back=7):
    cutoff = datetime.now(PHT) - timedelta(days=days_back)
    enrolments = []
    for student in load_student_store().get("students", []):
        for course in student.get("courses", []):
            if str(course.get("status") or "").lower() != "enrolled":
                continue
            when = _parse_timestamp(course.get("date"))
            if when is None or when < cutoff:
                continue
            enrolments.append(
                {
                    "email": student.get("email", ""),
                    "name": student.get("name", ""),
                    "phone": student.get("phone", ""),
                    "course": course.get("name", ""),
                    "date": course.get("date", ""),
                    "source_event": course.get("source_event", ""),
                }
            )

    enrolments.sort(
        key=lambda item: _parse_timestamp(item.get("date")) or datetime.min.replace(tzinfo=PHT),
        reverse=True,
    )
    return enrolments


def search_student_records(user_message, limit=5):
    criteria = extract_lookup_criteria(user_message)
    students = load_student_store().get("students", [])
    scored = []

    for student in students:
        score = 0
        reasons = []
        email = str(student.get("email") or "").lower()
        phone = str(student.get("phone") or "").strip()
        phone_tokens = _phone_tokens(phone)
        name = str(student.get("name") or "").strip().lower()
        haystack = " ".join(
            [
                email,
                phone,
                name,
                " ".join(str(course.get("name") or "") for course in student.get("courses", [])),
            ]
        ).lower()

        for query_email in criteria.get("emails", []):
            if email and email == query_email.lower():
                score += 120
                reasons.append(f"email={query_email}")

        for query_phone in criteria.get("phones", []):
            if phone_tokens.intersection(_phone_tokens(query_phone)):
                score += 110
                reasons.append(f"phone={query_phone}")

        for query_name in criteria.get("names", []):
            if query_name.lower() in name:
                score += 90
                reasons.append(f"name~{query_name}")

        for token in criteria.get("tokens", []):
            if token.lower() in haystack:
                score += 8

        if score > 0:
            scored.append((score, student.get("last_event_at", ""), reasons, student))

    scored.sort(
        key=lambda item: (
            item[0],
            _parse_timestamp(item[1]) or datetime.min.replace(tzinfo=PHT),
        ),
        reverse=True,
    )

    matches = []
    for score, _, reasons, student in scored[:limit]:
        enriched = dict(student)
        enriched["match_score"] = score
        enriched["match_reasons"] = reasons
        matches.append(enriched)

    return {
        "checked_at": load_student_store().get("checked_at", ""),
        "criteria": criteria,
        "matches": matches,
    }


def format_student_lookup_summary(user_message, limit=5):
    lookup = search_student_records(user_message, limit=limit)
    checked_at = _parse_timestamp(lookup.get("checked_at"))
    checked_label = checked_at.strftime("%Y-%m-%d %H:%M") + " PHT" if checked_at else "unknown"

    identifiers = []
    criteria = lookup.get("criteria", {})
    identifiers.extend(criteria.get("emails", []))
    identifiers.extend(criteria.get("phones", []))
    identifiers.extend(criteria.get("names", []))

    lines = [f"Stored Systeme students last synced: {checked_label}"]
    if identifiers:
        lines.append(f"Lookup identifiers: {', '.join(identifiers[:3])}")

    matches = lookup.get("matches", [])
    if not matches:
        lines.append("No matching Systeme student found.")
        return {
            "count": 0,
            "summary": "\n".join(lines),
            "matches": [],
            "checked_at": lookup.get("checked_at", ""),
        }

    lines.append(f"Matches found: {len(matches)}")
    for student in matches:
        course_names = [
            str(course.get("name") or "").strip()
            for course in student.get("courses", [])
            if str(course.get("name") or "").strip()
        ]
        courses_label = ", ".join(course_names[:5]) if course_names else "no course yet"
        lines.append(
            f"• {student.get('name') or 'Unknown'} | {student.get('email') or 'no email'} | "
            f"Courses: {courses_label}"
        )

    return {
        "count": len(matches),
        "summary": "\n".join(lines),
        "matches": matches,
        "checked_at": lookup.get("checked_at", ""),
    }


def format_course_enrollment_summary(course_query=""):
    """Format enrolled students grouped by course as summary counts for Telegram/chat."""
    store = load_student_store()
    students = store.get("students", [])
    checked_at = _parse_timestamp(store.get("checked_at", ""))
    checked_label = checked_at.strftime("%Y-%m-%d %H:%M") + " PHT" if checked_at else "unknown"
    query = str(course_query or "").strip().lower()

    grouped = {}
    for student in students:
        for course in student.get("courses", []):
            if str(course.get("status") or "").lower() != "enrolled":
                continue
            course_name = str(course.get("name") or "").strip()
            if not course_name:
                continue
            if query and query not in course_name.lower():
                continue
            grouped.setdefault(course_name, set())
            unique_key = (
                str(student.get("email") or "").strip().lower()
                or str(student.get("contact_id") or "").strip()
                or str(student.get("name") or "").strip().lower()
            )
            if unique_key:
                grouped[course_name].add(unique_key)

    if not grouped:
        if query:
            return (
                "📚 *Systeme Students by Course*\n"
                f"Last synced: {checked_label}\n\n"
                f"Walang enrolled students na nakita for `{course_query}` yet."
            )
        return (
            "📚 *Systeme Students by Course*\n"
            f"Last synced: {checked_label}\n\n"
            "Wala pang stored enrolled students."
        )

    lines = ["📚 *Systeme Students by Course*", f"Last synced: {checked_label}", ""]
    total_rows = 0
    for course_name in sorted(grouped):
        count = len(grouped[course_name])
        total_rows += count
        lines.append(f"• {course_name} | {count} students total")

    lines.append("")
    lines.append(f"Courses shown: {len(grouped)}")
    lines.append(f"Known enrolled student-course rows: {total_rows}")
    return "\n".join(lines)
