"""
Import a normalized Systeme student summary CSV into the local student store.

Expected columns:
- email
- courses (comma-separated or newline/bullet list)
- tags (comma-separated or newline/bullet list)

This can be fed from a local CSV file or a published/exported Google Sheet CSV URL.
"""

import csv
import io
import logging
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from config import (
    SYSTEME_STUDENTS_BASELINE_CSV_URL,
    SYSTEME_STUDENTS_BASELINE_LOCAL_CSV,
    SYSTEME_SHEET_EXCLUDED_TAGS,
)
from systeme_students import upsert_systeme_student_snapshot
from xendit_payments import find_payment_by_email

logger = logging.getLogger(__name__)


def available():
    return bool(SYSTEME_STUDENTS_BASELINE_CSV_URL or SYSTEME_STUDENTS_BASELINE_LOCAL_CSV)


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _split_list_values(value):
    lines = []
    seen = set()
    for raw_line in str(value or "").splitlines():
        for raw_part in str(raw_line).split(","):
            line = str(raw_part).replace("\u00a0", " ").strip()
            if not line:
                continue
            for _ in range(4):
                try:
                    repaired = line.encode("latin1").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    break
                if repaired == line:
                    break
                line = repaired
            while True:
                original = line
                for prefix in ("Ã¢ÂÂ¢", "â¢", "•", "-"):
                    if line.startswith(prefix):
                        line = line[len(prefix):].strip()
                if line == original:
                    break
            line = re.sub(r"\s+", " ", line).strip()
            line = line.strip(" ,")
            if line:
                key = line.lower()
                if key in seen:
                    continue
                seen.add(key)
                lines.append(line)
    return lines


def _row_to_snapshot(row, imported_at):
    email = str(row.get("email") or row.get("Email") or "").strip().lower()
    if not email:
        return None

    course_names = _split_list_values(row.get("courses") or row.get("Courses") or "")
    tags = [
        tag
        for tag in _split_list_values(row.get("tags") or row.get("Tags") or "")
        if tag.lower() not in SYSTEME_SHEET_EXCLUDED_TAGS
    ]

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


def _enrich_snapshot_from_xendit(snapshot):
    email = str((snapshot or {}).get("email") or "").strip().lower()
    if not email:
        return snapshot, False

    payment = find_payment_by_email(email)
    if not payment:
        return snapshot, False

    enriched = dict(snapshot)
    matched = False

    payer_name = str(payment.get("payer_name") or "").strip()
    payer_phone = str(payment.get("phone") or payment.get("phone_normalized") or "").strip()

    if payer_name and not str(enriched.get("name") or "").strip():
        enriched["name"] = payer_name
        matched = True

    if payer_phone and not str(enriched.get("phone") or "").strip():
        enriched["phone"] = payer_phone
        matched = True

    return enriched, matched


def import_summary_csv_text(csv_text, source_label="manual", imported_at=""):
    imported_at = str(imported_at or _now_iso()).strip()
    reader = csv.DictReader(io.StringIO(csv_text))
    rows_scanned = 0
    imported_students = 0
    skipped_without_email = 0
    xendit_matches = 0

    for row in reader:
        rows_scanned += 1
        snapshot = _row_to_snapshot(row, imported_at)
        if not snapshot:
            skipped_without_email += 1
            continue
        snapshot, enriched = _enrich_snapshot_from_xendit(snapshot)
        if enriched:
            xendit_matches += 1
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
        "xendit_matches": xendit_matches,
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
