"""
Data Queries - On-demand data fetching for AI chat.
Allows Karl to ask about recent FB messages, comments, and emails via natural language.
"""

import json
import os
import requests
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from config import (
    PAGE_ID, PAGE_ACCESS_TOKEN, BASE_URL, DATA_DIR, GMAIL_ENABLED
)
from xendit_payments import (
    extract_lookup_criteria,
    format_payment_lookup_summary,
    load_payment_store,
)

PHT = timezone(timedelta(hours=8))


def _parse_timestamp(value):
    """Parse ISO or RFC2822 timestamps into PHT-aware datetimes."""
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except Exception:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=PHT)

    return dt.astimezone(PHT)


def _filter_recent_entries(entries, cutoff):
    recent = []
    for entry in entries:
        ts = _parse_timestamp(entry.get("date") or entry.get("date_paid"))
        if ts and ts >= cutoff:
            recent.append(entry)
    return recent


def _payment_store_summary(payment_store, hours_back):
    checked_at = _parse_timestamp(payment_store.get("checked_at", ""))
    if not payment_store.get("payments") or not checked_at:
        return None

    cutoff = datetime.now(PHT) - timedelta(hours=hours_back)
    payments = _filter_recent_entries(payment_store.get("payments", []), cutoff)
    summary_lines = [
        f"📧 Recent Xendit Payment Activity (store synced: {checked_at.strftime('%Y-%m-%d %H:%M')} PHT)",
        f"• Stored Xendit payments in last {hours_back} hour(s): {len(payments)}",
    ]
    for payment in payments[:5]:
        payer = payment.get("payer_name") or payment.get("email") or "Unknown payer"
        summary_lines.append(
            f"  • {payer} - {payment.get('course', 'N/A')} "
            f"({payment.get('amount', 'N/A')})"
        )
    if not payments:
        summary_lines.append(
            f"No stored Xendit payments matched the last {hours_back} hour(s)."
        )

    return {
        "count": len(payments),
        "summary": "\n".join(summary_lines),
        "checked_at": checked_at,
    }


# ============================================================
# FACEBOOK DM QUERIES
# ============================================================

def get_recent_dms(hours_back=1):
    """Get recent Facebook DMs from stored messages."""
    messages_file = os.path.join(DATA_DIR, "messages.json")
    if not os.path.exists(messages_file):
        return {"count": 0, "messages": [], "summary": "No stored DMs found."}

    with open(messages_file) as f:
        all_messages = json.load(f)

    cutoff = datetime.now(PHT) - timedelta(hours=hours_back)
    recent = []

    for m in all_messages:
        try:
            msg_time = datetime.fromisoformat(m.get("timestamp", ""))
            if msg_time > cutoff:
                recent.append(m)
        except:
            pass

    if not recent:
        return {
            "count": 0,
            "messages": [],
            "summary": f"No new DMs in the last {hours_back} hour(s)."
        }

    summary_lines = [f"📬 {len(recent)} DM(s) in the last {hours_back} hour(s):\n"]
    for m in recent:
        name = m.get("sender_name", "Unknown")
        text = m.get("text", "")[:150]
        time_str = m.get("timestamp", "")[:16]
        summary_lines.append(f"• {name} ({time_str}): {text}")

    return {
        "count": len(recent),
        "messages": recent,
        "summary": "\n".join(summary_lines)
    }


# ============================================================
# FACEBOOK COMMENTS QUERIES
# ============================================================

