"""
Karl C AI Buddy - Main Application
Runs all services:
1. Webhook server for Facebook DMs (Flask)
2. Telegram bot listener (long polling)
3. Scheduled reports (7AM & 7PM Philippine Time)
"""

import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

try:
    # Python 3.9+
    from zoneinfo import ZoneInfo
    PHT = ZoneInfo("Asia/Manila")
except Exception:
    PHT = timezone(timedelta(hours=8))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

_shutdown_event = threading.Event()


def run_webhook_server():
    """Run the Flask webhook server."""
    from webhook_server import app
    port = int(os.environ.get("PORT", 5000))
    logger.info("Starting webhook server on port %s", port)
    # debug=False, use_reloader=False is important inside a thread
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def run_telegram_listener():
    """Run the Telegram bot listener."""
    logger.info("Starting Telegram bot listener")
    from telegram_bot import start_listener
    start_listener()


def run_morning_report():
    logger.info("Running 7AM morning report")
    try:
        from fb_agent import run_agent
        run_agent(is_morning=True)
        logger.info("Morning report completed")
    except Exception:
        logger.exception("Morning report error")
        try:
            from telegram_bot import send_message
            send_message("❌ Error generating morning report - check logs")
        except Exception:
            logger.exception("Could not notify Telegram")


def run_evening_report():
    logger.info("Running 7PM evening report")
    try:
        from fb_agent import run_agent
        run_agent(is_morning=False)
        logger.info("Evening report completed")
    except Exception:
        logger.exception("Evening report error")
        try:
            from telegram_bot import send_message
            send_message("❌ Error generating evening report - check logs")
        except Exception:
            logger.exception("Could not notify Telegram")


def run_hourly_enrollment_watch():
    logger.info("Running hourly enrollment watch")
    try:
        from fb_agent import run_enrollment_check
        report = run_enrollment_check(notify_if_new_tickets=True)
        if report:
            logger.info(
                "Hourly enrollment watch completed: payments=%s enrolments=%s matched=%s unmatched=%s",
                report.get("total_payments", 0),
                report.get("total_enrolments", 0),
                report.get("matched", 0),
                report.get("unmatched", 0),
            )
    except Exception:
        logger.exception("Hourly enrollment watch error")
        try:
            from telegram_bot import send_message
            send_message("❌ Error running hourly enrollment watch - check logs")
        except Exception:
            logger.exception("Could not notify Telegram")


def _next_run_at(hour_pht: int) -> datetime:
    """Return the next UTC datetime corresponding to `hour_pht:00` Philippine time."""
    now_pht = datetime.now(PHT)
    target = now_pht.replace(hour=hour_pht, minute=0, second=0, microsecond=0)
    if target <= now_pht:
        target += timedelta(days=1)
    return target


def _next_hourly_run() -> datetime:
    """Return the next top-of-hour datetime in Philippine time."""
    now_pht = datetime.now(PHT)
    target = now_pht.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return target


def run_scheduler():
    """Simple timezone-aware scheduler for reports and enrollment checks."""
    logger.info("Scheduler started (hourly enrollment watch + 7AM/7PM Asia/Manila reports)")

    next_morning = _next_run_at(7)
    next_evening = _next_run_at(19)
    next_hourly_enrollment = _next_hourly_run()

    while not _shutdown_event.is_set():
        now = datetime.now(PHT)

        if now >= next_hourly_enrollment:
            run_hourly_enrollment_watch()
            next_hourly_enrollment = _next_hourly_run()

        if now >= next_morning:
            run_morning_report()
            next_morning = _next_run_at(7)

        if now >= next_evening:
            run_evening_report()
            next_evening = _next_run_at(19)

        # Sleep up to 30 seconds but wake early on shutdown signal.
        _shutdown_event.wait(timeout=30)


def send_startup_message():
    """Send a startup notification to Karl via Telegram."""
    try:
        from telegram_bot import send_message
        msg = (
            "🤖 *Karl C AI Buddy - ONLINE!*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "✅ Webhook Server: Running\n"
            "✅ Telegram Listener: Active\n"
            "✅ Scheduler: Hourly enrollment watch + 7AM & 7PM reports (PHT)\n"
            "✅ AI Chat: Ready\n\n"
            f"🕐 Started: {datetime.now(PHT).strftime('%Y-%m-%d %H:%M:%S')} PHT\n\n"
            "💬 Chat with me anytime Boss!\n"
            "Type /help for commands."
        )
        send_message(msg)
    except Exception:
        logger.exception("Error sending startup message")


def _install_signal_handlers():
    def _shutdown(signum, frame):
        logger.info("Received signal %s - shutting down", signum)
        _shutdown_event.set()
        try:
            from telegram_bot import send_message
            send_message("🔴 *Karl C AI Buddy - OFFLINE*\nShutting down gracefully...")
        except Exception:
            pass
        # Give threads a moment, then exit.
        time.sleep(2)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)


def _preflight_checks():
    """Warn loudly if critical env vars are missing."""
    from config import (
        PAGE_ID, PAGE_ACCESS_TOKEN, FB_APP_SECRET, WEBHOOK_VERIFY_TOKEN,
        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY,
    )
    missing = []
    if not PAGE_ID: missing.append("FB_PAGE_ID")
    if not PAGE_ACCESS_TOKEN: missing.append("FB_PAGE_ACCESS_TOKEN")
    if not FB_APP_SECRET: missing.append("FB_APP_SECRET (webhook signature will fail!)")
    if not WEBHOOK_VERIFY_TOKEN: missing.append("FB_VERIFY_TOKEN")
    if not TELEGRAM_BOT_TOKEN: missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID: missing.append("TELEGRAM_CHAT_ID")
    if not GEMINI_API_KEY: missing.append("GEMINI_API_KEY")
    if missing:
        logger.warning("Missing env vars: %s", ", ".join(missing))


def main():
    print("=" * 60)
    print("  Karl C AI Buddy - Starting Up")
    print(f"  Time: {datetime.now(PHT).strftime('%Y-%m-%d %H:%M:%S')} PHT")
    print("=" * 60)

    _preflight_checks()

    from config import DATA_DIR, REPORT_DIR
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)

    _install_signal_handlers()

    webhook_thread = threading.Thread(target=run_webhook_server, daemon=True)
    webhook_thread.start()
    logger.info("Webhook server thread started")

    time.sleep(2)

    telegram_thread = threading.Thread(target=run_telegram_listener, daemon=True)
    telegram_thread.start()
    logger.info("Telegram listener thread started")

    time.sleep(2)
    send_startup_message()

    logger.info("All services started - scheduler running")
    print("=" * 60)

    try:
        run_scheduler()
    except Exception:
        logger.exception("Scheduler crashed")


if __name__ == "__main__":
    main()
