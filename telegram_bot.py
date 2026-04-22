"""
Telegram Bot for Karl C AI Buddy
- Gemini-powered natural language chat
- Command handling
- Report sending
- 24/7 listener
"""

import json
import logging
import os
import re
import time
import requests
import threading
from datetime import datetime, timedelta, timezone

from config import (
    TELEGRAM_API_URL, TELEGRAM_CHAT_ID,
    PAGE_NAME, GEMINI_MODEL,
    GEMINI_FALLBACK_MODELS, get_gemini_url,
    AI_BUDDY_SYSTEM_PROMPT, DATA_DIR, SEMAPHORE_ENABLED
)
from storage import file_lock, load_json, save_json

PHT = timezone(timedelta(hours=8))
MAX_MSG_LENGTH = 4096
_SYSTEME_BACKFILL_STATE_LOCK = threading.Lock()
_SYSTEME_BACKFILL_RUNNING = False
logger = logging.getLogger("telegram_bot")

TELEGRAM_COMMANDS = [
    {"command": "help", "description": "Show bot commands"},
    {"command": "status", "description": "Check bot and ticket status"},
    {"command": "report", "description": "Generate Facebook report now"},
    {"command": "pending", "description": "Show pending Facebook replies"},
    {"command": "keywords", "description": "Show auto-reply keywords"},
    {"command": "tickets", "description": "Show pending student tickets"},
    {"command": "done", "description": "Resolve ticket(s), e.g. /done 12"},
    {"command": "follow", "description": "Send SMS follow-up for a ticket"},
    {"command": "support", "description": "Show recent support emails"},
    {"command": "enrollment", "description": "Run payment vs enrollment check"},
    {"command": "students", "description": "Show enrolled students by course"},
    {"command": "systeme_sync", "description": "Refresh sheet baseline + Xendit info"},
    {"command": "systeme_api_sync", "description": "Run direct Systeme API backfill"},
    {"command": "systeme_add", "description": "Create Systeme contact"},
    {"command": "systeme_enroll", "description": "Tag/enroll student in Systeme"},
]

# Conversation history for AI chat
CONVERSATION_FILE = os.path.join(DATA_DIR, "conversation_history.json")


def _load_conversation():
    """Load conversation history."""
    if os.path.exists(CONVERSATION_FILE):
        with open(CONVERSATION_FILE) as f:
            data = json.load(f)
            # Keep only last 20 messages
            return data[-20:]
    return []


def _save_conversation(history):
    """Save conversation history."""
    with open(CONVERSATION_FILE, "w") as f:
        json.dump(history[-30:], f, indent=2, ensure_ascii=False)


# ============================================================
# GEMINI API FUNCTION
# ============================================================

def call_gemini(messages_history, system_prompt, user_message):
    """Call Google Gemini API with fast fallback on error."""
    # Build Gemini-format contents
    contents = []

    # Add conversation history
    for h in messages_history:
        role = "user" if h["role"] == "user" else "model"
        contents.append({
            "role": role,
            "parts": [{"text": h["content"]}]
        })

    # Add current user message
    contents.append({
        "role": "user",
        "parts": [{"text": user_message}]
    })

    payload = {
        "contents": contents,
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1000,
        }
    }

    # Try primary model, then fallbacks - no retries, just switch fast
    models_to_try = [GEMINI_MODEL] + [m for m in GEMINI_FALLBACK_MODELS if m != GEMINI_MODEL]

    for model in models_to_try:
        url = get_gemini_url(model)
        try:
            response = requests.post(url, json=payload, timeout=15)
            data = response.json()

            if "candidates" in data and data["candidates"]:
                if model != GEMINI_MODEL:
                    print(f"[Gemini] Used fallback model: {model}")
                return data["candidates"][0]["content"]["parts"][0]["text"]

            # Any error - immediately try next model
            if "error" in data:
                print(f"[Gemini] {model}: {data['error'].get('message', 'error')[:80]}")
                continue

        except Exception as e:
            print(f"[Gemini] {model} error: {e}")
            continue

    print("[Gemini] All models failed")
    return "Pasensya na Boss, medyo busy ang AI ngayon. Try mo ulit in a few seconds! 🙏"


# ============================================================
# TELEGRAM API FUNCTIONS
# ============================================================

def send_message(text, chat_id=None, parse_mode="Markdown"):
    """Send a text message via Telegram."""
    if chat_id is None:
        chat_id = TELEGRAM_CHAT_ID

    messages = split_message(text, MAX_MSG_LENGTH)
    results = []

    for msg in messages:
        url = f"{TELEGRAM_API_URL}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": parse_mode
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            result = response.json()
            if not result.get("ok"):
                # Retry without parse_mode if markdown fails
                payload.pop("parse_mode", None)
                response = requests.post(url, json=payload, timeout=10)
                result = response.json()
            results.append(result)
        except Exception as e:
            results.append({"ok": False, "error": str(e)})
        time.sleep(0.3)

    return results


def split_message(text, max_length=4096):
    """Split a long message into chunks."""
    if len(text) <= max_length:
        return [text]

    messages = []
    lines = text.split("\n")
    current = ""

    for line in lines:
        if len(current) + len(line) + 1 > max_length:
            if current:
                messages.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line

    if current:
        messages.append(current)

    return messages


