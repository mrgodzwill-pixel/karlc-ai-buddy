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

from config import (
    WEBHOOK_VERIFY_TOKEN,
    FB_APP_SECRET,
    DATA_DIR,
    PAGE_ID,
    SYSTEME_AUTOMATION_TOKEN,
    SYSTEME_WEBHOOK_SECRET,
    XENDIT_INVOICE_WEBHOOK_TOKEN,
    XENDIT_PAYMENT_WEBHOOK_TOKEN,
)
from storage import file_lock

PHT = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)

app = Flask(__name__)

os.makedirs(DATA_DIR, exist_ok=True)

MESSAGES_FILE = os.path.join(DATA_DIR, "messages.json")
PROCESSED_MIDS_FILE = os.path.join(DATA_DIR, "processed_mids.json")
PROCESSED_XENDIT_WEBHOOKS_FILE = os.path.join(DATA_DIR, "processed_xendit_webhooks.json")
PROCESSED_SYSTEME_WEBHOOKS_FILE = os.path.join(DATA_DIR, "processed_systeme_webhooks.json")
# Keep track of the last N processed message IDs to dedupe FB retries
MAX_PROCESSED_MIDS = 1000
MAX_PROCESSED_XENDIT_WEBHOOKS = 2000
MAX_PROCESSED_SYSTEME_WEBHOOKS = 2000

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


def _already_processed_xendit(webhook_key: str) -> bool:
    if not webhook_key:
        return False
    with file_lock(PROCESSED_XENDIT_WEBHOOKS_FILE):
        keys = _load_json(PROCESSED_XENDIT_WEBHOOKS_FILE, [])
        return webhook_key in keys


def _mark_processed_xendit(webhook_key: str) -> None:
    if not webhook_key:
        return
    with file_lock(PROCESSED_XENDIT_WEBHOOKS_FILE):
        keys = _load_json(PROCESSED_XENDIT_WEBHOOKS_FILE, [])
        if webhook_key in keys:
            return
        keys.append(webhook_key)
        if len(keys) > MAX_PROCESSED_XENDIT_WEBHOOKS:
            keys = keys[-MAX_PROCESSED_XENDIT_WEBHOOKS:]
        _save_json(PROCESSED_XENDIT_WEBHOOKS_FILE, keys)


def _already_processed_systeme(webhook_key: str) -> bool:
    if not webhook_key:
        return False
    with file_lock(PROCESSED_SYSTEME_WEBHOOKS_FILE):
        keys = _load_json(PROCESSED_SYSTEME_WEBHOOKS_FILE, [])
        return webhook_key in keys


def _mark_processed_systeme(webhook_key: str) -> None:
    if not webhook_key:
        return
    with file_lock(PROCESSED_SYSTEME_WEBHOOKS_FILE):
        keys = _load_json(PROCESSED_SYSTEME_WEBHOOKS_FILE, [])
        if webhook_key in keys:
            return
        keys.append(webhook_key)
        if len(keys) > MAX_PROCESSED_SYSTEME_WEBHOOKS:
            keys = keys[-MAX_PROCESSED_SYSTEME_WEBHOOKS:]
        _save_json(PROCESSED_SYSTEME_WEBHOOKS_FILE, keys)


def _verify_xendit_callback_token(expected_token: str) -> bool:
    if not expected_token:
        logger.error("Xendit webhook token is not configured - rejecting webhook")
        return False
    received = request.headers.get("x-callback-token", "")
    if not received:
        return False
    return hmac.compare_digest(received, expected_token)


def _systeme_signature_candidates(raw_body: bytes):
    candidates = [raw_body]
    try:
        payload = json.loads(raw_body.decode("utf-8"))
        normalized = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace("/", "\\/")
        candidates.append(normalized.encode("utf-8"))
    except Exception:
        pass
    return candidates


