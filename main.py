"""
Karl C AI Buddy - Main Application
Runs all services:
1. Webhook server for Facebook DMs (Flask)
2. Telegram bot listener (long polling)
3. Scheduled reports (7AM & 7PM)
"""

import os
import sys
import time
import threading
import schedule
from datetime import datetime, timedelta, timezone

PHT = timezone(timedelta(hours=8))


def run_webhook_server():
    """Run the Flask webhook server."""
    from webhook_server import app
    port = int(os.environ.get("PORT", 5000))
    print(f"[Main] Starting webhook server on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def run_telegram_listener():
    """Run the Telegram bot listener."""
    print("[Main] Starting Telegram bot listener...")
    from telegram_bot import start_listener
    start_listener()


def run_morning_report():
    """Run the 7AM morning report."""
    print(f"\n[Scheduler] Running 7AM morning report at {datetime.now(PHT).strftime('%Y-%m-%d %H:%M:%S')} PHT")
    try:
        from fb_agent import run_agent
        run_agent(is_morning=True)
        print("[Scheduler] Morning report completed!")
    except Exception as e:
        print(f"[Scheduler] Morning report error: {e}")
        try:
            from telegram_bot import send_message
            send_message(f"❌ Error generating morning report: {str(e)[:200]}")
        except:
            pass


def run_evening_report():
    """Run the 7PM evening report."""
    print(f"\n[Scheduler] Running 7PM evening report at {datetime.now(PHT).strftime('%Y-%m-%d %H:%M:%S')} PHT")
    try:
        from fb_agent import run_agent
        run_agent(is_morning=False)
        print("[Scheduler] Evening report completed!")
    except Exception as e:
        print(f"[Scheduler] Evening report error: {e}")
        try:
            from telegram_bot import send_message
            send_message(f"❌ Error generating evening report: {str(e)[:200]}")
        except:
            pass


def run_scheduler():
    """Run the scheduled tasks."""
    print("[Main] Starting scheduler...")
    
    # Schedule reports at PHT times
    # Note: schedule library uses system time, so we need to convert
    # PHT is UTC+8
    schedule.every().day.at("23:00").do(run_morning_report)  # 7AM PHT = 23:00 UTC (previous day)
    schedule.every().day.at("11:00").do(run_evening_report)  # 7PM PHT = 11:00 UTC
    
    print("[Main] Scheduled: Morning report at 7:00 AM PHT")
    print("[Main] Scheduled: Evening report at 7:00 PM PHT")
    
    while True:
        schedule.run_pending()
        time.sleep(30)


def send_startup_message():
    """Send a startup notification to Karl via Telegram."""
    try:
        from telegram_bot import send_message
        msg = "🤖 *Karl C AI Buddy - ONLINE!*\n"
        msg += "━━━━━━━━━━━━━━━━━━\n\n"
        msg += "✅ Webhook Server: Running\n"
        msg += "✅ Telegram Listener: Active\n"
        msg += "✅ Scheduler: 7AM & 7PM reports\n"
        msg += "✅ AI Chat: Ready\n\n"
        msg += f"🕐 Started: {datetime.now(PHT).strftime('%Y-%m-%d %H:%M:%S')} PHT\n\n"
        msg += "💬 Chat with me anytime Boss!\n"
        msg += "Type /help for commands."
        send_message(msg)
    except Exception as e:
        print(f"[Main] Error sending startup message: {e}")


def main():
    """Main entry point - start all services."""
    print("=" * 60)
    print("  Karl C AI Buddy - Starting Up")
    print(f"  Time: {datetime.now(PHT).strftime('%Y-%m-%d %H:%M:%S')} PHT")
    print("=" * 60)
    
    # Ensure data directories exist
    from config import DATA_DIR, REPORT_DIR
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)
    
    # Start webhook server in a thread
    webhook_thread = threading.Thread(target=run_webhook_server, daemon=True)
    webhook_thread.start()
    print("[Main] Webhook server thread started")
    
    # Wait for webhook server to start
    time.sleep(2)
    
    # Start Telegram listener in a thread
    telegram_thread = threading.Thread(target=run_telegram_listener, daemon=True)
    telegram_thread.start()
    print("[Main] Telegram listener thread started")
    
    # Wait a bit then send startup message
    time.sleep(2)
    send_startup_message()
    
    # Run scheduler in main thread (keeps the process alive)
    print("[Main] All services started! Running scheduler...")
    print("=" * 60)
    
    try:
        run_scheduler()
    except KeyboardInterrupt:
        print("\n[Main] Shutting down...")
        try:
            from telegram_bot import send_message
            send_message("🔴 *Karl C AI Buddy - OFFLINE*\nShutting down...")
        except:
            pass


if __name__ == "__main__":
    main()