def get_updates(offset=None):
    """Get new messages sent to the bot."""
    url = f"{TELEGRAM_API_URL}/getUpdates"
    params = {"timeout": 30, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset

    try:
        response = requests.get(url, params=params, timeout=35)
        data = response.json()
        return data.get("result", [])
    except Exception as e:
        print(f"[Telegram] Error getting updates: {e}")
        return []


def register_bot_commands():
    """Register Telegram slash commands so typing `/` shows the command list."""
    url = f"{TELEGRAM_API_URL}/setMyCommands"
    payload = {"commands": TELEGRAM_COMMANDS}
    try:
        response = requests.post(url, json=payload, timeout=15)
        data = response.json()
        if not data.get("ok"):
            logger.warning("Telegram setMyCommands failed: %s", data)
            return False
        logger.info("Telegram slash commands registered: %s", len(TELEGRAM_COMMANDS))
        return True
    except Exception:
        logger.exception("Telegram setMyCommands request failed")
        return False


def clean_markdown_for_telegram(text):
    """Convert standard markdown to Telegram-compatible format."""
    lines = text.split("\n")
    cleaned = []

    for line in lines:
        if line.strip().startswith("|---"):
            continue
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if cells:
                cleaned.append("  ".join(cells))
        elif line.startswith("### "):
            cleaned.append(f"\n*{line[4:]}*")
        elif line.startswith("## "):
            cleaned.append(f"\n*{line[3:]}*")
        elif line.startswith("# "):
            cleaned.append(f"*{line[2:]}*")
        elif line.startswith("> "):
            cleaned.append(f"💬 _{line[2:]}_")
        else:
            cleaned.append(line)

    return "\n".join(cleaned)


# ============================================================
# REPORT FUNCTIONS
# ============================================================

def send_report(report_markdown):
    """Send the Facebook Page report via Telegram."""
    header = f"📊 *Facebook Page Report*\n🕐 {datetime.now(PHT).strftime('%Y-%m-%d %H:%M')} PHT\n\n"
    full_message = header + clean_markdown_for_telegram(report_markdown)
    return send_message(full_message)


def send_suggested_replies_summary(suggested_replies):
    """Send formatted summary of suggested replies for approval."""
    if not suggested_replies:
        send_message("✅ Walang suggested replies sa period na ito.")
        return

    msg = f"📝 *Suggested Replies ({len(suggested_replies)} pending)*\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"

    for i, s in enumerate(suggested_replies, 1):
        msg += f"*Reply #{i}*\n"
        msg += f"👤 From: {s['comment_from']}\n"
        msg += f"💬 Comment: \"{s['comment_message'][:80]}\"\n"
        msg += f"📌 Post: {s['post_preview'][:50]}...\n"
        msg += f"🔑 Keyword: `{s['keyword_matched']}`\n"
        msg += f"✉️ Reply: \"{s['suggested_reply'][:80]}\"\n\n"

    msg += "━━━━━━━━━━━━━━━━━━\n"
    msg += "*Commands:*\n"
    msg += "• /approve\\_all - Send all replies\n"
    msg += "• /approve 1 3 5 - Send specific replies\n"
    msg += "• /skip\\_all - Discard all\n"
    msg += "• /skip 2 4 - Skip specific, approve rest\n"

    send_message(msg)


def send_approval_results(results):
    """Send the results of approved/skipped replies."""
    msg = "📤 *Reply Approval Results*\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"

    sent_count = skipped_count = error_count = 0
    for r in results:
        num = r["reply_num"]
        status = r["status"]
        if "sent" in status:
            msg += f"✅ Reply #{num}: Sent!\n"
            sent_count += 1
        elif "skip" in status:
            msg += f"⏭️ Reply #{num}: Skipped\n"
            skipped_count += 1
        elif "error" in status:
            msg += f"❌ Reply #{num}: Error\n"
            error_count += 1

    msg += f"\n📊 Total: {sent_count} sent, {skipped_count} skipped, {error_count} errors"
    send_message(msg)


# ============================================================
# COMMAND HANDLING
# ============================================================

def send_help():
    """Send help/command list."""
    msg = "🤖 *KarlC AI Buddy*\n"
    msg += f"📄 Page: {PAGE_NAME}\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    msg += "*Commands:*\n\n"
    msg += "📊 /report - Generate report now\n"
    msg += "📝 /pending - View pending replies\n"
    msg += "✅ /approve\\_all - Approve all pending replies\n"
    msg += "✅ /approve 1 3 5 - Approve specific replies\n"
    msg += "⏭️ /skip\\_all - Skip all pending replies\n"
    msg += "⏭️ /skip 2 4 - Skip specific replies\n"
    msg += "🔑 /keywords - View auto-reply keywords\n"
    msg += "📡 /status - Check agent status\n"
    msg += "🎫 /tickets - View pending student tickets\n"
    msg += "✅ /done 1 - Mark ticket #1 as resolved\n"
    msg += "✅ /done 1 2 3 - Mark multiple tickets as done\n"
    msg += "✅ /done all - Mark all pending tickets as done\n"
    msg += "📲 /follow 12 - SMS follow-up using saved ticket name/number\n"
    msg += "📲 /follow 12 | Juan Dela Cruz | 09171234567 - override saved details\n"
    msg += "📬 /support - View recent emails sent to support inbox\n"
    msg += "📊 /enrollment - Run enrollment comparison now\n"
    msg += "📚 /students - View enrolled students grouped by course\n"
    msg += "📚 /students hybrid - Filter enrolled students by course keyword\n"
    msg += "📄 /systeme\\_sync - Refresh student baseline from Google Sheet + Xendit\n"
    msg += "📇 /systeme\\_add 12 - Create a Systeme contact from a ticket\n"
    msg += "📇 /systeme\\_add juan@example.com | Juan Dela Cruz | 09171234567\n"
    msg += "🎓 /systeme\\_enroll 12 - Create/add contact then assign course tag from a ticket\n"
    msg += "🎓 /systeme\\_enroll juan@example.com | MikroTik Hybrid | Juan Dela Cruz | 09171234567\n"
    msg += "🗣️ /chat - Talk to AI Buddy (or just type normally!)\n"
    msg += "❓ /help - Show this help\n"
    msg += "\n━━━━━━━━━━━━━━━━━━\n"
    msg += "📅 *Auto Reports:*\n"
    msg += "  • 7AM - Full report + Enrollment check\n"
    msg += "  • 7PM - Full report\n"
    msg += "\n💬 *Or just chat with me naturally!*\n"
    msg += "Try: `May payment ba si Juan Dela Cruz?`, `Check payment for juan@example.com`, or `Hanapin mo yung payment ng 09171234567`"

    send_message(msg)


def send_status():
    """Send agent status."""
    from ticket_system import get_ticket_stats

    pending_file = os.path.join(DATA_DIR, "pending_replies.json")
    pending_count = 0
    if os.path.exists(pending_file):
        with open(pending_file) as f:
            pending_count = len(json.load(f))

    replied_file = os.path.join(DATA_DIR, "replied_comments.json")
    replied_count = 0
    if os.path.exists(replied_file):
        with open(replied_file) as f:
            replied_count = len(json.load(f))

    ticket_stats = get_ticket_stats()

    msg = "📡 *Agent Status*\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"🟢 Status: Active (24/7)\n"
    msg += f"📄 Page: {PAGE_NAME}\n"
    msg += f"📝 Pending Comment Replies: {pending_count}\n"
    msg += f"✅ Total Replied: {replied_count}\n"
    msg += f"🎫 Pending Tickets: {ticket_stats['pending']}\n"
    msg += f"   🟡 DM Verified: {ticket_stats['dm_verified']}\n"
    msg += f"   🔴 No Payment: {ticket_stats['dm_no_payment']}\n"
    msg += f"   🟠 Enrollment: {ticket_stats['enrollment_incomplete']}\n"
    msg += f"   📬 Support Email: {ticket_stats['support_email']}\n"
    msg += f"✅ Resolved Tickets: {ticket_stats['done']}\n"
    msg += f"🕐 Time: {datetime.now(PHT).strftime('%Y-%m-%d %H:%M:%S')} PHT\n"
    msg += f"\n📅 Next Reports: 7AM & 7PM daily"

    send_message(msg)


def send_keywords_list():
    """Send current keyword configuration."""
    from config import KEYWORD_REPLIES

    msg = "🔑 *Auto-Reply Keywords*\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"

    shown = set()
    for keyword, reply in KEYWORD_REPLIES.items():
        preview = reply[:50].replace("\n", " ")
        if preview not in shown:
            msg += f"• `{keyword}` → {preview}...\n"
            shown.add(preview)

    msg += "\n_Para mag-add/change, sabihin mo lang sa AI Buddy._"
    send_message(msg)


def send_tickets():
    """Send pending student tickets."""
    from ticket_system import format_pending_tickets_telegram, get_ticket_stats

    stats = get_ticket_stats()
    msg = format_pending_tickets_telegram()
    msg += f"\n\n📊 *Stats:* {stats['total']} total | {stats['pending']} pending | {stats['done']} resolved"

    send_message(msg)


def send_support_emails():
    """Show recent support inbox emails."""
    import gmail_imap
    from support_inbox import (
        filter_unresolved_support_emails,
        format_support_emails_telegram,
        get_recent_support_emails,
        sync_support_email_tickets,
    )

    if not gmail_imap.available():
        gmail_user_present = bool(os.environ.get("GMAIL_USER", "").strip())
        gmail_password_present = bool(os.environ.get("GMAIL_APP_PASSWORD", "").strip())
        send_message(
            "❌ Gmail support inbox is not configured.\n"
            f"• `GMAIL_USER` present: {'yes' if gmail_user_present else 'no'}\n"
            f"• `GMAIL_APP_PASSWORD` present: {'yes' if gmail_password_present else 'no'}\n"
            "Check Railway variables, then redeploy."
        )
        return

    emails = get_recent_support_emails(days_back=7, limit=10)
    if emails is None:
        send_message(
            "❌ Gmail support inbox login/search failed.\n"
            "Check `GMAIL_USER`, `GMAIL_APP_PASSWORD`, then redeploy Railway."
        )
        return

    emails, _created_tickets = sync_support_email_tickets(emails)
    actionable_emails = filter_unresolved_support_emails(emails)
    send_message(format_support_emails_telegram(actionable_emails))


def send_systeme_students(course_query=""):
    """Show enrolled students grouped by course from the local Systeme store."""
    from systeme_students import format_course_enrollment_summary

    send_message(format_course_enrollment_summary(course_query=course_query))


def _format_systeme_backfill_result(result):
    """Format Systeme backfill results for Telegram."""
    if not result.get("ok"):
        return (
            "❌ Systeme API backfill failed.\n"
            f"{result.get('message', 'Unknown error.')}\n\n"
            "Add `SYSTEME_API_KEY` in Railway, redeploy, then try `/systeme_sync` again."
        )

    msg = "📥 *Systeme API Backfill Complete*\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"👥 Raw contacts fetched: {result.get('contacts_scanned', 0)}\n"
    msg += f"📚 Courses scanned via API: {result.get('courses_scanned', 0)}\n"
    msg += f"🎓 Raw enrollments fetched: {result.get('enrollments_scanned', 0)}\n"
    msg += f"🔗 Enrollments linked: {result.get('enrollments_linked', 0)}\n"
    if result.get("student_snapshots", 0):
        msg += f"🧾 Unique student emails merged: {result.get('student_snapshots', 0)}\n"
    if result.get("contacts_with_course_tags", 0):
        msg += f"🏷️ Unique contacts with paid tags: {result.get('contacts_with_course_tags', 0)}\n"
    if result.get("bundle_contacts_with_course_tags", 0):
        msg += f"📦 Unique contacts with bundle tags: {result.get('bundle_contacts_with_course_tags', 0)}\n"
    msg += f"✅ Unique students imported/updated: {result.get('students_imported', 0)}\n"
    if result.get("students_without_recognized_courses", 0):
        msg += f"❓ Unique contacts without recognized course mapping: {result.get('students_without_recognized_courses', 0)}\n"
    if result.get("unknown_paid_tags"):
        msg += f"🔎 Unknown paid-like tags seen: {', '.join(result.get('unknown_paid_tags', [])[:5])}\n"
    if result.get("hit_contact_page_cap") or result.get("hit_enrollment_page_cap"):
        msg += "\n⚠️ Raw fetch hit the current safety cap, so these raw counts are not the real total yet.\n"
    msg += "\nℹ️ Bundle enrollments are inferred from recognized bundle tags, not from the course API count.\n"
    if result.get("skipped_without_email", 0):
        msg += f"⚠️ Skipped without email: {result.get('skipped_without_email', 0)}\n"
    msg += "\nTry `/students` or ask me about a student/course right away."
    return msg


def _run_systeme_backfill_job():
    """Run Systeme backfill in the background so Telegram stays responsive."""
    global _SYSTEME_BACKFILL_RUNNING

    try:
        logger.info("Starting Systeme API backfill job in background thread")
        from systeme_backfill import run_systeme_backfill

        result = run_systeme_backfill()
        logger.info(
            "Systeme API backfill finished: ok=%s contacts=%s enrollments=%s tagged_contacts=%s students_imported=%s",
            result.get("ok"),
            result.get("contacts_scanned", 0),
            result.get("enrollments_scanned", 0),
            result.get("contacts_with_course_tags", 0),
            result.get("students_imported", 0),
        )
        send_message(_format_systeme_backfill_result(result))
    except Exception as e:
        logger.exception("Systeme API backfill crashed")
        send_message(
            "❌ Systeme API backfill crashed.\n"
            f"{str(e)[:250]}"
        )
    finally:
        with _SYSTEME_BACKFILL_STATE_LOCK:
            _SYSTEME_BACKFILL_RUNNING = False
        logger.info("Systeme API backfill background flag cleared")


def send_systeme_backfill():
    """Start a one-time Systeme Public API backfill in the background."""
    global _SYSTEME_BACKFILL_RUNNING

    with _SYSTEME_BACKFILL_STATE_LOCK:
        if _SYSTEME_BACKFILL_RUNNING:
            logger.info("Ignored duplicate Systeme API backfill request because one is already running")
            send_message("⏳ Systeme API backfill is already running, sandali lang Boss!")
            return False
        _SYSTEME_BACKFILL_RUNNING = True

    thread = threading.Thread(target=_run_systeme_backfill_job, daemon=True)
    thread.start()
    logger.info("Queued Systeme API backfill background thread")
    return True


def send_systeme_sheet_sync():
    """Import the configured Systeme student summary CSV into the local store."""
    import google_sheet_sync
    import systeme_sheet_import

    result = systeme_sheet_import.run_configured_import()
    if not result.get("ok"):
        send_message(
            "❌ Systeme student sheet sync failed.\n"
            f"{result.get('message', 'Unknown error.')}"
        )
        return False

    msg = "📄 *Systeme Student Sheet Sync Complete*\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"📥 Source: {result.get('source', 'unknown')}\n"
    msg += f"🧾 Rows scanned: {result.get('rows_scanned', 0)}\n"
    msg += f"✅ Students imported/updated: {result.get('students_imported', 0)}\n"
    if result.get("xendit_matches", 0):
        msg += f"💳 Matched with Xendit payer info: {result.get('xendit_matches', 0)}\n"
    if result.get("skipped_without_email", 0):
        msg += f"⚠️ Skipped without email: {result.get('skipped_without_email', 0)}\n"

    if google_sheet_sync.available():
        writeback = google_sheet_sync.sync_all_students()
        if writeback.get("ok"):
            msg += f"📝 Google Sheet rows updated: {writeback.get('updated', 0)}\n"
            if writeback.get("appended", 0):
                msg += f"➕ Google Sheet rows appended: {writeback.get('appended', 0)}\n"
            if writeback.get("duplicates_removed", 0):
                msg += f"🧹 Duplicate sheet rows removed: {writeback.get('duplicates_removed', 0)}\n"
        else:
            msg += "⚠️ Google Sheet write-back skipped or had errors.\n"
            if writeback.get("errors"):
                msg += f"   {writeback.get('errors', ['Unknown error'])[0][:120]}\n"

    msg += "\nThe local Systeme student store is refreshed and ready for `/students`, lookups, and enrollment confirmation."
    send_message(msg)
    return True


def _parse_systeme_add_command(text):
    """Parse `/systeme_add 12` or `/systeme_add email | name | phone`."""
    raw = text.strip().split(maxsplit=1)
    raw = raw[1].strip() if len(raw) > 1 else ""
    if not raw:
        raise ValueError("Usage: /systeme_add 12 OR /systeme_add juan@example.com | Juan Dela Cruz | 09171234567")

    parts = [part.strip() for part in raw.split("|")]
    if len(parts) == 1 and parts[0].isdigit():
        return {"ticket_id": int(parts[0]), "email": "", "name": "", "phone_number": ""}
    if len(parts) == 1:
        return {"ticket_id": None, "email": parts[0], "name": "", "phone_number": ""}
    if len(parts) == 2:
        return {"ticket_id": None, "email": parts[0], "name": parts[1], "phone_number": ""}
    if len(parts) == 3:
        return {"ticket_id": None, "email": parts[0], "name": parts[1], "phone_number": parts[2]}
    raise ValueError("Usage: /systeme_add 12 OR /systeme_add juan@example.com | Juan Dela Cruz | 09171234567")


def _parse_systeme_enroll_command(text):
    """Parse `/systeme_enroll 12` or `/systeme_enroll email | course | name | phone`."""
    raw = text.strip().split(maxsplit=1)
    raw = raw[1].strip() if len(raw) > 1 else ""
    if not raw:
        raise ValueError("Usage: /systeme_enroll 12 OR /systeme_enroll juan@example.com | MikroTik Hybrid | Juan Dela Cruz | 09171234567")

    parts = [part.strip() for part in raw.split("|")]
    if len(parts) == 1 and parts[0].isdigit():
        return {
            "ticket_id": int(parts[0]),
            "email": "",
            "course_query": "",
            "name": "",
            "phone_number": "",
        }
    if len(parts) == 2:
        return {
            "ticket_id": None,
            "email": parts[0],
            "course_query": parts[1],
            "name": "",
            "phone_number": "",
        }
    if len(parts) == 3:
        return {
            "ticket_id": None,
            "email": parts[0],
            "course_query": parts[1],
            "name": parts[2],
            "phone_number": "",
        }
    if len(parts) == 4:
        return {
            "ticket_id": None,
            "email": parts[0],
            "course_query": parts[1],
            "name": parts[2],
            "phone_number": parts[3],
        }
    raise ValueError("Usage: /systeme_enroll 12 OR /systeme_enroll juan@example.com | MikroTik Hybrid | Juan Dela Cruz | 09171234567")


def send_systeme_manual_contact(ticket_id=None, email="", name="", phone_number=""):
    """Create a contact in Systeme manually or from an enrollment ticket."""
    from systeme_manual import add_contact

    result = add_contact(
        email=email,
        name=name,
        phone_number=phone_number,
        ticket_id=ticket_id,
    )
    contact = result["contact"]
    msg = "📇 *Systeme Contact Ready*\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    if result.get("ticket_id"):
        msg += f"🎫 From ticket #{result['ticket_id']}\n"
    msg += f"👤 {result.get('name') or result.get('email')}\n"
    msg += f"📧 {result.get('email')}\n"
    if result.get("phone_number"):
        msg += f"📱 {result.get('phone_number')}\n"
    if contact.get("id") not in (None, ""):
        msg += f"🆔 Contact ID: {contact.get('id')}\n"
    msg += "\nContact is now saved in Systeme and in the bot's local student memory."
    send_message(msg)


def send_systeme_manual_enrollment(ticket_id=None, email="", course_query="", name="", phone_number=""):
    """Create contact if needed, assign enrollment tag, and resolve ticket if applicable."""
    from systeme_manual import enroll_student

    result = enroll_student(
        email=email,
        course_query=course_query,
        name=name,
        phone_number=phone_number,
        ticket_id=ticket_id,
        resolve_ticket_on_success=True,
    )
    contact = result["contact"]
    course = result["course"]
    tag = result.get("tag", {})
    msg = "🎓 *Systeme Enrollment Triggered*\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    if result.get("ticket_id"):
        msg += f"🎫 Ticket #{result['ticket_id']}\n"
    msg += f"👤 {result.get('name') or result.get('email')}\n"
    msg += f"📧 {result.get('email')}\n"
    msg += f"📚 {course.get('name') or course_query}\n"
    if tag.get("name"):
        msg += f"🏷️ Tag: {tag.get('name')}\n"
    if result.get("phone_number"):
        msg += f"📱 {result.get('phone_number')}\n"
    if contact.get("id") not in (None, ""):
        msg += f"🆔 Contact ID: {contact.get('id')}\n"
    if tag.get("id") not in (None, ""):
        msg += f"🆔 Tag ID: {tag.get('id')}\n"
    msg += "\n✅ Contact was tagged in Systeme."
    msg += "\n⚙️ Your Systeme automation should do the actual enrollment next."
    if result.get("ticket_id"):
        msg += "\n✅ Related pending ticket was marked resolved."
    send_message(msg)


def resolve_tickets(ticket_ids):
    """Resolve one or more tickets."""
    from ticket_system import resolve_ticket

    msg = "🎫 *Ticket Resolution*\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"

    for tid in ticket_ids:
        ticket, status = resolve_ticket(tid)
        if status == "resolved":
            msg += f"✅ Ticket #{tid} - RESOLVED\n"
            msg += f"   👤 {ticket['student_name']} ({ticket['student_email']})\n"
            if ticket.get('course_title'):
                msg += f"   📚 {ticket['course_title'][:40]}\n"
            msg += "\n"
        elif status == "already_done":
            msg += f"ℹ️ Ticket #{tid} - Already resolved\n\n"
        elif status == "not_found":
            msg += f"❌ Ticket #{tid} - Not found\n\n"

    msg += "━━━━━━━━━━━━━━━━━━"
    send_message(msg)


def resolve_all_tickets():
    """Resolve every pending ticket."""
    from ticket_system import resolve_all_pending_tickets

    resolved = resolve_all_pending_tickets()
    if not resolved:
        send_message("✅ Walang pending tickets na kailangan i-resolve.")
        return

    msg = "🎫 *Bulk Ticket Resolution*\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"✅ Resolved {len(resolved)} pending ticket(s)\n\n"

    sample = resolved[:10]
    for ticket in sample:
        msg += f"• #{ticket['id']} - {ticket['student_name']} ({ticket['type']})\n"

    remaining = len(resolved) - len(sample)
    if remaining > 0:
        msg += f"\n...and {remaining} more."

    send_message(msg)


def _parse_follow_command(text):
    """Parse `/follow ticket_id` or `/follow ticket_id | name | phone`."""
    raw = text.strip()[len("/follow"):].strip()
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) == 1:
        ticket_id_text = parts[0]
        contact_name = ""
        phone_number = ""
    elif len(parts) == 3:
        ticket_id_text, contact_name, phone_number = parts
    else:
        raise ValueError("Usage: /follow 12 OR /follow 12 | Juan Dela Cruz | 09171234567")

    if not ticket_id_text.isdigit():
        raise ValueError("Ticket number should be a whole number like 12.")

    return int(ticket_id_text), contact_name, phone_number


