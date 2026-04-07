"""
Webhook Server for Facebook Messenger
Handles incoming DMs and forwards to AI Buddy for processing.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify

from config import WEBHOOK_VERIFY_TOKEN, DATA_DIR

PHT = timezone(timedelta(hours=8))

app = Flask(__name__)

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

MESSAGES_FILE = os.path.join(DATA_DIR, "messages.json")


def _load_messages():
    """Load stored messages."""
    if os.path.exists(MESSAGES_FILE):
        with open(MESSAGES_FILE) as f:
            return json.load(f)
    return []


def _save_messages(messages):
    """Save messages."""
    with open(MESSAGES_FILE, "w") as f:
        json.dump(messages, f, indent=2, ensure_ascii=False)


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """Facebook webhook verification."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
        print(f"[Webhook] Verified successfully!")
        return challenge, 200
    else:
        print(f"[Webhook] Verification failed. Token: {token}")
        return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """Handle incoming webhook events from Facebook."""
    data = request.get_json()

    if not data or data.get("object") != "page":
        return "Not a page event", 404

    for entry in data.get("entry", []):
        for messaging_event in entry.get("messaging", []):
            sender_id = messaging_event.get("sender", {}).get("id", "")
            recipient_id = messaging_event.get("recipient", {}).get("id", "")
            message = messaging_event.get("message", {})
            text = message.get("text", "")

            if not text or not sender_id:
                continue

            # Skip messages from our own page
            from config import PAGE_ID
            if sender_id == PAGE_ID:
                continue

            timestamp = datetime.now(PHT)
            print(f"[Webhook] Message from {sender_id}: {text[:50]}...")

            # Store message
            messages = _load_messages()
            msg_record = {
                "sender_id": sender_id,
                "sender_name": "",
                "text": text,
                "timestamp": timestamp.isoformat(),
                "recipient_id": recipient_id,
            }

            # Get sender name
            try:
                from ai_buddy import get_sender_name
                msg_record["sender_name"] = get_sender_name(sender_id)
            except:
                msg_record["sender_name"] = "Unknown"

            messages.append(msg_record)
            # Keep only last 500 messages
            messages = messages[-500:]
            _save_messages(messages)

            # Process with AI Buddy
            try:
                from ai_buddy import handle_incoming_dm
                handle_incoming_dm(sender_id, text, msg_record["sender_name"])
            except Exception as e:
                print(f"[Webhook] AI Buddy error: {e}")
                # Still send Telegram notification even if AI Buddy fails
                try:
                    from telegram_bot import send_message
                    notif = f"💬 *New Facebook DM*\n"
                    notif += f"👤 From: {msg_record['sender_name']}\n"
                    notif += f"📝 Message: {text[:200]}\n"
                    notif += f"⚠️ AI Buddy error: {str(e)[:100]}"
                    send_message(notif)
                except:
                    pass

    return "OK", 200


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "running",
        "time": datetime.now(PHT).isoformat(),
        "ai_buddy": "enabled",
        "messages_stored": len(_load_messages()),
    })


@app.route("/messages", methods=["GET"])
def get_messages():
    """Get stored messages (for debugging)."""
    messages = _load_messages()
    return jsonify({"messages": messages[-20:], "total": len(messages)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