def _verify_systeme_signature(expected_secret: str) -> bool:
    if not expected_secret:
        logger.error("Systeme webhook secret is not configured - rejecting webhook")
        return False

    signature = request.headers.get("X-Webhook-Signature", "")
    if not signature:
        return False

    raw_body = request.get_data() or b""
    for candidate in _systeme_signature_candidates(raw_body):
        expected = hmac.new(
            expected_secret.encode("utf-8"),
            candidate,
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(signature, expected):
            return True
    return False


def _verify_systeme_automation_token(expected_token: str) -> bool:
    if not expected_token:
        logger.error("Systeme automation token is not configured - rejecting webhook")
        return False
    received = request.args.get("token", "")
    if not received:
        return False
    return hmac.compare_digest(received, expected_token)


def _xendit_webhook_key(payload, fallback_event="xendit"):
    payload = payload or {}
    header_id = request.headers.get("webhook-id", "")
    if header_id:
        return f"{fallback_event}:{header_id}"

    normalized = payload
    if not ("event" in normalized and isinstance(normalized.get("data"), dict)):
        for value in payload.values():
            if not isinstance(value, dict):
                continue
            candidate = value.get("value") if isinstance(value.get("value"), dict) else value
            if isinstance(candidate, dict) and "event" in candidate and isinstance(candidate.get("data"), dict):
                normalized = candidate
                break

    if "event" in normalized and isinstance(normalized.get("data"), dict):
        data = normalized.get("data") or {}
        return ":".join(
            [
                str(normalized.get("event") or fallback_event),
                str(data.get("payment_id") or data.get("id") or normalized.get("id") or ""),
                str(data.get("status") or normalized.get("status") or ""),
                str(data.get("updated") or normalized.get("updated") or normalized.get("created") or ""),
            ]
        )

    return ":".join(
        [
            fallback_event,
            str(payload.get("id") or ""),
            str(payload.get("external_id") or ""),
            str(payload.get("status") or ""),
            str(payload.get("updated") or payload.get("paid_at") or payload.get("created") or ""),
        ]
    )


def _systeme_webhook_key(payload, fallback_event="systeme"):
    message_id = request.headers.get("X-Webhook-Message-Id", "")
    if message_id:
        return f"{fallback_event}:{message_id}"

    body_type = ""
    if isinstance(payload, dict):
        body_type = str(payload.get("type") or "").strip()

    raw_body = request.get_data() or b""
    digest = hashlib.sha256(raw_body).hexdigest()[:24]
    event_name = request.headers.get("X-Webhook-Event", "") or body_type or fallback_event
    event_timestamp = request.headers.get("X-Webhook-Event-Timestamp", "") or str(payload.get("created_at") or "")
    return f"{event_name}:{event_timestamp}:{digest}"


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


def _notify_xendit_webhook_failure(kind: str, webhook_key: str, error: Exception):
    try:
        from telegram_bot import send_message
        send_message(
            f"💳 *Xendit {kind} Webhook Failed*\n"
            f"🆔 {webhook_key[:120]}\n"
            f"⚠️ Error: {str(error)[:200]}"
        )
    except Exception:
        logger.exception("Failed to notify Telegram of Xendit %s webhook error", kind.lower())


def _notify_systeme_webhook_failure(webhook_key: str, error: Exception):
    try:
        from telegram_bot import send_message
        send_message(
            f"📚 *Systeme Webhook Failed*\n"
            f"🆔 {webhook_key[:120]}\n"
            f"⚠️ Error: {str(error)[:200]}"
        )
    except Exception:
        logger.exception("Failed to notify Telegram of Systeme webhook error")


def _process_xendit_invoice_webhook(payload: dict, webhook_key: str):
    from xendit_sync import process_invoice_webhook

    record = process_invoice_webhook(payload)
    if record:
        logger.info(
            "Processed Xendit invoice webhook %s invoice=%s email=%s amount=%s",
            webhook_key,
            record.get("xendit_invoice_id") or payload.get("id", ""),
            record.get("email", ""),
            record.get("amount", ""),
        )
    else:
        logger.info("Ignored Xendit invoice webhook %s with status=%s", webhook_key, payload.get("status", ""))
    return record


def _process_xendit_payment_webhook(payload: dict, webhook_key: str):
    from xendit_sync import process_payment_webhook

    record = process_payment_webhook(payload)
    if record:
        logger.info(
            "Processed Xendit payment webhook %s payment=%s payer=%s email=%s",
            webhook_key,
            record.get("xendit_payment_id", ""),
            record.get("payer_name", ""),
            record.get("email", ""),
        )
    else:
        logger.info("Ignored Xendit payment webhook %s", webhook_key)
    return record


def _process_systeme_webhook(payload: dict, webhook_key: str):
    from systeme_students import upsert_systeme_student

    event_name = request.headers.get("X-Webhook-Event", "") or str(payload.get("type") or "").strip()
    event_timestamp = request.headers.get("X-Webhook-Event-Timestamp", "") or str(payload.get("created_at") or "")
    message_id = request.headers.get("X-Webhook-Message-Id", "")
    student = upsert_systeme_student(
        payload,
        event_type=event_name,
        event_timestamp=event_timestamp,
        message_id=message_id,
    )
    if student:
        logger.info(
            "Processed Systeme webhook %s event=%s email=%s courses=%s",
            webhook_key,
            event_name or payload.get("type", ""),
            student.get("email", ""),
            len(student.get("courses", [])),
        )
    else:
        logger.info(
            "Ignored Systeme webhook %s event=%s",
            webhook_key,
            event_name or payload.get("type", ""),
        )
    return student


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


@app.route("/webhook/xendit/invoice", methods=["POST"])
def handle_xendit_invoice_webhook():
    """Handle legacy Xendit invoice/payment-link webhooks."""
    if not _verify_xendit_callback_token(XENDIT_INVOICE_WEBHOOK_TOKEN):
        logger.warning("Invalid Xendit invoice callback token")
        abort(403)

    payload = request.get_json(silent=True) or {}
    webhook_key = _xendit_webhook_key(payload, fallback_event="xendit_invoice")
    if _already_processed_xendit(webhook_key):
        logger.info("Skipping already-processed Xendit invoice webhook %s", webhook_key)
        return "OK", 200

    try:
        _process_xendit_invoice_webhook(payload, webhook_key)
        _mark_processed_xendit(webhook_key)
    except Exception as e:
        logger.exception("Xendit invoice webhook handler error")
        _notify_xendit_webhook_failure("Invoice", webhook_key, e)
        return "Webhook processing failed", 500
    return "OK", 200


@app.route("/webhook/xendit/payment", methods=["POST"])
def handle_xendit_payment_webhook():
    """Handle Xendit Payments API webhooks."""
    if not _verify_xendit_callback_token(XENDIT_PAYMENT_WEBHOOK_TOKEN):
        logger.warning("Invalid Xendit payment callback token")
        abort(403)

    payload = request.get_json(silent=True) or {}
    webhook_key = _xendit_webhook_key(payload, fallback_event="xendit_payment")
    if _already_processed_xendit(webhook_key):
        logger.info("Skipping already-processed Xendit payment webhook %s", webhook_key)
        return "OK", 200

    try:
        _process_xendit_payment_webhook(payload, webhook_key)
        _mark_processed_xendit(webhook_key)
    except Exception as e:
        logger.exception("Xendit payment webhook handler error")
        _notify_xendit_webhook_failure("Payment", webhook_key, e)
        return "Webhook processing failed", 500
    return "OK", 200


@app.route("/webhook/systeme", methods=["POST"])
def handle_systeme_webhook():
    """Handle signed official Systeme.io webhooks."""
    if not _verify_systeme_signature(SYSTEME_WEBHOOK_SECRET):
        logger.warning("Invalid Systeme webhook signature")
        abort(403)

    payload = request.get_json(silent=True) or {}
    webhook_key = _systeme_webhook_key(payload, fallback_event="systeme")
    if _already_processed_systeme(webhook_key):
        logger.info("Skipping already-processed Systeme webhook %s", webhook_key)
        return "OK", 200

    try:
        _process_systeme_webhook(payload, webhook_key)
        _mark_processed_systeme(webhook_key)
    except Exception as e:
        logger.exception("Systeme webhook handler error")
        _notify_systeme_webhook_failure(webhook_key, e)
        return "Webhook processing failed", 500
    return "OK", 200


@app.route("/webhook/systeme/automation", methods=["POST"])
def handle_systeme_automation_webhook():
    """Handle Systeme automation/workflow webhooks protected by a query token."""
    if not _verify_systeme_automation_token(SYSTEME_AUTOMATION_TOKEN):
        logger.warning("Invalid Systeme automation token")
        abort(403)

    payload = request.get_json(silent=True) or {}
    webhook_key = _systeme_webhook_key(payload, fallback_event="systeme_automation")
    if _already_processed_systeme(webhook_key):
        logger.info("Skipping already-processed Systeme automation webhook %s", webhook_key)
        return "OK", 200

    try:
        _process_systeme_webhook(payload, webhook_key)
        _mark_processed_systeme(webhook_key)
    except Exception as e:
        logger.exception("Systeme automation webhook handler error")
        _notify_systeme_webhook_failure(webhook_key, e)
        return "Webhook processing failed", 500
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