def send_ticket_followup(ticket_id, contact_name, phone_number):
    """Send an SMS follow-up for a specific ticket."""
    if not SEMAPHORE_ENABLED:
        send_message("❌ Semaphore SMS is not configured yet. Add `SEMAPHORE_API_KEY` in Railway first.")
        return

    from sms_followup import send_followup_sms
    from ticket_system import get_ticket, record_followup_attempt

    ticket = get_ticket(ticket_id)
    if not ticket:
        send_message(f"❌ Ticket #{ticket_id} not found.")
        return

    contact_name = contact_name or str(ticket.get("student_name") or "").strip()
    phone_number = phone_number or str(ticket.get("phone_number") or "").strip()
    if not contact_name or not phone_number:
        send_message(
            "❌ Ticket has no saved name/phone yet.\n"
            "Use `/follow 12 | Juan Dela Cruz | 09171234567` once, "
            "or enrich the ticket via Xendit/support sync first."
        )
        return

    try:
        result = send_followup_sms(ticket, contact_name, phone_number)
        record_followup_attempt(
            ticket_id=ticket_id,
            contact_name=contact_name,
            phone_number=result["recipient"],
            message_text=result["message_text"],
            provider="semaphore",
            result_status=result["status"],
            provider_message_id=result["provider_message_id"],
            provider_response=result["provider_response"],
        )
    except Exception as e:
        send_message(f"❌ Follow-up failed: {str(e)[:200]}")
        return

    msg = "📲 *Follow-up Sent*\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"🎫 Ticket #{ticket_id}\n"
    msg += f"👤 {contact_name}\n"
    msg += f"📱 {result['recipient']}\n"
    msg += f"📡 Semaphore status: {result['status']}\n"
    if ticket.get("course_title"):
        msg += f"📚 {ticket['course_title'][:40]}\n"
    msg += "\n✉️ Message:\n"
    msg += result["message_text"]
    send_message(msg)


