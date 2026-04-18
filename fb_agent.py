"""
Facebook Page Agent for Karl C
- Compiles comments from page posts
- Suggests keyword-based replies (Review-First Mode)
- Generates reports with DMs and tickets
- Handles webhook for real-time DM notifications
"""

import os
import re
import requests
from datetime import datetime, timedelta, timezone

from config import (
    PAGE_ID, PAGE_NAME, PAGE_ACCESS_TOKEN,
    BASE_URL, KEYWORD_REPLIES, DATA_DIR, REPORT_DIR
)
from storage import file_lock, load_json, save_json
from telegram_bot import send_report, send_suggested_replies_summary, send_message
from ticket_system import (
    get_pending_tickets,
    get_ticket_stats,
    format_pending_tickets_report,
    create_enrollment_ticket,
    filter_resolved_enrollment_students,
)
from enrollment_checker import compare_payments_vs_enrolments, format_comparison_telegram

PHT = timezone(timedelta(hours=8))

# Ensure directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)


# ============================================================
# FACEBOOK API FUNCTIONS
# ============================================================

def get_page_posts(limit=25):
    """Fetch recent posts from the Facebook Page."""
    url = f"{BASE_URL}/{PAGE_ID}/feed"
    params = {
        "access_token": PAGE_ACCESS_TOKEN,
        "fields": "id,message,created_time,permalink_url",
        "limit": limit,
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        data = response.json()
        return data.get("data", [])
    except Exception as e:
        print(f"Error fetching posts: {e}")
        return []


def get_post_comments(post_id, limit=100):
    """Fetch comments for a specific post."""
    url = f"{BASE_URL}/{post_id}/comments"
    params = {
        "access_token": PAGE_ACCESS_TOKEN,
        "fields": "id,message,from,created_time",
        "limit": limit,
        "order": "reverse_chronological",
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        data = response.json()
        return data.get("data", [])
    except Exception as e:
        print(f"Error fetching comments for {post_id}: {e}")
        return []


def reply_to_comment(comment_id, message):
    """Reply to a Facebook comment."""
    url = f"{BASE_URL}/{comment_id}/comments"
    params = {
        "access_token": PAGE_ACCESS_TOKEN,
        "message": message,
    }
    try:
        response = requests.post(url, data=params, timeout=15)
        return response.json()
    except Exception as e:
        print(f"Error replying to comment: {e}")
        return {"error": str(e)}


# ============================================================
# COMMENT COMPILATION & KEYWORD MATCHING
# ============================================================

def _load_json(filepath, default=None):
    """Load JSON file."""
    if default is None:
        default = []
    return load_json(filepath, default)


def _save_json(filepath, data):
    """Save JSON file."""
    save_json(filepath, data)


def match_keyword(text):
    """Check if text matches any keyword. Longest keywords win so that
    'dual isp' beats 'isp', and 'solar' beats 'how much'."""
    text_lower = text.lower()
    for keyword in sorted(KEYWORD_REPLIES.keys(), key=len, reverse=True):
        if keyword.lower() in text_lower:
            return keyword
    return None


def compile_comments_report(hours_back=12):
    """Compile all comments from recent posts within the time window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    
    replied_file = os.path.join(DATA_DIR, "replied_comments.json")
    pending_file = os.path.join(DATA_DIR, "pending_replies.json")

    with file_lock(pending_file):
        with file_lock(replied_file):
            replied = set(_load_json(replied_file, []))
    
    posts = get_page_posts(limit=25)
    
    report_data = {
        "timestamp": datetime.now(PHT).isoformat(),
        "period_hours": hours_back,
        "posts": [],
        "total_new_comments": 0,
        "total_suggested_replies": 0,
        "total_new_dms": 0,
        "suggested_replies": [],
    }
    
    for post in posts:
        post_id = post.get("id", "")
        post_message = post.get("message", "")[:100]
        
        comments = get_post_comments(post_id)
        new_comments = []
        
        for comment in comments:
            comment_time = comment.get("created_time", "")
            try:
                ct = datetime.fromisoformat(comment_time.replace("Z", "+00:00"))
                if ct < cutoff:
                    continue
            except:
                continue
            
            comment_id = comment.get("id", "")
            comment_msg = comment.get("message", "")
            comment_from = comment.get("from", {}).get("name", "Unknown")
            
            new_comments.append({
                "id": comment_id,
                "message": comment_msg,
                "from": comment_from,
                "time": comment_time,
            })
            
            # Check for keyword match (only if not already replied)
            if comment_id not in replied:
                keyword = match_keyword(comment_msg)
                if keyword:
                    report_data["suggested_replies"].append({
                        "comment_id": comment_id,
                        "comment_message": comment_msg,
                        "comment_from": comment_from,
                        "post_id": post_id,
                        "post_preview": post_message,
                        "keyword_matched": keyword,
                        "suggested_reply": KEYWORD_REPLIES[keyword],
                    })
        
        if new_comments:
            report_data["posts"].append({
                "post_id": post_id,
                "post_preview": post_message,
                "comments": new_comments,
            })
            report_data["total_new_comments"] += len(new_comments)
    
    report_data["total_suggested_replies"] = len(report_data["suggested_replies"])
    
    # Save pending replies
    with file_lock(pending_file):
        with file_lock(replied_file):
            _save_json(pending_file, report_data["suggested_replies"])
    
    # Count DMs
    messages_file = os.path.join(DATA_DIR, "messages.json")
    if os.path.exists(messages_file):
        messages = _load_json(messages_file, [])
        recent_msgs = []
        for m in messages:
            try:
                mt = datetime.fromisoformat(m.get("timestamp", ""))
                if mt > cutoff.astimezone(PHT):
                    recent_msgs.append(m)
            except:
                pass
        report_data["total_new_dms"] = len(recent_msgs)
        report_data["dms"] = recent_msgs
    
    return report_data


def approve_replies(action, specific_numbers=None):
    """Approve or skip suggested replies."""
    pending_file = os.path.join(DATA_DIR, "pending_replies.json")
    replied_file = os.path.join(DATA_DIR, "replied_comments.json")

    with file_lock(pending_file):
        with file_lock(replied_file):
            pending = _load_json(pending_file, [])
            replied = _load_json(replied_file, [])

            results = []

            if action == "all":
                for i, reply in enumerate(pending, 1):
                    result = reply_to_comment(reply["comment_id"], reply["suggested_reply"])
                    if "error" not in result:
                        replied.append(reply["comment_id"])
                        results.append({"reply_num": i, "status": "sent"})
                    else:
                        results.append({"reply_num": i, "status": "error"})

            elif action == "skip_all":
                for i in range(len(pending)):
                    results.append({"reply_num": i + 1, "status": "skipped"})

            elif isinstance(action, list):
                # Approve specific numbers
                for i, reply in enumerate(pending, 1):
                    if i in action:
                        result = reply_to_comment(reply["comment_id"], reply["suggested_reply"])
                        if "error" not in result:
                            replied.append(reply["comment_id"])
                            results.append({"reply_num": i, "status": "sent"})
                        else:
                            results.append({"reply_num": i, "status": "error"})
                    else:
                        results.append({"reply_num": i, "status": "skipped"})

            elif action == "skip" and specific_numbers:
                for i, reply in enumerate(pending, 1):
                    if i in specific_numbers:
                        results.append({"reply_num": i, "status": "skipped"})
                    else:
                        result = reply_to_comment(reply["comment_id"], reply["suggested_reply"])
                        if "error" not in result:
                            replied.append(reply["comment_id"])
                            results.append({"reply_num": i, "status": "sent"})
                        else:
                            results.append({"reply_num": i, "status": "error"})

            _save_json(replied_file, replied)
            _save_json(pending_file, [])

    return results


# ============================================================
# REPORT GENERATION
# ============================================================

def format_report_markdown(report_data):
    """Format report data as markdown."""
    md = f"# Facebook Page Report - {PAGE_NAME}\n"
    md += f"**Report Time:** {report_data['timestamp'][:19]} PHT\n"
    md += f"**Period:** Last {report_data['period_hours']} hours\n"
    md += f"**Mode:** Review-First (No auto-reply)\n\n"
    md += "---\n\n"
    
    # Summary
    ticket_stats = get_ticket_stats()
    md += "## Summary\n"
    md += f"- **Total New Comments:** {report_data['total_new_comments']}\n"
    md += f"- **Suggested Replies (Awaiting Approval):** {report_data['total_suggested_replies']}\n"
    md += f"- **New DMs (Messages):** {report_data.get('total_new_dms', 0)}\n"
    md += f"- **Pending Student Tickets:** {ticket_stats['pending']}\n"
    md += f"- **Posts with New Activity:** {len(report_data['posts'])}\n\n"
    md += "---\n\n"
    
    # DMs
    dms = report_data.get("dms", [])
    if dms:
        md += f"## Incoming Messages (DMs)\n"
        md += f"**Total New Messages:** {len(dms)}\n\n"
        for dm in dms[:10]:
            md += f"- **{dm.get('sender_name', 'Unknown')}**: {dm.get('text', '')[:100]}\n"
        md += "\n---\n\n"
    
    # Comments
    if report_data["posts"]:
        md += "## Comments by Post\n\n"
        for post in report_data["posts"]:
            md += f"### {post['post_preview'][:80]}...\n"
            for c in post["comments"][:10]:
                md += f"- **{c['from']}**: {c['message'][:100]}\n"
            md += "\n"
        md += "---\n\n"
    
    # Suggested Replies
    if report_data["suggested_replies"]:
        md += f"## Suggested Replies ({len(report_data['suggested_replies'])} pending)\n\n"
        for i, s in enumerate(report_data["suggested_replies"], 1):
            md += f"**#{i}** [{s['keyword_matched']}] {s['comment_from']}: \"{s['comment_message'][:60]}\"\n"
            md += f"> Reply: \"{s['suggested_reply'][:80]}...\"\n\n"
        md += "---\n\n"
    
    # Pending Tickets
    md += format_pending_tickets_report()
    
    return md


def save_report(report_data):
    """Save report to file and return path + markdown."""
    markdown = format_report_markdown(report_data)
    
    timestamp = datetime.now(PHT).strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(REPORT_DIR, f"report_{timestamp}.md")
    
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(filepath, "w") as f:
        f.write(markdown)
    
    return filepath, markdown


# ============================================================
# ENROLLMENT CHECK
# ============================================================

def run_enrollment_check(notify_if_new_tickets=False):
    """Run enrollment comparison and create tickets for unmatched students."""
    print("\n[Enrollment] Running payment vs enrollment comparison...")
    
    try:
        report = compare_payments_vs_enrolments(days_back=7)
        active_unmatched, suppressed_unmatched = filter_resolved_enrollment_students(
            report.get("unmatched_students", [])
        )
        report["suppressed_unmatched_students"] = suppressed_unmatched
        report["suppressed"] = len(suppressed_unmatched)
        report["unmatched_students"] = active_unmatched
        report["unmatched"] = len(active_unmatched)
        
        new_tickets = 0
        for student in report.get("unmatched_students", []):
            ticket = create_enrollment_ticket(
                student_name=student.get("payer_name", student.get("name", student.get("email", "Unknown"))),
                student_email=student.get("email", "N/A"),
                course_title=student.get("course", "Unknown"),
                price=student.get("amount", "Unknown"),
                payment_method=student.get("payment_method", "Unknown"),
                date_paid=student.get("date_paid", student.get("date", "Unknown")),
                phone_number=student.get("phone", ""),
            )
            if ticket:
                new_tickets += 1
        
        print(f"[Enrollment] Payments: {report['total_payments']}, Enrollments: {report['total_enrolments']}")
        print(f"[Enrollment] Matched: {report['matched']}, Unmatched: {report['unmatched']}")
        if report.get("suppressed"):
            print(f"[Enrollment] Suppressed (manually resolved): {report['suppressed']}")
        if new_tickets:
            print(f"[Enrollment] Created {new_tickets} new enrollment tickets")

        if notify_if_new_tickets and report.get("unmatched"):
            msg = "🚨 *Hourly Enrollment Alert*\n"
            msg += "━━━━━━━━━━━━━━━━━━\n\n"
            if new_tickets:
                msg += f"🔴 New unmatched students: {new_tickets}\n"
            else:
                msg += f"🟠 Active unmatched students still pending: {report['unmatched']}\n"
            msg += f"💰 Xendit Payments checked: {report['total_payments']}\n"
            msg += f"✅ Systeme.io Enrollments: {report['total_enrolments']}\n\n"
            msg += "Latest unmatched students:\n"
            for student in report.get("unmatched_students", [])[:5]:
                label = student.get("payer_name") or student.get("email", "N/A")
                msg += f"• {label}"
                if student.get("phone"):
                    msg += f" | {student.get('phone')}"
                msg += f" | {student.get('email', 'N/A')} - {student.get('course', 'Unknown')}\n"
            msg += "\nUse `/tickets` or `/enrollment` to review."
            send_message(msg)
        
        return report
    except Exception as e:
        print(f"[Enrollment] Error: {e}")
        return None


# ============================================================
# MAIN AGENT
# ============================================================

def run_agent(is_morning=False):
    """Main agent function."""
    print(f"[{datetime.now(PHT).strftime('%Y-%m-%d %H:%M:%S')}] Facebook Page Agent starting...")
    print(f"Page: {PAGE_NAME} (ID: {PAGE_ID})")
    print(f"Report type: {'Morning (7AM) + Enrollment Check' if is_morning else 'Evening (7PM)'}")
    
    # Compile comments
    report_data = compile_comments_report(hours_back=12)
    
    print(f"Found {report_data['total_new_comments']} new comments")
    print(f"Found {report_data.get('total_new_dms', 0)} new DMs")
    print(f"Generated {report_data['total_suggested_replies']} suggested replies")
    
    # Run enrollment check only for morning
    enrollment_report = None
    if is_morning:
        enrollment_report = run_enrollment_check()
    
    # Save report
    filepath, markdown = save_report(report_data)
    print(f"Report saved to: {filepath}")
    
    # Send via Telegram
    try:
        send_report(markdown)
        
        if report_data["suggested_replies"]:
            send_suggested_replies_summary(report_data["suggested_replies"])
        
        if is_morning and enrollment_report:
            send_message(format_comparison_telegram(enrollment_report))
    except Exception as e:
        print(f"Error sending to Telegram: {e}")
    
    return filepath, markdown, report_data


if __name__ == "__main__":
    import sys
    is_morning = "--morning" in sys.argv or "-m" in sys.argv
    run_agent(is_morning=is_morning)
