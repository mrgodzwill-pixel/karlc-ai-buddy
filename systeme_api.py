"""
Minimal Systeme.io Public API helpers for one-time backfills.

The official docs clearly expose collection endpoints for contacts, courses,
and enrollments. The docs also say API keys can be sent in a header or query
string, so this client tries a few common auth patterns and caches whichever
one succeeds for the current process.
"""

import logging
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


def _request(method, path, *, params=None, headers=None, timeout=20):
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
            response = requests.request(
                method,
                url,
                params=req_params,
                headers=req_headers,
                timeout=timeout,
            )
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