# ============================================================
# AI CHAT (GEMINI-POWERED)
# ============================================================

def _get_context_info():
    """Get current context info for the AI to use."""
    from ticket_system import get_ticket_stats, get_pending_tickets

    stats = get_ticket_stats()
    pending = get_pending_tickets()

    context = f"\n[Current Time: {datetime.now(PHT).strftime('%Y-%m-%d %H:%M:%S')} PHT]\n"
    context += (
        f"[Pending Tickets: {stats['pending']} "
        f"({stats['dm_verified']} DM verified, {stats['dm_no_payment']} no payment, "
        f"{stats['enrollment_incomplete']} enrollment, {stats['support_email']} support email)]\n"
    )
    context += f"[Resolved Tickets: {stats['done']}]\n"

    if pending:
        context += "\n[Recent Pending Tickets:]\n"
        for t in pending[:5]:
            context += f"  - Ticket #{t['id']}: {t['type']} | {t['student_name']} | {t['student_email']} | {t.get('course_title', 'N/A')}\n"

    pending_file = os.path.join(DATA_DIR, "pending_replies.json")
    if os.path.exists(pending_file):
        with open(pending_file) as f:
            pending_replies = json.load(f)
            if pending_replies:
                context += f"\n[Pending Comment Replies: {len(pending_replies)}]\n"

    return context


