"""
Enrollment Checker - Compares Xendit payments vs Systeme.io enrollments.
Identifies students who paid but haven't completed enrollment.
"""

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

from config import DATA_DIR, GMAIL_ENABLED

PHT = timezone(timedelta(hours=8))


def _run_gmail_search(query, max_results=20):
    """Run Gmail search via MCP CLI."""
    try:
        result = subprocess.run(
            ["manus-mcp-cli", "tool", "call", "gmail_search_messages",
             "--server", "gmail",
             "--input", json.dumps({"query": query, "maxResults": max_results})],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            # Find the result file
            import glob
            files = sorted(glob.glob("/tmp/manus-mcp/mcp_result_*.json"), key=os.path.getmtime, reverse=True)
            if files:
                with open(files[0]) as f:
                    return json.load(f)
        return None
    except Exception as e:
        print(f"[Enrollment] Gmail search error: {e}")
        return None


def _run_gmail_read(thread_ids):
    """Read full email threads via MCP CLI."""
    try:
        result = subprocess.run(
            ["manus-mcp-cli", "tool", "call", "gmail_read_threads",
             "--server", "gmail",
             "--input", json.dumps({"threadIds": thread_ids})],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            import glob
            files = sorted(glob.glob("/tmp/manus-mcp/mcp_result_*.json"), key=os.path.getmtime, reverse=True)
            if files:
                with open(files[0]) as f:
                    return json.load(f)
        return None
    except Exception as e:
        print(f"[Enrollment] Gmail read error: {e}")
        return None


def _extract_payer_email(text):
    """Extract payer email from Xendit invoice email body."""
    patterns = [
        r'Payer Email[:\s]*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
        r'Email[:\s]*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).lower()
    return None


def _extract_course_from_subject(subject):
    """Extract course name from Xendit invoice subject."""
    # Subject format: "INVOICE PAID: karlcw-course-name-price-id"
    subject_lower = subject.lower()
    
    course_map = {
        "quickstart": "MikroTik Basic (QuickStart)",
        "dual-isp": "MikroTik Dual-ISP",
        "hybrid-access": "MikroTik Hybrid",
        "traffic-control": "MikroTik Traffic Control",
        "core10g": "MikroTik 10G Core Part 1",
        "ospf": "MikroTik 10G Core Part 2 (OSPF)",
        "ftth": "Hybrid FTTH (PLC + FBT)",
        "solar": "DIY Hybrid Solar",
        "bundle": "Course Bundle",
    }
    
    for key, name in course_map.items():
        if key in subject_lower:
            return name
    
    return subject.split(":")[-1].strip() if ":" in subject else subject


def _extract_amount(text):
    """Extract payment amount from email body."""
    patterns = [
        r'(?:PHP|₱)\s*([\d,]+(?:\.\d{2})?)',
        r'Amount[:\s]*(?:PHP|₱)?\s*([\d,]+(?:\.\d{2})?)',
        r'Total[:\s]*(?:PHP|₱)?\s*([\d,]+(?:\.\d{2})?)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return f"PHP {match.group(1)}"
    return "N/A"


def _extract_enrolment_email(text):
    """Extract student email from New Enrolment email body."""
    # Look for email patterns in the body
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    # Filter out system emails
    system_emails = ["noreply@xendit.co", "mr.godzwill@gmail.com", "noreply@systeme.io"]
    student_emails = [e.lower() for e in emails if e.lower() not in system_emails]
    return student_emails[0] if student_emails else None


def compare_payments_vs_enrolments(days_back=7):
    """Compare Xendit payments with Systeme.io enrollments."""
    print(f"[Enrollment] Comparing last {days_back} days...")
    
    # Search for Xendit invoices
    xendit_search = _run_gmail_search(f"from:noreply@xendit.co INVOICE PAID newer_than:{days_back}d")
    
    # Search for New Enrolment emails
    enrolment_search = _run_gmail_search(f"from:mr.godzwill@gmail.com New Enrolment newer_than:{days_back}d")
    
    # Parse Xendit invoices
    payments = []
    if xendit_search:
        # Get thread IDs for Xendit invoices
        xendit_threads = []
        all_messages = xendit_search if isinstance(xendit_search, list) else xendit_search.get("messages", [])
        
        for msg in all_messages:
            if isinstance(msg, dict):
                subject = msg.get("subject", "")
                thread_id = msg.get("threadId", msg.get("id", ""))
                if "INVOICE PAID" in subject.upper():
                    xendit_threads.append(thread_id)
        
        # Read full threads to get payer emails
        if xendit_threads:
            thread_data = _run_gmail_read(xendit_threads[:10])
            if thread_data:
                threads = thread_data if isinstance(thread_data, list) else thread_data.get("threads", [])
                for thread in threads:
                    messages = thread.get("messages", []) if isinstance(thread, dict) else []
                    for msg in messages:
                        subject = msg.get("subject", "")
                        body = msg.get("markdown", msg.get("body", ""))
                        
                        if "INVOICE PAID" in subject.upper():
                            payer_email = _extract_payer_email(body)
                            if payer_email:
                                payments.append({
                                    "email": payer_email,
                                    "course": _extract_course_from_subject(subject),
                                    "amount": _extract_amount(body),
                                    "subject": subject,
                                    "date": msg.get("date", ""),
                                })
    
    print(f"[Enrollment] Found {len(payments)} Xendit invoices (with payer emails)")
    
    # Parse enrollments
    enrolments = []
    if enrolment_search:
        enrolment_threads = []
        all_messages = enrolment_search if isinstance(enrolment_search, list) else enrolment_search.get("messages", [])
        
        for msg in all_messages:
            if isinstance(msg, dict):
                subject = msg.get("subject", "")
                thread_id = msg.get("threadId", msg.get("id", ""))
                if "enrol" in subject.lower() or "new" in subject.lower():
                    enrolment_threads.append(thread_id)
        
        if enrolment_threads:
            thread_data = _run_gmail_read(enrolment_threads[:10])
            if thread_data:
                threads = thread_data if isinstance(thread_data, list) else thread_data.get("threads", [])
                for thread in threads:
                    messages = thread.get("messages", []) if isinstance(thread, dict) else []
                    for msg in messages:
                        body = msg.get("markdown", msg.get("body", ""))
                        student_email = _extract_enrolment_email(body)
                        if student_email:
                            enrolments.append({
                                "email": student_email,
                                "date": msg.get("date", ""),
                            })
    
    print(f"[Enrollment] Found {len(enrolments)} enrolment confirmations")
    
    # Compare
    enrolled_emails = set(e["email"] for e in enrolments)
    
    matched = []
    unmatched = []
    
    for p in payments:
        if p["email"] in enrolled_emails:
            matched.append(p)
        else:
            unmatched.append(p)
    
    report = {
        "total_payments": len(payments),
        "total_enrolments": len(enrolments),
        "matched": len(matched),
        "unmatched": len(unmatched),
        "matched_students": matched,
        "unmatched_students": unmatched,
        "payments": payments,
        "enrolments": enrolments,
        "checked_at": datetime.now(PHT).isoformat(),
    }
    
    # Save report
    report_file = os.path.join(DATA_DIR, "enrollment_report.json")
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    return report


def format_comparison_telegram(report):
    """Format the comparison report for Telegram."""
    msg = "📊 *Enrollment Comparison Report*\n"
    msg += f"🕐 {report['checked_at'][:16]} PHT\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    
    msg += f"💰 Xendit Payments: {report['total_payments']}\n"
    msg += f"✅ Systeme.io Enrollments: {report['total_enrolments']}\n"
    msg += f"🟢 Matched: {report['matched']}\n"
    msg += f"🔴 Unmatched: {report['unmatched']}\n\n"
    
    if report["unmatched_students"]:
        msg += "⚠️ *UNMATCHED - Paid but NOT Enrolled:*\n\n"
        for i, s in enumerate(report["unmatched_students"], 1):
            msg += f"🔴 *#{i}*\n"
            msg += f"   📧 {s['email']}\n"
            msg += f"   📚 {s['course']}\n"
            msg += f"   💰 {s['amount']}\n\n"
        msg += "⚡ These students need manual enrollment verification!\n"
    else:
        msg += "✅ *All payments matched with enrollments!*\n"
        msg += "Walang student na nag-bayad pero hindi naka-enroll. 🎉\n"
    
    if report["matched_students"]:
        msg += "\n🟢 *Matched Students:*\n"
        for s in report["matched_students"]:
            msg += f"  ✅ {s['email']} - {s['course']}\n"
    
    return msg


def format_comparison_markdown(report):
    """Format the comparison report for markdown."""
    md = "### Enrollment Comparison\n\n"
    md += f"| Metric | Count |\n"
    md += f"|--------|-------|\n"
    md += f"| Xendit Payments | {report['total_payments']} |\n"
    md += f"| Systeme.io Enrollments | {report['total_enrolments']} |\n"
    md += f"| Matched | {report['matched']} |\n"
    md += f"| Unmatched | {report['unmatched']} |\n\n"
    
    if report["unmatched_students"]:
        md += "#### Unmatched Students (Paid but NOT Enrolled)\n\n"
        md += "| Email | Course | Amount |\n"
        md += "|-------|--------|--------|\n"
        for s in report["unmatched_students"]:
            md += f"| {s['email']} | {s['course']} | {s['amount']} |\n"
    
    return md
