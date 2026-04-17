"""
Webhook Server for Facebook Messenger
Handles incoming DMs and forwards to AI Buddy for processing.

Security:
- Validates FB_APP_SECRET HMAC signature on every POST
- Deduplicates by Facebook message ID
- Processes messages in a background thread so Facebook gets a fast 200 OK
"""

import hashlib
import hmac
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, abort

from config import WEBHOOK_VERIFY_TOKEN, FB_APP_SECRET, DATA_DIR, PAGE_ID
from storage import file_lock

PHT = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)

app = Flask(__name__)

os.makedirs(DATA_DIR, exist_ok=True)

MESSAGES_FILE = os.path.join(DATA_DIR, "messages.json")
PROCESSED_MIDS_FILE = os.path.join(DATA_DIR, "processed_mids.json")
# Keep track of the last N processed message IDs to dedupe FB retries
MAX_PROCESSED_MIDS = 1000

# Optional shared secret to protect debug endpoints (/messages, /health details)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

# Set SKIP_WEBHOOK_SIGNATURE=true in Railway to bypass HMAC check temporarily.
# Useful for debugging. Set back to false once DMs are confirmed working.
SKIP_WEBHOOK_SIGNATURE = os.environ.get("SKIP_WEBHOOK_SIGNATURE", "false").lower() == "true"


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        logger.exception("Failed to load %s", path)
        return default


def _save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        logger.exception("Failed to save %s", path)


def _verify_fb_signature(request) -> bool:
    """Verify X-Hub-Signature-256 against FB_APP_SECRET."""
    if not FB_APP_SECRET:
        # If app secret isn't configured, refuse to accept webhooks.
        logger.error("FB_APP_SECRET not configured - rejecting webhook")
        return False

    signature_header = request.headers.get("X-Hub-Signature-256", "")
    if not signature_header.startswith("sha256="):
        return False

    received_sig = signature_header.split("=", 1)[1]
    body = request.get_data()  # raw bytes
    expected_sig = hmac.new(
        FB_APP_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(received_sig, expected_sig)


def _already_processed(mid: str) -> bool:
    """Check if a Facebook message ID was already handled."""
    if not mid:
        return False
    with file_lock(PROCESSED_MIDS_FILE):
        mids = _load_json(PROCESSED_MIDS_FILE, [])
        return mid in mids


def _mark_processed(mid: str) -> None:
    if not mid:
        return
    with file_lock(PROCESSED_MIDS_FILE):
        mids = _load_json(PROCESSED_MIDS_FILE, [])
        if mid in mids:
            return
        mids.append(mid)
        if len(mids) > MAX_PROCESSED_MIDS:
            mids = mids[-MAX_PROCESSED_MIDS:]
        _save_json(PROCESSED_MIDS_FILE, mids)


def _handle_message_async(sender_id: str, text: str, sender_name: str, mid: str):
    """Background processing so we can return 200 to Facebook within its 20s window."""
    try:
        # Store message
        with file_lock(MESSAGES_FILE):
            messages = _load_json(MESSAGES_FILE, [])
            messages.append({
                "sender_id": sender_id,
                "sender_name": sender_name,
                "text": text,
                "timestamp": datetime.now(PHT).isoformat(),
                "mid": mid,
            })
            messages = messages[-500:]
            _save_json(MESSAGES_FILE, messages)

        from ai_buddy import handle_incoming_dm
        handle_incoming_dm(sender_id, text, sender_name)
    except Exception as e:
        logger.exception("AI Buddy error")
        try:
            from telegram_bot import send_message
            send_message(
                f"💬 *New Facebook DM (handler crashed)*\n"
                f"👤 From: {sender_name}\n"
                f"📝 Message: {text[:200]}\n"
                f"⚠️ Error: {str(e)[:100]}"
            )
        except Exception:
            logger.exception("Failed to notify Telegram of handler error")


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """Facebook webhook verification handshake."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN and WEBHOOK_VERIFY_TOKEN:
        logger.info("Webhook verified")
        return challenge, 200
    logger.warning("Webhook verification failed")
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """Handle incoming webhook events from Facebook."""
    if SKIP_WEBHOOK_SIGNATURE:
        logger.warning("SKIP_WEBHOOK_SIGNATURE=true — skipping signature check (debug mode)")
    elif not _verify_fb_signature(request):
        logger.warning("Invalid FB webhook signature - rejecting (set SKIP_WEBHOOK_SIGNATURE=true to bypass for debugging)")
        abort(403)

    data = request.get_json(silent=True)
    if not data or data.get("object") != "page":
        return "Not a page event", 404

    # Collect work, then return 200 quickly. Heavy lifting runs in a thread.
    jobs = []
    for entry in data.get("entry", []):
        for messaging_event in entry.get("messaging", []):
            sender_id = messaging_event.get("sender", {}).get("id", "")
            message = messaging_event.get("message", {})
            text = message.get("text", "")
            mid = message.get("mid", "")

            if not text or not sender_id:
                continue
            if sender_id == PAGE_ID:
                continue  # our own messages echoed back
            if _already_processed(mid):
                logger.info("Skipping already-processed mid=%s", mid)
                continue

            _mark_processed(mid)

            # Pre-resolve sender name (FB Graph is fast; do it before queueing)
            try:
                from ai_buddy import get_sender_name
                sender_name = get_sender_name(sender_id)
            except Exception:
                sender_name = "Unknown"

            logger.info("Queuing DM from %s (%s): %s", sender_name, sender_id, text[:50])
            jobs.append((sender_id, text, sender_name, mid))

    for sender_id, text, sender_name, mid in jobs:
        t = threading.Thread(
            target=_handle_message_async,
            args=(sender_id, text, sender_name, mid),
            daemon=True,
        )
        t.start()

    return "OK", 200


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint - public, no sensitive info."""
    return jsonify({
        "status": "running",
        "time": datetime.now(PHT).isoformat(),
    })


@app.route("/messages", methods=["GET"])
def get_messages():
    """Debug endpoint - requires ADMIN_TOKEN query param."""
    if not ADMIN_TOKEN:
        abort(404)
    token = request.args.get("token", "")
    if not hmac.compare_digest(token, ADMIN_TOKEN):
        abort(403)
    messages = _load_json(MESSAGES_FILE, [])
    return jsonify({"messages": messages[-20:], "total": len(messages)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