def chat_with_ai(user_message):
    """Process a natural language message from Karl and respond with AI."""
    history = _load_conversation()
    context = _get_context_info()

    # Check if Karl is asking about real data (messages, comments, emails)
    from data_queries import build_data_context
    data_context = build_data_context(user_message)
    if data_context:
        context += data_context

    system_prompt = AI_BUDDY_SYSTEM_PROMPT + context

    ai_reply = call_gemini(history, system_prompt, user_message)

    # Save to conversation history
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": ai_reply})
    _save_conversation(history)

    return ai_reply


# ============================================================
# COMMAND PARSER & HANDLER
# ============================================================

def process_message(text):
    """Process incoming message - command or natural language."""
    text_lower = text.strip().lower()

    # === COMMANDS ===
    if text_lower in ["/start", "/help"]:
        send_help()
        return "help"

    if text_lower == "/status":
        send_status()
        return "status"

    if text_lower == "/report":
        send_message("⏳ Generating report... sandali lang Boss!")
        try:
            from fb_agent import run_agent
            run_agent(is_morning=False)
        except Exception as e:
            send_message(f"❌ Error generating report: {str(e)[:200]}")
        return "report"

    if text_lower == "/pending":
        pending_file = os.path.join(DATA_DIR, "pending_replies.json")
        if os.path.exists(pending_file):
            with open(pending_file) as f:
                pending = json.load(f)
            if pending:
                send_suggested_replies_summary(pending)
            else:
                send_message("✅ Walang pending replies ngayon Boss!")
        else:
            send_message("✅ Walang pending replies ngayon Boss!")
        return "pending"

    if text_lower in ["/approve_all", "/approveall", "/approve all"]:
        try:
            from fb_agent import approve_replies
            results = approve_replies("all")
            send_approval_results(results)
        except Exception as e:
            send_message(f"❌ Error: {str(e)[:200]}")
        return "approve_all"

    if text_lower in ["/skip_all", "/skipall", "/skip all"]:
        try:
            from fb_agent import approve_replies
            results = approve_replies("skip_all")
            send_approval_results(results)
        except Exception as e:
            send_message(f"❌ Error: {str(e)[:200]}")
        return "skip_all"

    # Match `/approve 1 2 3` but not `/approve_all` (handled above).
    # We split on whitespace and check the first token is exactly `/approve`.
    tokens = text_lower.split()
    if tokens and tokens[0] == "/approve":
        numbers = [int(p) for p in tokens[1:] if p.isdigit()]
        if numbers:
            try:
                from fb_agent import approve_replies
                results = approve_replies(numbers)
                send_approval_results(results)
            except Exception as e:
                send_message(f"❌ Error: {str(e)[:200]}")
        else:
            send_message("Usage: /approve 1 2 3")
        return "approve"

    if tokens and tokens[0] == "/skip":
        numbers = [int(p) for p in tokens[1:] if p.isdigit()]
        if numbers:
            try:
                from fb_agent import approve_replies
                results = approve_replies("skip", numbers)
                send_approval_results(results)
            except Exception as e:
                send_message(f"❌ Error: {str(e)[:200]}")
        else:
            send_message("Usage: /skip 1 2 3")
        return "skip"

    if text_lower == "/keywords":
        send_keywords_list()
        return "keywords"

    if text_lower == "/tickets":
        send_tickets()
        return "tickets"

    if text_lower == "/support":
        send_message("⏳ Checking support inbox... sandali lang Boss!")
        send_support_emails()
        return "support"

    if tokens and tokens[0] == "/students":
        course_query = text.strip()[len(tokens[0]):].strip()
        send_message("⏳ Checking stored Systeme students... sandali lang Boss!")
        send_systeme_students(course_query=course_query)
        return "students"

    if text_lower in [
        "/systeme_sync",
        "/systemesync",
        "/systeme_sheet_sync",
        "/systemesheetsync",
        "/sheet_sync",
        "/students_sheet_sync",
    ]:
        send_message("⏳ Syncing Systeme student sheet... sandali lang Boss!")
        send_systeme_sheet_sync()
        return "systeme_sync"

    if text_lower in ["/systeme_api_sync", "/systemeapisync", "/backfill_systeme", "/systeme_backfill"]:
        if send_systeme_backfill():
            send_message("⏳ Running Systeme API backfill... this may take a bit, sandali lang Boss!")
        return "systeme_api_sync"

    if tokens and tokens[0] in ["/systeme_add", "/systemeadd", "/contact_add", "/add_contact"]:
        try:
            parsed = _parse_systeme_add_command(text)
            send_message("⏳ Creating Systeme contact... sandali lang Boss!")
            send_systeme_manual_contact(**parsed)
        except Exception as e:
            send_message(f"❌ {str(e)[:250]}")
        return "systeme_add"

    if tokens and tokens[0] in ["/systeme_enroll", "/systemeenroll", "/enroll_student", "/manual_enroll"]:
        try:
            parsed = _parse_systeme_enroll_command(text)
            send_message("⏳ Enrolling student in Systeme... sandali lang Boss!")
            send_systeme_manual_enrollment(**parsed)
        except Exception as e:
            send_message(f"❌ {str(e)[:250]}")
        return "systeme_enroll"

    # /enrollment command OR natural-language ask ("check enrollments",
    # "enrollment status", "paid but not enrolled", etc.) — both run the check
    # on demand so Karl can get data anytime, not just on the 7AM/7PM reports.
    _enrollment_phrases = (
        "check enrollment", "check enrolment",
        "enrollment check", "enrolment check",
        "enrollment status", "enrolment status",
        "enrollment report", "enrolment report",
        "run enrollment", "run enrolment",
        "compare payment", "compare enrollment", "compare enrolment",
        "paid but not enrolled", "paid not enrolled",
        "who paid but",
    )
    if text_lower == "/enrollment" or any(p in text_lower for p in _enrollment_phrases):
        send_message("⏳ Running enrollment comparison... sandali lang Boss!")
        try:
            from fb_agent import run_enrollment_check
            from enrollment_checker import format_comparison_telegram
            report = run_enrollment_check()
            if report:
                send_message(format_comparison_telegram(report))
            else:
                send_message("❌ Error running enrollment check.")
        except Exception as e:
            send_message(f"❌ Error: {str(e)[:200]}")
        return "enrollment"

    if tokens and tokens[0] == "/done":
        if len(tokens) >= 2 and tokens[1] == "all":
            resolve_all_tickets()
            return "done_all"
        numbers = [int(p) for p in tokens[1:] if p.isdigit()]
        if numbers:
            resolve_tickets(numbers)
        else:
            send_message("Usage: /done 1 or /done 1 2 3 or /done all")
        return "done"

    if text_lower.startswith("/follow"):
        try:
            ticket_id, contact_name, phone_number = _parse_follow_command(text)
            send_ticket_followup(ticket_id, contact_name, phone_number)
        except Exception as e:
            send_message(f"❌ {str(e)[:200]}")
        return "follow"

    # === NATURAL LANGUAGE (AI CHAT) ===
    # If not a command, treat as AI chat
    ai_reply = chat_with_ai(text)
    send_message(ai_reply)
    return "ai_chat"


