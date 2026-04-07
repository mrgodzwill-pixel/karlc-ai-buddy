"""
AI Buddy - Facebook DM Handler
Handles student inquiries via Facebook Messenger with Gemini AI intelligence.
- Asks for email when student has enrollment issues
- Checks Gmail for Xendit payment verification
- Creates tickets and sends urgent Telegram notifications
"""

import json
import os
import re
import subprocess
import time
import requests
from datetime import datetime, timedelta, timezone
# Using Google Gemini for AI

from config import (
    PAGE_ACCESS_TOKEN, BASE_URL, GEMINI_API_KEY, GEMINI_MODEL,
    GEMINI_API_URL, GEMINI_FALLBACK_MODELS, get_gemini_url,
    DATA_DIR, COURSES, GMAIL_ENABLED
)

PHT = timezone(timedelta(hours=8))
CONVERSATIONS_FILE = os.path.join(DATA_DIR, "dm_conversations.json")

# Gemini API helper with retry + fallback
def _call_gemini_simple(system_prompt, user_message):
    """Call Gemini API with fast fallback on error."""
    payload = {
        "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 300}
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
                    print(f"[AI Buddy] Used fallback model: {model}")
                return data["candidates"][0]["content"]["parts"][0]["text"]

            # Any error - immediately try next model
            if "error" in data:
                print(f"[AI Buddy] {model}: {data['error'].get('message', 'error')[:80]}")
                continue

        except Exception as e:
            print(f"[AI Buddy] {model} error: {e}")
            continue

    print("[AI Buddy] All Gemini models failed")
    return None

# DM conversation states
STATE_IDLE = "idle"
STATE_WAITING_EMAIL = "waiting_email"
STATE_CHECKING_PAYMENT = "checking_payment"


def _load_conversations():
    """Load DM conversation states."""
    if os.path.exists(CONVERSATIONS_FILE):
        with open(CONVERSATIONS_FILE) as f:
            return json.load(f)
    return {}


def _save_conversations(convos):
    """Save DM conversation states."""
    with open(CONVERSATIONS_FILE, "w") as f:
        json.dump(convos, f, indent=2, ensure_ascii=False)


def send_fb_message(recipient_id, message_text):
    """Send a message via Facebook Messenger."""
    url = f"{BASE_URL}/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": message_text},
        "messaging_type": "RESPONSE",
        "access_token": PAGE_ACCESS_TOKEN,
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        result = response.json()
        if "error" in result:
            print(f"[AI Buddy] FB send error: {result['error']}")
        return result
    except Exception as e:
        print(f"[AI Buddy] FB send exception: {e}")
        return {"error": str(e)}


def get_sender_name(sender_id):
    """Get the name of a Facebook user by their ID."""
    url = f"{BASE_URL}/{sender_id}"
    params = {"fields": "name", "access_token": PAGE_ACCESS_TOKEN}
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        return data.get("name", "Unknown")
    except:
        return "Unknown"


def is_enrollment_inquiry(message_text):
    """Check if the message is about enrollment/access issues."""
    keywords = [
        "access", "hindi makapasok", "wala pa", "nag bayad", "nag-bayad",
        "nagbayad", "paid", "payment", "enroll", "enrolled", "course",
        "hindi ko makita", "walang access", "di makapasok", "login",
        "hindi makapag login", "saan na", "wala pang", "receive",
        "hindi pa nare", "gcash", "paymaya", "bayad", "binayaran",
    ]
    text_lower = message_text.lower()
    return any(kw in text_lower for kw in keywords)


def is_email(text):
    """Check if text contains an email address."""
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    match = re.search(email_pattern, text)
    return match.group(0) if match else None


