"""
Minimal Systeme.io Public API helpers for one-time backfills.

The official docs clearly expose collection endpoints for contacts, courses,
and enrollments. The docs also say API keys can be sent in a header or query
string, so this client tries a few common auth patterns and caches whichever
one succeeds for the current process.
"""

import logging
import re
from typing import Any

import requests

from config import SYSTEME_API_BASE_URL, SYSTEME_API_KEY

logger = logging.getLogger(__name__)

_AUTH_MODE_CACHE = ""
_AUTH_MODES = (
    "bearer_header",
    "x_api_key_header",
    "x_api_key_header_lower",
    "query_api_key",
    "query_apiKey",
)


class SystemeAPIRequestError(Exception):
    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message or f"Systeme API request failed ({status_code})")
        self.status_code = status_code


def available():
    return bool(SYSTEME_API_KEY)


def _auth_variants():
    ordered = []
    if _AUTH_MODE_CACHE in _AUTH_MODES:
        ordered.append(_AUTH_MODE_CACHE)
    for mode in _AUTH_MODES:
        if mode not in ordered:
            ordered.append(mode)
    return ordered


def _with_auth(params, headers, mode):
    auth_headers = dict(headers or {})
    auth_params = dict(params or {})

    if mode == "bearer_header":
        auth_headers["Authorization"] = f"Bearer {SYSTEME_API_KEY}"
    elif mode == "x_api_key_header":
        auth_headers["X-API-Key"] = SYSTEME_API_KEY
    elif mode == "x_api_key_header_lower":
        auth_headers["X-Api-Key"] = SYSTEME_API_KEY
    elif mode == "query_api_key":
        auth_params["api_key"] = SYSTEME_API_KEY
    elif mode == "query_apiKey":
        auth_params["apiKey"] = SYSTEME_API_KEY

    return auth_params, auth_headers


