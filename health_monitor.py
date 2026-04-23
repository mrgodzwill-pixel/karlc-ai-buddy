"""
Health monitor helpers for Karl C AI Buddy.

This module summarizes the readiness and freshness of the integrations that
matter most to Karl's day-to-day workflow.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import gmail_imap
import google_sheet_sync
import systeme_api
import systeme_sheet_import
import xendit_api
from config import DATA_DIR
from storage import load_json
from systeme_students import load_student_store
from ticket_system import get_ticket_stats
from xendit_payments import load_payment_store

PHT = timezone(timedelta(hours=8))
ENROLLMENT_REPORT_FILE = os.path.join(DATA_DIR, "enrollment_report.json")
PROCESSED_XENDIT_WEBHOOKS_FILE = os.path.join(DATA_DIR, "processed_xendit_webhooks.json")
PROCESSED_SYSTEME_WEBHOOKS_FILE = os.path.join(DATA_DIR, "processed_systeme_webhooks.json")


def _parse_timestamp(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=PHT)
    return parsed.astimezone(PHT)


def _format_timestamp(value):
    parsed = _parse_timestamp(value)
    return parsed.strftime("%Y-%m-%d %H:%M") + " PHT" if parsed else "unknown"


def _age_minutes(value, *, now=None):
    parsed = _parse_timestamp(value)
    if not parsed:
        return None
    now = now or datetime.now(PHT)
    return max(0, int((now - parsed).total_seconds() // 60))


def _age_label(value, *, now=None):
    minutes = _age_minutes(value, now=now)
    if minutes is None:
        return "unknown"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _health_icon(ok=None, warning=False):
    if ok is True and not warning:
        return "✅"
    if ok is False:
        return "❌"
    return "⚠️"


def _webhook_activity(path, *, now=None):
    if not os.path.exists(path):
        return {"count": 0, "last_seen": "", "age_label": "never", "ok": False}

    try:
        payload = load_json(path, [])
    except Exception:
        payload = []

    count = len(payload) if isinstance(payload, list) else 0
    modified = datetime.fromtimestamp(os.path.getmtime(path), tz=PHT).isoformat()
    age_minutes = _age_minutes(modified, now=now)
    return {
        "count": count,
        "last_seen": modified,
        "age_label": _age_label(modified, now=now),
        "ok": age_minutes is not None and age_minutes <= 60 * 24 * 7,
    }


def build_health_report(*, now=None):
    now = now or datetime.now(PHT)

    payment_store = load_payment_store()
    student_store = load_student_store()
    enrollment_report = load_json(
        ENROLLMENT_REPORT_FILE,
        {
            "checked_at": "",
            "unmatched": 0,
            "matched": 0,
            "total_payments": 0,
            "total_enrolled_students": 0,
            "total_enrolments": 0,
        },
    )
    ticket_stats = get_ticket_stats()

    paid_records = [
        record
        for record in payment_store.get("payments", [])
        if str(record.get("status") or "").lower() in {"paid", "settled", "succeeded"}
    ]
    student_rows = student_store.get("students", [])
    enrolled_course_rows = sum(
        1
        for student in student_rows
        for course in student.get("courses", [])
        if isinstance(course, dict) and str(course.get("status") or "").lower() == "enrolled"
    )

    xendit_checked_at = payment_store.get("checked_at", "")
    systeme_checked_at = student_store.get("checked_at", "")
    enrollment_checked_at = enrollment_report.get("checked_at", "")

    xendit_age = _age_minutes(xendit_checked_at, now=now)
    systeme_age = _age_minutes(systeme_checked_at, now=now)
    enrollment_age = _age_minutes(enrollment_checked_at, now=now)

    report = {
        "checked_at": now.isoformat(),
        "gmail": {
            "configured": gmail_imap.available(),
            "status": "configured" if gmail_imap.available() else "missing config",
        },
        "xendit_api": {
            "configured": xendit_api.available(),
            "status": "enabled" if xendit_api.available() else "missing secret key",
        },
        "systeme_api": {
            "configured": systeme_api.available(),
            "status": "enabled" if systeme_api.available() else "missing API key",
        },
        "sheet_read": {
            "configured": systeme_sheet_import.available(),
            "status": "enabled" if systeme_sheet_import.available() else "missing CSV baseline URL",
        },
        "sheet_write": {
            "configured": google_sheet_sync.available(),
            "status": "enabled" if google_sheet_sync.available() else "missing sheet ID or Google service account",
        },
        "xendit_store": {
            "checked_at": xendit_checked_at,
            "checked_label": _format_timestamp(xendit_checked_at),
            "age_label": _age_label(xendit_checked_at, now=now),
            "payments": len(paid_records),
            "ok": xendit_age is not None and xendit_age <= 60 * 24 * 2,
        },
        "systeme_store": {
            "checked_at": systeme_checked_at,
            "checked_label": _format_timestamp(systeme_checked_at),
            "age_label": _age_label(systeme_checked_at, now=now),
            "students": len(student_rows),
            "course_rows": enrolled_course_rows,
            "ok": systeme_age is not None and systeme_age <= 60 * 24 * 2,
        },
        "enrollment_report": {
            "checked_at": enrollment_checked_at,
            "checked_label": _format_timestamp(enrollment_checked_at),
            "age_label": _age_label(enrollment_checked_at, now=now),
            "unmatched": int(enrollment_report.get("unmatched") or 0),
            "matched": int(enrollment_report.get("matched") or 0),
            "ok": enrollment_age is not None and enrollment_age <= 60 * 24,
        },
        "tickets": ticket_stats,
        "xendit_webhooks": _webhook_activity(PROCESSED_XENDIT_WEBHOOKS_FILE, now=now),
        "systeme_webhooks": _webhook_activity(PROCESSED_SYSTEME_WEBHOOKS_FILE, now=now),
    }

    return report


def format_health_report(report):
    gmail = report["gmail"]
    xendit_api_status = report["xendit_api"]
    systeme_api_status = report["systeme_api"]
    sheet_read = report["sheet_read"]
    sheet_write = report["sheet_write"]
    xendit_store = report["xendit_store"]
    systeme_store = report["systeme_store"]
    enrollment = report["enrollment_report"]
    tickets = report["tickets"]
    x_webhooks = report["xendit_webhooks"]
    s_webhooks = report["systeme_webhooks"]

    lines = [
        "🩺 *Bot Health*",
        "━━━━━━━━━━━━━━━━━━",
        f"🕐 Checked: {_format_timestamp(report.get('checked_at', ''))}",
        "",
        "*Config / Access*",
        f"{_health_icon(gmail['configured'])} Gmail IMAP: {gmail['status']}",
        f"{_health_icon(xendit_api_status['configured'])} Xendit API: {xendit_api_status['status']}",
        f"{_health_icon(systeme_api_status['configured'])} Systeme API: {systeme_api_status['status']}",
        f"{_health_icon(sheet_read['configured'])} Sheet Baseline Read: {sheet_read['status']}",
        f"{_health_icon(sheet_write['configured'])} Sheet Write-back: {sheet_write['status']}",
        "",
        "*Freshness*",
        f"{_health_icon(xendit_store['ok'])} Xendit Store: {xendit_store['payments']} paid records | {xendit_store['age_label']} ({xendit_store['checked_label']})",
        f"{_health_icon(systeme_store['ok'])} Systeme Store: {systeme_store['students']} students / {systeme_store['course_rows']} course rows | {systeme_store['age_label']} ({systeme_store['checked_label']})",
        f"{_health_icon(enrollment['ok'])} Enrollment Report: {enrollment['matched']} matched / {enrollment['unmatched']} unmatched | {enrollment['age_label']} ({enrollment['checked_label']})",
        "",
        "*Webhooks*",
        f"{_health_icon(x_webhooks['ok'])} Xendit: {x_webhooks['count']} processed keys | {x_webhooks['age_label']}",
        f"{_health_icon(s_webhooks['ok'])} Systeme: {s_webhooks['count']} processed keys | {s_webhooks['age_label']}",
        "",
        "*Tickets*",
        f"🎫 Pending: {tickets['pending']} | Enrollment: {tickets['enrollment_incomplete']} | Support: {tickets['support_email']}",
        f"✅ Resolved: {tickets['done']} | Total: {tickets['total']}",
    ]

    return "\n".join(lines)