def search_xendit_payment(email):
    """Search Gmail for Xendit payment invoice matching the email.
    Uses MCP CLI if available, otherwise returns None.
    """
    try:
        # Try using manus-mcp-cli for Gmail search
        result = subprocess.run(
            ["manus-mcp-cli", "tool", "call", "gmail_search_messages",
             "--server", "gmail",
             "--input", json.dumps({"query": f"from:noreply@xendit.co {email}", "maxResults": 5})],
            capture_output=True, text=True, timeout=30
        )
        
        if result.returncode == 0 and result.stdout:
            # Parse the result
            data = json.loads(result.stdout)
            messages = data.get("messages", [])
            
            for msg in messages:
                subject = msg.get("subject", "")
                if "INVOICE PAID" in subject.upper():
                    # Try to extract course and amount from subject
                    return {
                        "found": True,
                        "email": email,
                        "subject": subject,
                        "date": msg.get("date", ""),
                    }
        
        return {"found": False, "email": email}
        
    except Exception as e:
        print(f"[AI Buddy] Gmail search error: {e}")
        return {"found": False, "email": email, "error": str(e)}


def generate_smart_reply(sender_name, message_text, conversation_state):
    """Generate a smart reply using Gemini AI."""
    system_prompt = """You are Karl C's AI assistant on Facebook Messenger. You help students with enrollment and course access issues.

Rules:
- Reply in Taglish (mix of Tagalog and English)
- Be friendly and helpful
- Keep replies SHORT (max 2-3 sentences)
- If student has access/payment issues, ask for their EMAIL address
- If student asks about courses/pricing, provide info from the course catalog
- Always be polite and professional

Course Catalog:
- MikroTik Basic (QuickStart) - PHP 799 - karlcomboy.com/checkout-quickstart
- MikroTik Dual-ISP - PHP 1,999 - karlcomboy.com/checkout-dual-isp
- MikroTik Hybrid - PHP 1,499 - karlcomboy.com/checkout-hybrid-access
- MikroTik Traffic Control - PHP 749 - karlcomboy.com/checkout-traffic-control
- MikroTik 10G Core Part 1 - PHP 1,749 - karlcomboy.com/checkout-core10g
- MikroTik 10G Core Part 2 (OSPF) - PHP 977 - karlcomboy.com/checkout-ospf
- Hybrid FTTH (PLC + FBT) - PHP 499 - karlcomboy.com/checkout-ftth
- DIY Hybrid Solar - PHP 997 - karlcomboy.com/checkout-solar

Student Portal Login:
1. Go to karlcomboy.com
2. Click 'Student Login'
3. Use email as username
4. Click 'Forgot Password' if needed
"""

    try:
        return _call_gemini_simple(system_prompt, f"[Sender: {sender_name}] [State: {conversation_state}] {message_text}")
    except Exception as e:
        print(f"[AI Buddy] Gemini error: {e}")
        return None