def _extract_error_detail(response):
    try:
        payload = response.json()
    except Exception:
        return response.text[:200]

    if isinstance(payload, dict):
        for key in ("message", "detail", "error", "description"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return str(payload)[:200]


def _request(method, path, *, params=None, headers=None, json_body=None, timeout=20):
    global _AUTH_MODE_CACHE

    if not available():
        return None

    url = f"{SYSTEME_API_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    base_headers = {"accept": "application/json"}
    if headers:
        base_headers.update(headers)

    auth_failures = []

    for auth_mode in _auth_variants():
        req_params, req_headers = _with_auth(params, base_headers, auth_mode)
        try:
            request_kwargs = {
                "params": req_params,
                "headers": req_headers,
                "timeout": timeout,
            }
            if json_body is not None:
                request_kwargs["json"] = json_body
            response = requests.request(method, url, **request_kwargs)
        except Exception:
            logger.exception("Systeme request failed: %s %s", method, path)
            return None

        if response.status_code in (401, 403):
            auth_failures.append(auth_mode)
            continue

        if response.status_code >= 400:
            raise SystemeAPIRequestError(
                response.status_code,
                _extract_error_detail(response),
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise SystemeAPIRequestError(response.status_code, "Systeme returned non-JSON response") from exc

        _AUTH_MODE_CACHE = auth_mode
        return payload

    logger.warning("Systeme auth failed for %s %s using modes=%s", method, path, ",".join(auth_failures))
    return None


def _collection_items(payload: Any):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "results", "hydra:member", "member"):
            if isinstance(payload.get(key), list):
                return payload.get(key)
    return []


def _cursor_id(item):
    if not isinstance(item, dict):
        return ""

    for key in ("id", "contactId", "contact_id", "courseId", "course_id"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)

    for key in ("contact", "course"):
        value = item.get(key)
        if isinstance(value, dict) and value.get("id") not in (None, ""):
            return str(value.get("id"))

    return ""


def _list_collection(path, *, limit=100, max_pages=50, timeout=20):
    collected = []
    use_cursor_params = True
    starting_after = ""
    seen_cursors = set()

    for page_num in range(max_pages):
        params = {}
        if use_cursor_params:
            params = {"limit": limit, "order": "asc"}
            if starting_after:
                params["startingAfter"] = starting_after

        try:
            payload = _request("GET", path, params=params or None, timeout=timeout)
        except SystemeAPIRequestError as exc:
            # If the first page rejects cursor params, retry once without them.
            if use_cursor_params and page_num == 0 and exc.status_code == 400:
                logger.info("Systeme %s rejected cursor params; retrying without them", path)
                use_cursor_params = False
                continue
            logger.warning("Systeme request failed for %s: %s", path, exc)
            return None

        items = _collection_items(payload)
        if not items:
            break

        collected.extend(items)

        if not use_cursor_params:
            break

        if len(items) < limit:
            break

        cursor = _cursor_id(items[-1])
        if not cursor or cursor in seen_cursors:
            break

        seen_cursors.add(cursor)
        starting_after = cursor

    return collected


def list_contacts(limit=100, max_pages=50, timeout=20):
    return _list_collection("/contacts", limit=limit, max_pages=max_pages, timeout=timeout)


def list_courses(limit=100, max_pages=10, timeout=20):
    return _list_collection("/school/courses", limit=limit, max_pages=max_pages, timeout=timeout)


def list_enrollments(limit=100, max_pages=50, timeout=20):
    return _list_collection("/school/enrollments", limit=limit, max_pages=max_pages, timeout=timeout)


def list_tags(limit=100, max_pages=20, timeout=20):
    return _list_collection("/tags", limit=limit, max_pages=max_pages, timeout=timeout)


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


def _first_item(payload):
    items = _collection_items(payload)
    return items[0] if items else None


def find_contact_by_email(email, timeout=20):
    email = str(email or "").strip().lower()
    if not email:
        return None

    try:
        payload = _request("GET", "/contacts", params={"email": email}, timeout=timeout)
    except SystemeAPIRequestError as exc:
        logger.warning("Systeme contact lookup failed for %s: %s", email, exc)
        return None

    return _first_item(payload)


def _candidate_contact_payloads(email, first_name="", surname="", full_name="", phone_number=""):
    fields = {}
    if first_name:
        fields["first_name"] = first_name
    if surname:
        fields["surname"] = surname
    if phone_number:
        fields["phone_number"] = phone_number

    base_payload = {"email": email}
    if full_name:
        base_payload["name"] = full_name
    if fields:
        base_payload["fields"] = fields

    flat_payload = {"email": email}
    if first_name:
        flat_payload["first_name"] = first_name
    if surname:
        flat_payload["surname"] = surname
    if phone_number:
        flat_payload["phone_number"] = phone_number
    if full_name:
        flat_payload["name"] = full_name

    wrapped_payload = {"contact": dict(base_payload)}
    return [base_payload, flat_payload, wrapped_payload]


def create_contact(email, *, first_name="", surname="", full_name="", phone_number="", timeout=20):
    email = str(email or "").strip().lower()
    if not email:
        raise ValueError("Email is required")

    existing = find_contact_by_email(email, timeout=timeout)
    if existing:
        return existing

    last_error = None
    for payload in _candidate_contact_payloads(
        email,
        first_name=first_name,
        surname=surname,
        full_name=full_name,
        phone_number=phone_number,
    ):
        try:
            created = _request("POST", "/contacts", json_body=payload, timeout=timeout)
        except SystemeAPIRequestError as exc:
            last_error = exc
            message = str(exc).lower()
            if exc.status_code == 409 or "already" in message or "exists" in message or "duplicate" in message:
                existing = find_contact_by_email(email, timeout=timeout)
                if existing:
                    return existing
            continue

        if isinstance(created, dict):
            return created

    if last_error:
        raise last_error

    return None


def _candidate_enrollment_payloads(contact_id, course_id):
    contact_id = _coerce_id(contact_id)
    course_id = _coerce_id(course_id)
    return [
        {"contactId": contact_id, "courseId": course_id},
        {"contact_id": contact_id, "course_id": course_id},
        {"contact": {"id": contact_id}, "course": {"id": course_id}},
        {"contact": f"/api/contacts/{contact_id}", "course": f"/api/school/courses/{course_id}"},
        {"contact": contact_id, "course": course_id},
    ]


def create_enrollment(contact_id, course_id, timeout=20):
    if not contact_id or not course_id:
        raise ValueError("Both contact_id and course_id are required")

    last_error = None
    for payload in _candidate_enrollment_payloads(contact_id, course_id):
        try:
            created = _request("POST", "/school/enrollments", json_body=payload, timeout=timeout)
        except SystemeAPIRequestError as exc:
            last_error = exc
            continue

        if isinstance(created, dict):
            return created

    if last_error:
        raise last_error

    return None


def find_tag_by_name(tag_name, timeout=20):
    normalized = str(tag_name or "").strip().lower()
    if not normalized:
        return None

    tags = list_tags(timeout=timeout) or []
    exact = None
    partial = []
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        name = str(tag.get("name") or "").strip()
        if not name:
            continue
        lowered = name.lower()
        if lowered == normalized:
            exact = tag
            break
        if normalized in lowered or lowered in normalized:
            partial.append(tag)

    return exact or (partial[0] if partial else None)


def create_tag(name, timeout=20):
    name = str(name or "").strip()
    if not name:
        raise ValueError("Tag name is required")

    existing = find_tag_by_name(name, timeout=timeout)
    if existing:
        return existing

    created = _request("POST", "/tags", json_body={"name": name}, timeout=timeout)
    if isinstance(created, dict):
        return created
    return None


def assign_tag_to_contact(contact_id, tag_id, timeout=20):
    contact_id = _coerce_id(contact_id)
    tag_id = _coerce_id(tag_id)
    if not contact_id or not tag_id:
        raise ValueError("Both contact_id and tag_id are required")

    return _request(
        "POST",
        f"/contacts/{contact_id}/tags",
        json_body={"tagId": int(tag_id) if str(tag_id).isdigit() else tag_id},
        timeout=timeout,
    )