# ============================================================
# TELEGRAM LISTENER (24/7 POLLING)
# ============================================================

def start_listener():
    """Start the Telegram bot listener (long polling)."""
    print(f"[Telegram] Bot listener started at {datetime.now(PHT).strftime('%Y-%m-%d %H:%M:%S')} PHT")
    print(f"[Telegram] Listening for messages from chat_id: {TELEGRAM_CHAT_ID}")
    register_bot_commands()

    offset = None

    while True:
        try:
            updates = get_updates(offset)

            for update in updates:
                offset = update["update_id"] + 1

                # Process message
                message = update.get("message", {})
                chat_id = str(message.get("chat", {}).get("id", ""))
                text = message.get("text", "")

                if not text:
                    continue

                # Only respond to Karl
                if chat_id != str(TELEGRAM_CHAT_ID):
                    print(f"[Telegram] Ignoring message from unknown chat_id: {chat_id}")
                    continue

                print(f"[Telegram] Received: {text[:50]}...")

                try:
                    action = process_message(text)
                    print(f"[Telegram] Processed: {action}")
                except Exception as e:
                    print(f"[Telegram] Error processing message: {e}")
                    send_message(f"❌ Error Boss: {str(e)[:200]}")

        except Exception as e:
            print(f"[Telegram] Listener error: {e}")
            time.sleep(5)


def start_listener_thread():
    """Start the Telegram listener in a background thread."""
    thread = threading.Thread(target=start_listener, daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    print("Starting Telegram Bot Listener...")
    start_listener()