def handle_incoming_dm(sender_id, message_text, sender_name=None):
    """Main handler for incoming Facebook DMs."""
    from telegram_bot import send_message as send_telegram
    from ticket_system import create_dm_ticket, create_no_payment_ticket

    if not sender_name:
        sender_name = get_sender_name(sender_id)

    convos = _load_conversations()
    sender_key = str(sender_id)

    # Get or create conversation state
    if sender_key not in convos:
        convos[sender_key] = {
            "state": STATE_IDLE,
            "name": sender_name,
            "messages": [],
        }

    convo = convos[sender_key]
    convo["messages"].append({
        "text": message_text,
        "time": datetime.now(PHT).isoformat(),
        "direction": "in",
    })

    # Check if VPN-related message (smart detection)
    msg_lower = message_text.lower()

    # Strong VPN indicators - if any of these are present, it's definitely VPN
    vpn_strong = ["vpn", "karlcomvpn", "wireguard", "remote access",
                  "vpn.karlc", "karlc.cloud", "coins", "coin",
                  "top up", "topup", "top-up", "pag top"]
    # Weak indicators - only VPN if no course context
    vpn_weak = ["magkano"]
    # Course/enrollment indicators - if present, it's NOT VPN
    course_indicators = ["course", "enroll", "access", "portal", "student",
                         "login", "mikrotik", "ftth", "solar", "ospf",
                         "quickstart", "dual-isp", "hybrid", "traffic",
                         "10g", "karlcomboy", "systeme", "xendit",
                         "hindi makapasok", "wala pa", "nag bayad",
                         "nagbayad", "nag-bayad", "binayaran"]

    has_strong_vpn = any(kw in msg_lower for kw in vpn_strong)
    has_weak_vpn = any(kw in msg_lower for kw in vpn_weak)
    has_course = any(kw in msg_lower for kw in course_indicators)

    # VPN inquiry = has strong VPN keyword, OR has weak keyword without course context
    is_vpn_inquiry = has_strong_vpn or (has_weak_vpn and not has_course)

    # Send Telegram notification for every DM
    if is_vpn_inquiry:
        notif = f"🌐 *VPN INQUIRY - New DM*\n"
        notif += f"━━━━━━━━━━━━━━━━━━\n"
        notif += f"👤 From: {sender_name}\n"
        notif += f"📝 Message: {message_text[:200]}\n"
        notif += f"🕐 {datetime.now(PHT).strftime('%H:%M:%S')} PHT\n\n"
        notif += f"💡 *Suggested Reply:*\n"
        notif += f"Hi {sender_name}! 👋 Para sa VPN coin top-up, pwede ka magbayad via GCash:\n"
        notif += f"📱 09495446516 (Karl Andrew C.)\n\n"
        notif += f"Pricing:\n"
        notif += f"• 50 coins - ₱50 (1 device/1 mo)\n"
        notif += f"• 150 coins - ₱150\n"
        notif += f"• 300 coins - ₱300 ⭐\n"
        notif += f"• 600 coins - ₱600\n\n"
        notif += f"Send mo lang ang GCash receipt after payment! 😊"
    else:
        notif = f"💬 *New Facebook DM*\n"
        notif += f"👤 From: {sender_name}\n"
        notif += f"📝 Message: {message_text[:200]}\n"
        notif += f"🕐 {datetime.now(PHT).strftime('%H:%M:%S')} PHT"
    send_telegram(notif)

    # Check if message contains an email
    email = is_email(message_text)

    if email:
        # Student provided email - check payment
        convo["state"] = STATE_CHECKING_PAYMENT
        convo["email"] = email

        # Send acknowledgment
        send_fb_message(sender_id, f"Salamat! Checking ko ang payment record para sa {email}... Sandali lang po! 🔍")

        # Search for Xendit payment
        payment = search_xendit_payment(email)

        if payment and payment.get("found"):
            # Payment found!
            course_info = payment.get("subject", "Course")
            
            send_fb_message(
                sender_id,
                f"✅ Nakita ko na ang payment mo! Na-verify na ang bayad mo.\n\n"
                f"I-forward ko na ito kay Sir Karl para ma-resolve agad ang access mo. "
                f"Mag-aantay lang po ng konti! 😊"
            )

            # Create ticket
            ticket = create_dm_ticket(
                student_name=sender_name,
                student_email=email,
                course_title=course_info,
                price="See invoice",
                fb_sender_id=sender_id,
            )

            # Send URGENT Telegram notification
            urgent = f"🚨 *URGENT - Student Access Issue*\n"
            urgent += f"━━━━━━━━━━━━━━━━━━\n"
            urgent += f"👤 Student: {sender_name}\n"
            urgent += f"📧 Email: {email}\n"
            urgent += f"📚 Invoice: {course_info[:60]}\n"
            urgent += f"✅ Payment: VERIFIED (Xendit)\n"
            if ticket:
                urgent += f"🎫 Ticket: #{ticket['id']}\n"
            urgent += f"\n⚡ Action needed: Verify enrollment sa systeme.io\n"
            urgent += f"━━━━━━━━━━━━━━━━━━\n"
            urgent += f"✅ /done {ticket['id'] if ticket else '?'} - kapag resolved na"
            send_telegram(urgent)

            convo["state"] = STATE_IDLE

        else:
            # No payment found
            send_fb_message(
                sender_id,
                f"Hmm, hindi ko makita ang payment record para sa {email}. 🤔\n\n"
                f"Possible reasons:\n"
                f"• Baka ibang email ang ginamit mo sa payment\n"
                f"• Baka hindi pa na-process ang payment\n\n"
                f"Pwede mo i-try ang ibang email, o mag-message ka ulit with your payment screenshot para ma-verify namin. 😊"
            )

            # Create no-payment ticket
            ticket = create_no_payment_ticket(
                student_name=sender_name,
                student_email=email,
                fb_sender_id=sender_id,
            )

            # Telegram notification
            notif = f"⚠️ *Student Payment Not Found*\n"
            notif += f"━━━━━━━━━━━━━━━━━━\n"
            notif += f"👤 Student: {sender_name}\n"
            notif += f"📧 Email: {email}\n"
            notif += f"🔴 Payment: NOT FOUND in Xendit\n"
            if ticket:
                notif += f"🎫 Ticket: #{ticket['id']}\n"
            notif += f"\n⚠️ May need manual check"
            send_telegram(notif)

            convo["state"] = STATE_IDLE

    elif is_vpn_inquiry:
        # VPN inquiry - auto-reply with GCash payment info
        vpn_reply = (
            f"Hi {sender_name}! \U0001f44b\n\n"
            f"Para sa KarlComVPN coin top-up, pwede ka magbayad via GCash:\n\n"
            f"\U0001f4f1 GCash: 09495446516\n"
            f"\U0001f464 Name: Karl Andrew C.\n\n"
            f"\U0001f4cb Pricing:\n"
            f"\u2022 50 coins - \u20b150 (1 device/1 month)\n"
            f"\u2022 150 coins - \u20b1150 (3 device-months)\n"
            f"\u2022 300 coins - \u20b1300 (6 device-months) \u2b50\n"
            f"\u2022 600 coins - \u20b1600 (12 device-months)\n"
            f"\u2022 1200 coins - \u20b11200 (24 device-months)\n\n"
            f"\U0001f4f8 After payment, send the GCash receipt/screenshot here para ma-top up agad ang coins mo!\n\n"
            f"\U0001f517 Website: vpn.karlc.cloud\n"
            f"Salamat po! \U0001f60a"
        )
        send_fb_message(sender_id, vpn_reply)
        convo["messages"].append({
            "text": vpn_reply,
            "time": datetime.now(PHT).isoformat(),
            "direction": "out",
        })

    elif is_enrollment_inquiry(message_text):
        # Student has enrollment/access issue - ask for email
        convo["state"] = STATE_WAITING_EMAIL

        reply = (
            f"Hi {sender_name}! 👋\n\n"
            f"Para ma-check ko ang payment at enrollment status mo, "
            f"pakibigay po ang email address na ginamit mo nung nag-enroll ka. 📧\n\n"
            f"(Yung email na ni-type mo sa Xendit payment page)"
        )
        send_fb_message(sender_id, reply)

    elif convo["state"] == STATE_WAITING_EMAIL:
        # We're waiting for email but got something else
        reply = generate_smart_reply(sender_name, message_text, "waiting_for_email")
        if reply:
            send_fb_message(sender_id, reply)
        else:
            send_fb_message(
                sender_id,
                f"Pakibigay po ang email address mo para ma-check ko ang payment status mo. 📧"
            )

    else:
        # General message - use Gemini for smart reply
        reply = generate_smart_reply(sender_name, message_text, "general")
        if reply:
            send_fb_message(sender_id, reply)
            convo["messages"].append({
                "text": reply,
                "time": datetime.now(PHT).isoformat(),
                "direction": "out",
            })

    _save_conversations(convos)
