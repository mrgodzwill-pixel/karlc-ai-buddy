"""
Import a normalized Systeme student summary CSV into the local student store.

Expected columns:
- email
- courses (newline/bullet list)
- tags (newline/bullet list)

This can be fed from a local CSV file or a published/exported Google Sheet CSV URL.
"""

import csv
import io
import logging
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from config import (
    SYSTEME_STUDENTS_BASELINE_CSV_URL,
    SYSTEME_STUDENTS_BASELINE_LOCAL_CSV,
)
from systeme_students import upsert_systeme_student_snapshot

logger = logging.getLogger(__name__)


def available():
    return bool(SYSTEME_STUDENTS_BASELINE_CSV_URL or SYSTEME_STUDENTS_BASELINE_LOCAL_CSV)


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _split_bullets(value):
    lines = []
    for raw in str(value or "").splitlines():
        line = str(raw).strip()
        if not line:
            continue
        if line.startswith("•"):
            line = line[1:].strip()
        elif line.startswith("-"):
            line = line[1:].strip()
        if line:
            lines.append(line)
    return lines


def _row_to_snapshot(row, imported_at):
    email = str(row.get("email") or row.get("Email") or "").strip().lower()
    if not email:
        return None

    course_names = _split_bullets(row.get("courses") or row.get("Courses") or "")
    tags = _split_bullets(row.get("tags") or row.get("Tags") or "")

    courses = [
        {
            "id": "",
            "name": course_name,
            "kind": "course_bundle" if "bundle" in course_name.lower() else "course",
            "status": "enrolled",
            "date": imported_at,
            "source_event": "sheet.baseline_import",
        }
        for course_name in course_names
    ]

    return {
        "email": email,
        "contact_id": "",
        "name": "",
        "first_name": "",
        "surname": "",
        "phone": "",
        "tags": tags,
        "fields": {},
        "courses": courses,
        "sales": [],
        "last_event_at": imported_at,
        "source_event": "sheet.baseline_import",
    }


def import_summary_csv_text(csv_text, source_label="manual", imported_at=""):
    imported_at = str(imported_at or _now_iso()).strip()
    reader = csv.DictReader(io.StringIO(csv_text))
    rows_scanned = 0
    imported_students = 0
    skipped_without_email = 0

    for row in reader:
        rows_scanned += 1
        snapshot = _row_to_snapshot(row, imported_at)
        if not snapshot:
            skipped_without_email += 1
            continue
        imported = upsert_systeme_student_snapshot(
            snapshot,
            source_event="sheet.baseline_import",
            event_timestamp=imported_at,
        )
        if imported:
            imported_students += 1

    return {
        "ok": True,
        "source": source_label,
        "rows_scanned": rows_scanned,
        "students_imported": imported_students,
        "skipped_without_email": skipped_without_email,
        "imported_at": imported_at,
    }


def import_summary_csv_url(csv_url, timeout=30):
    try:
        import requests  # Optional dependency in some local test environments.

        response = requests.get(csv_url, timeout=timeout)
        response.raise_for_status()
        return import_summary_csv_text(response.text, source_label=csv_url)
    except ModuleNotFoundError:
        pass

    try:
        with urlopen(csv_url, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return import_summary_csv_text(
                response.read().decode(charset, errors="replace"),
                source_label=csv_url,
            )
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"Failed to fetch Systeme baseline CSV: {exc}") from exc


def import_summary_local_csv(path):
    with open(path, encoding="utf-8-sig") as handle:
        return import_summary_csv_text(handle.read(), source_label=path)


def run_configured_import():
    if SYSTEME_STUDENTS_BASELINE_CSV_URL:
        logger.info("Importing Systeme baseline student summary from CSV URL")
        return import_summary_csv_url(SYSTEME_STUDENTS_BASELINE_CSV_URL)
    if SYSTEME_STUDENTS_BASELINE_LOCAL_CSV:
        logger.info("Importing Systeme baseline student summary from local CSV")
        return import_summary_local_csv(SYSTEME_STUDENTS_BASELINE_LOCAL_CSV)
    return {
        "ok": False,
        "message": "No Systeme student summary source configured yet. Set `SYSTEME_STUDENTS_BASELINE_CSV_URL` in Railway.",
    }
