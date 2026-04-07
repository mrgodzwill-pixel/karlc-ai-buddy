"""
Telegram Bot for Karl C AI Buddy
- Gemini-powered natural language chat
- Command handling
- Report sending
- 24/7 listener
"""

import json
import os
import re
import time
import requests
import threading
from datetime import datetime, timedelta, timezone

from config import (
    TELEGRAM_API_URL, TELEGRAM_CHAT_ID, TELEGRAM_BOT_TOKEN,
    PAGE_NAME, GEMINI_API_KEY, GEMINI_MODEL, GEMINI_API_URL,
    GEMINI_FALLBACK_MODELS, get_gemini_url,
    AI_BUDDY_SYSTEM_PROMPT, DATA_DIR, COURSES
)

PHT = timezone(timedelta(hours=8))
MAX_MSG_LENGTH = 4096

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
    msg += "📊 /enrollment - Run enrollment comparison now\n"
    msg += "🗣️ /chat - Talk to AI Buddy (or just type normally!)\n"
    msg += "❓ /help - Show this help\n"
    msg += "\n━━━━━━━━━━━━━━━━━━\n"
    msg += "📅 *Auto Reports:*\n"
    msg += "  • 7AM - Full report + Enrollment check\n"
    msg += "  • 7PM - Full report\n"
    msg += "\n💬 *Or just chat with me naturally!*"

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


# ============================================================
# AI CHAT (GEMINI-POWERED)
# ============================================================

def _get_context_info():
    """Get current context info for the AI to use."""
    from ticket_system import get_ticket_stats, get_pending_tickets

    stats = get_ticket_stats()
    pending = get_pending_tickets()

    context = f"\n[Current Time: {datetime.now(PHT).strftime('%Y-%m-%d %H:%M:%S')} PHT]\n"
    context += f"[Pending Tickets: {stats['pending']} ({stats['dm_verified']} DM verified, {stats['dm_no_payment']} no payment, {stats['enrollment_incomplete']} enrollment)]\n"
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

    if text_lower.startswith("/approve"):
        parts = text_lower.replace("/approve", "").strip().split()
        numbers = [int(p) for p in parts if p.isdigit()]
        if numbers:
            try:
                from fb_agent import approve_replies
                results = approve_replies(numbers)
                send_approval_results(results)
            except Exception as e:
                send_message(f"❌ Error: {str(e)[:200]}")
        return "approve"

    if text_lower.startswith("/skip"):
        parts = text_lower.replace("/skip", "").strip().split()
        numbers = [int(p) for p in parts if p.isdigit()]
        if numbers:
            try:
                from fb_agent import approve_replies
                results = approve_replies("skip", numbers)
                send_approval_results(results)
            except Exception as e:
                send_message(f"❌ Error: {str(e)[:200]}")
        return "skip"

    if text_lower == "/keywords":
        send_keywords_list()
        return "keywords"

    if text_lower == "/tickets":
        send_tickets()
        return "tickets"

    if text_lower == "/enrollment":
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

    if text_lower.startswith("/done"):
        parts = text_lower.replace("/done", "").strip().split()
        numbers = [int(p) for p in parts if p.isdigit()]
        if numbers:
            resolve_tickets(numbers)
        else:
            send_message("Usage: /done 1 or /done 1 2 3")
        return "done"

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