def get_recent_comments(hours_back=1):
    """Fetch recent comments from Facebook Page posts."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    try:
        # Get recent posts
        url = f"{BASE_URL}/{PAGE_ID}/feed"
        params = {
            "access_token": PAGE_ACCESS_TOKEN,
            "fields": "id,message,created_time",
            "limit": 10,
        }
        response = requests.get(url, params=params, timeout=15)
        posts = response.json().get("data", [])
    except Exception as e:
        return {"count": 0, "comments": [], "summary": f"Error fetching posts: {e}"}

    all_comments = []

    for post in posts:
        post_id = post.get("id", "")
        post_msg = post.get("message", "")[:60]

        try:
            url = f"{BASE_URL}/{post_id}/comments"
            params = {
                "access_token": PAGE_ACCESS_TOKEN,
                "fields": "id,message,from,created_time",
                "limit": 50,
                "order": "reverse_chronological",
            }
            resp = requests.get(url, params=params, timeout=15)
            comments = resp.json().get("data", [])
        except:
            continue

        for c in comments:
            try:
                ct = datetime.fromisoformat(c.get("created_time", "").replace("Z", "+00:00"))
                if ct < cutoff:
                    break  # Comments are reverse-chronological, so stop early
                all_comments.append({
                    "post_preview": post_msg,
                    "from": c.get("from", {}).get("name", "Unknown"),
                    "message": c.get("message", ""),
                    "time": c.get("created_time", ""),
                })
            except:
                continue

    if not all_comments:
        return {
            "count": 0,
            "comments": [],
            "summary": f"No new comments in the last {hours_back} hour(s)."
        }

    summary_lines = [f"💬 {len(all_comments)} comment(s) in the last {hours_back} hour(s):\n"]
    for c in all_comments[:15]:
        summary_lines.append(f"• {c['from']} on \"{c['post_preview']}...\": {c['message'][:100]}")

    return {
        "count": len(all_comments),
        "comments": all_comments,
        "summary": "\n".join(summary_lines)
    }


# ============================================================
# EMAIL QUERIES (via stored reports or Gmail MCP)
# ============================================================

def get_recent_emails(hours_back=1):
    """Get recent email/payment activity from stored reports."""
    payment_store = load_payment_store()
    store_summary = _payment_store_summary(payment_store, hours_back)
    report_file = os.path.join(DATA_DIR, "enrollment_report.json")
    if not os.path.exists(report_file):
        if store_summary:
            return {
                "count": store_summary["count"],
                "summary": store_summary["summary"],
            }

        return {
            "count": 0,
            "summary": "No email report available yet. Use /enrollment to run a check, or wait for the next 7AM scheduled report."
        }

    with open(report_file) as f:
        report = json.load(f)

    checked_at = _parse_timestamp(report.get("checked_at", ""))
    if not checked_at:
        if store_summary:
            return {
                "count": store_summary["count"],
                "summary": store_summary["summary"],
            }
        return {
            "count": 0,
            "summary": "Stored email report has no valid timestamp. Use /enrollment to refresh it before asking for recent email activity."
        }

    cutoff = datetime.now(PHT) - timedelta(hours=hours_back)
    if checked_at < cutoff:
        if store_summary and store_summary["checked_at"] >= cutoff:
            return {
                "count": store_summary["count"],
                "summary": (
                    f"{store_summary['summary']}\n"
                    "Enrollment comparison data is older than this time window, "
                    "so only the stored Xendit payment index is current."
                ),
            }
        return {
            "count": 0,
            "summary": (
                f"Latest email report was checked at {checked_at.strftime('%Y-%m-%d %H:%M')} PHT, "
                f"which is older than the last {hours_back} hour(s). Use /enrollment to refresh it."
            )
        }

    payments = _filter_recent_entries(report.get("payments", []), cutoff)
    enrolments = _filter_recent_entries(report.get("enrolments", []), cutoff)
    unmatched = _filter_recent_entries(report.get("unmatched_students", []), cutoff)

    summary_lines = [f"📧 Recent Email Activity (report checked: {checked_at.strftime('%Y-%m-%d %H:%M')} PHT)\n"]
    summary_lines.append(f"• Xendit payment emails in last {hours_back} hour(s): {len(payments)}")
    summary_lines.append(f"• Systeme.io enrollment emails in last {hours_back} hour(s): {len(enrolments)}")
    summary_lines.append(f"• Unmatched payment emails in last {hours_back} hour(s): {len(unmatched)}")

    if unmatched:
        summary_lines.append("\n⚠️ Recent unmatched students (paid but not enrolled):")
        for s in unmatched[:5]:
            summary_lines.append(f"  • {s.get('email', 'N/A')} - {s.get('course', 'N/A')} ({s.get('amount', 'N/A')})")

    if payments:
        summary_lines.append("\n💰 Recent payments:")
        for p in payments[:5]:
            summary_lines.append(f"  • {p.get('email', 'N/A')} - {p.get('course', 'N/A')} ({p.get('amount', 'N/A')})")

    if not payments and not enrolments:
        summary_lines.append(f"\nNo payment or enrollment emails matched the last {hours_back} hour(s).")

    return {
        "count": len(payments) + len(enrolments),
        "summary": "\n".join(summary_lines)
    }


def get_payment_lookup(user_message, limit=5):
    """Search stored Xendit payments for a specific payer lookup."""
    return format_payment_lookup_summary(user_message, limit=limit)


# ============================================================
# MASTER QUERY - Detects what data Karl is asking about
# ============================================================

def build_data_context(user_message):
    """Detect what data Karl is asking about and fetch it.
    Returns context string to inject into AI prompt.
    """
    msg_lower = user_message.lower()

    # Detect time frame
    hours = 1  # default
    if "today" in msg_lower or "ngayon" in msg_lower:
        hours = 24
    elif "24 hour" in msg_lower or "24 hrs" in msg_lower or "24hrs" in msg_lower:
        hours = 24
    elif "12 hour" in msg_lower or "12 hrs" in msg_lower or "12hrs" in msg_lower:
        hours = 12
    elif "6 hour" in msg_lower or "6 hrs" in msg_lower or "6hrs" in msg_lower:
        hours = 6
    elif "3 hour" in msg_lower or "3 hrs" in msg_lower or "3hrs" in msg_lower:
        hours = 3
    elif "2 hour" in msg_lower or "2 hrs" in msg_lower or "2hrs" in msg_lower:
        hours = 2
    elif "1 hour" in msg_lower or "1 hr" in msg_lower or "1hr" in msg_lower or "isang oras" in msg_lower:
        hours = 1
    elif "30 min" in msg_lower or "kalahating oras" in msg_lower:
        hours = 0.5
    elif "this week" in msg_lower or "ngayong linggo" in msg_lower:
        hours = 168

    context_parts = []

    # Detect what data is being asked about
    asking_messages = any(kw in msg_lower for kw in [
        "message", "dm", "inbox", "mensahe", "nagmessage",
        "nag message", "nag-message", "sinend", "nagchat",
        "nag chat", "may bago", "new message", "latest message",
        "recent message", "bagong message"
    ])

    asking_comments = any(kw in msg_lower for kw in [
        "comment", "komento", "nagcomment", "nag comment",
        "nag-comment", "new comment", "latest comment",
        "recent comment", "bagong comment"
    ])

    asking_emails = any(kw in msg_lower for kw in [
        "email", "gmail", "payment", "xendit", "bayad",
        "enrollment", "enroll", "nag bayad", "nagbayad",
        "nag-bayad", "latest email", "recent email"
    ])

    asking_vpn = any(kw in msg_lower for kw in [
        "vpn", "karlcomvpn", "coins", "top up", "topup",
        "top-up", "wireguard", "remote access", "gcash",
        "vpn subscriber", "vpn customer", "vpn payment",
        "vpn message", "vpn dm", "vpn comment"
    ])

    # If asking generally ("any updates?", "may bago?", "anong meron?")
    asking_general = any(kw in msg_lower for kw in [
        "update", "bago", "meron", "anong nangyari", "what happened",
        "what's new", "ano na", "kamusta", "status ng page",
        "latest", "recent", "balita", "report"
    ])

    # If asking about VPN, also fetch DMs and comments (VPN inquiries come through those)
    if asking_vpn:
        asking_messages = True
        asking_comments = True

    payment_lookup_criteria = extract_lookup_criteria(user_message) if asking_emails else {
        "emails": [],
        "phones": [],
        "names": [],
        "tokens": [],
    }
    asking_specific_payment_lookup = asking_emails and any([
        payment_lookup_criteria.get("emails"),
        payment_lookup_criteria.get("phones"),
        payment_lookup_criteria.get("names"),
    ])

    # Fetch requested data
    if asking_messages or asking_general:
        dm_data = get_recent_dms(hours)
        context_parts.append(f"\n[FACEBOOK DMs - Last {hours} hour(s)]\n{dm_data['summary']}")

    if asking_comments or asking_general:
        comment_data = get_recent_comments(hours)
        context_parts.append(f"\n[FACEBOOK COMMENTS - Last {hours} hour(s)]\n{comment_data['summary']}")

    if asking_emails or asking_general:
        email_data = get_recent_emails(hours)
        context_parts.append(f"\n[EMAIL/PAYMENT DATA]\n{email_data['summary']}")

    if asking_specific_payment_lookup:
        payment_lookup = get_payment_lookup(user_message)
        context_parts.append(f"\n[PAYMENT LOOKUP]\n{payment_lookup['summary']}")

    if asking_vpn:
        context_parts.append(
            "\n[VPN SERVICE INFO]\n"
            "KarlComVPN (vpn.karlc.cloud) - WireGuard VPN for MikroTik remote access\n"
            "Coin Pricing: 50=\u20b150, 150=\u20b1150, 300=\u20b1300, 600=\u20b1600, 1200=\u20b11200\n"
            "Payment: GCash 09495446516 (Karl Andrew C.)\n"
            "NOTE: Look for VPN-related keywords in DMs/comments above (vpn, coins, top up, load, gcash, remote access)"
        )

    if context_parts:
        return "\n".join(context_parts)

    return ""
