"""
Configuration for Karl C AI Buddy
All sensitive values are loaded from environment variables for security.
"""

import os
import json

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# === Facebook Page Config ===
PAGE_ID = os.environ.get("FB_PAGE_ID", "")
PAGE_NAME = os.environ.get("FB_PAGE_NAME", "Karl C")
PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "")
FB_APP_SECRET = os.environ.get("FB_APP_SECRET", "")
WEBHOOK_VERIFY_TOKEN = os.environ.get("FB_VERIFY_TOKEN", "")
BASE_URL = "https://graph.facebook.com/v19.0"

# === Telegram Config ===
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# === Google Gemini Config ===
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_FALLBACK_MODELS = ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-2.5-flash-lite"]

def get_gemini_url(model=None):
    """Build the Gemini API URL for a given model. Resolves the API key lazily
    so a late-loaded env var still works."""
    m = model or GEMINI_MODEL
    key = os.environ.get("GEMINI_API_KEY", GEMINI_API_KEY)
    return f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={key}"

# === Owner / operator email (used to filter system emails out of enrollment scans) ===
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "").lower()

# === Systeme.io sender address ===
# This is the "From:" address Systeme uses when sending enrollment / verification
# emails. The enrollment checker searches messages from this sender and extracts
# the student's email from the body, then matches against Xendit payer emails.
SYSTEME_SENDER = os.environ.get("SYSTEME_SENDER", "course@karlcomboy.com").lower()
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "course@karlcomboy.com").lower()
SYSTEME_WEBHOOK_SECRET = os.environ.get("SYSTEME_WEBHOOK_SECRET", "")
SYSTEME_AUTOMATION_TOKEN = os.environ.get("SYSTEME_AUTOMATION_TOKEN", "")
SYSTEME_API_KEY = os.environ.get("SYSTEME_API_KEY", "")
SYSTEME_API_BASE_URL = os.environ.get("SYSTEME_API_BASE_URL", "https://api.systeme.io/api")
SYSTEME_STUDENTS_BASELINE_CSV_URL = os.environ.get("SYSTEME_STUDENTS_BASELINE_CSV_URL", "").strip()
SYSTEME_STUDENTS_BASELINE_LOCAL_CSV = os.environ.get("SYSTEME_STUDENTS_BASELINE_LOCAL_CSV", "").strip()
SYSTEME_STUDENTS_SHEET_ID = os.environ.get("SYSTEME_STUDENTS_SHEET_ID", "").strip()
SYSTEME_STUDENTS_SHEET_NAME = os.environ.get("SYSTEME_STUDENTS_SHEET_NAME", "Sheet1").strip() or "Sheet1"
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON_B64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_B64", "").strip()
SYSTEME_TAG_MIKROTIK_BASIC = os.environ.get("SYSTEME_TAG_MIKROTIK_BASIC", "QUICKSTART_PAID")
SYSTEME_TAG_MIKROTIK_DUAL_ISP = os.environ.get("SYSTEME_TAG_MIKROTIK_DUAL_ISP", "DUAL_PAID")
SYSTEME_TAG_MIKROTIK_HYBRID = os.environ.get("SYSTEME_TAG_MIKROTIK_HYBRID", "HYBRID_PAID")
SYSTEME_TAG_MIKROTIK_TRAFFIC = os.environ.get("SYSTEME_TAG_MIKROTIK_TRAFFIC", "TRAFFIC_PAID")
SYSTEME_TAG_MIKROTIK_10G = os.environ.get("SYSTEME_TAG_MIKROTIK_10G", "10G_PAID")
SYSTEME_TAG_MIKROTIK_OSPF = os.environ.get("SYSTEME_TAG_MIKROTIK_OSPF", "OSPF_PAID")
SYSTEME_TAG_FTTH = os.environ.get("SYSTEME_TAG_FTTH", "FTTH_PAID")
SYSTEME_TAG_SOLAR = os.environ.get("SYSTEME_TAG_SOLAR", "SOLAR_PAID")
SYSTEME_TAG_PISOWIFI = os.environ.get("SYSTEME_TAG_PISOWIFI", "PISOWIFI_PAID")
SYSTEME_TAG_BUNDLE4 = os.environ.get("SYSTEME_TAG_BUNDLE4", "BUNDLE4_PAID")
SYSTEME_SHEET_EXCLUDED_TAGS = {
    str(tag).strip().lower()
    for tag in os.environ.get("SYSTEME_SHEET_EXCLUDED_TAGS", "500off_for_verification").split(",")
    if str(tag).strip()
}

# === Gmail IMAP Config (for enrollment checker & Xendit lookups) ===
# See gmail_imap.py. Enable by setting GMAIL_USER and GMAIL_APP_PASSWORD
# (a 16-character Google App Password, NOT your regular account password).
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_ENABLED = bool(GMAIL_USER and GMAIL_APP_PASSWORD)

# === Xendit Config ===
XENDIT_SECRET_KEY = os.environ.get("XENDIT_SECRET_KEY", "")
XENDIT_API_BASE_URL = os.environ.get("XENDIT_API_BASE_URL", "https://api.xendit.co")
XENDIT_CUSTOMER_API_VERSION = os.environ.get("XENDIT_CUSTOMER_API_VERSION", "2020-10-31")
XENDIT_PAYMENT_API_VERSION = os.environ.get("XENDIT_PAYMENT_API_VERSION", "2024-11-11")
XENDIT_WEBHOOK_TOKEN = os.environ.get("XENDIT_WEBHOOK_TOKEN", "")
XENDIT_INVOICE_WEBHOOK_TOKEN = os.environ.get("XENDIT_INVOICE_WEBHOOK_TOKEN", XENDIT_WEBHOOK_TOKEN)
XENDIT_PAYMENT_WEBHOOK_TOKEN = os.environ.get("XENDIT_PAYMENT_WEBHOOK_TOKEN", XENDIT_WEBHOOK_TOKEN)

# === Semaphore SMS Config (for manual follow-ups on unresolved tickets) ===
SEMAPHORE_API_KEY = os.environ.get("SEMAPHORE_API_KEY", "")
SEMAPHORE_SENDER_NAME = os.environ.get("SEMAPHORE_SENDER_NAME", "")
SEMAPHORE_ENABLED = bool(SEMAPHORE_API_KEY)

# === Directories ===
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(PROJECT_DIR, "data")
REPORT_DIR = os.environ.get("REPORT_DIR") or os.path.join(DATA_DIR, "reports")

# Ensure directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)


def get_google_service_account_info():
    raw = GOOGLE_SERVICE_ACCOUNT_JSON.strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    raw_b64 = GOOGLE_SERVICE_ACCOUNT_JSON_B64.strip()
    if raw_b64:
        try:
            import base64

            return json.loads(base64.b64decode(raw_b64).decode("utf-8"))
        except Exception:
            return None

    return None

# === Course Catalog ===
COURSES = {
    "mikrotik_basic": {
        "name": "MikroTik Basic (QuickStart)",
        "price": 799,
        "url": "https://www.karlcomboy.com/checkout-quickstart",
        "keywords": [
            "basic",
            "quickstart",
            "beginner",
            "mikrotik basic",
            "configure from scratch",
            "routeros from scratch",
            "from scratch",
        ],
    },
    "mikrotik_dual_isp": {
        "name": "MikroTik Dual-ISP",
        "price": 1999,
        "url": "https://www.karlcomboy.com/checkout-dual-isp",
        "keywords": ["dual isp", "dual-isp", "load balance", "failover", "2 isp", "auto fail-over", "load balancing"],
    },
    "mikrotik_hybrid": {
        "name": "MikroTik Hybrid",
        "price": 1499,
        "url": "https://www.karlcomboy.com/checkout-hybrid-access",
        "keywords": ["hybrid access", "mikrotik hybrid", "ipoe", "pppoe", "hybrid access combo"],
    },
    "mikrotik_traffic": {
        "name": "MikroTik Traffic Control",
        "price": 749,
        "url": "https://www.karlcomboy.com/checkout-traffic-control",
        "keywords": ["traffic", "traffic control", "bandwidth", "queue", "traffic control basics"],
    },
    "mikrotik_10g": {
        "name": "MikroTik 10G Core Part 1",
        "price": 1749,
        "url": "https://www.karlcomboy.com/checkout-core10g",
        "keywords": ["10g", "10g core", "core 10g", "isp aggregator", "10g core part 1"],
    },
    "mikrotik_ospf": {
        "name": "MikroTik 10G Core Part 2 (OSPF)",
        "price": 977,
        "url": "https://www.karlcomboy.com/checkout-ospf",
        "keywords": ["ospf", "routing", "advance routing", "advanced routing", "ospf setup", "10g core part 2"],
    },
    "ftth": {
        "name": "Hybrid FTTH (PLC + FBT)",
        "price": 499,
        "url": "https://www.karlcomboy.com/checkout-ftth",
        "keywords": ["ftth", "fiber", "splitter", "plc", "fbt", "budget-friendly ftth design", "plc & fbt combo"],
    },
    "solar": {
        "name": "DIY Hybrid Solar",
        "price": 997,
        "url": "https://www.karlcomboy.com/checkout-solar",
        "keywords": ["solar", "hybrid solar", "diy solar", "diy hybrid solar setup"],
    },
}

# === VPN Service Config ===
VPN_GCASH_NUMBER = "09495446516"
VPN_GCASH_NAME = "Karl Andrew C."
VPN_WEBSITE = "vpn.karlc.cloud"
VPN_SERVICE_NAME = "KarlComVPN"
VPN_PRICING = {
    "50_coins": {"coins": 50, "price": 50, "description": "1 device for 1 month"},
    "150_coins": {"coins": 150, "price": 150, "description": "3 device-months"},
    "300_coins": {"coins": 300, "price": 300, "description": "6 device-months (Most Popular)"},
    "600_coins": {"coins": 600, "price": 600, "description": "12 device-months"},
    "1200_coins": {"coins": 1200, "price": 1200, "description": "24 device-months"},
}

def _build_vpn_payment_reply():
    return (
        "🌐 KarlComVPN - Coin Top Up\n\n"
        "📋 Pricing:\n"
        "• 50 coins - ₱50 (1 device/1 month)\n"
        "• 150 coins - ₱150 (3 device-months)\n"
        "• 300 coins - ₱300 (6 device-months) ⭐\n"
        "• 600 coins - ₱600 (12 device-months)\n"
        "• 1200 coins - ₱1200 (24 device-months)\n\n"
        "💳 Payment via GCash:\n"
        f"📱 {VPN_GCASH_NUMBER}\n"
        f"👤 {VPN_GCASH_NAME}\n\n"
        "📸 After payment, send the GCash receipt/screenshot here para ma-top up agad ang coins mo!\n\n"
        f"🔗 Website: {VPN_WEBSITE}\n"
        "Salamat po! 😊"
    )

# === Keyword Auto-Reply Config ===
def _build_price_list():
    lines = ["Ito po ang mga available courses namin:\n"]
    for c in COURSES.values():
        lines.append(f"📚 {c['name']} - PHP {c['price']:,}")
        lines.append(f"   🔗 {c['url']}\n")
    lines.append("Para mag-enroll, i-click lang po ang link ng gusto niyong course! 😊")
    return "\n".join(lines)

def _build_enroll_list():
    lines = ["Para mag-enroll, pumili po ng course:\n"]
    for c in COURSES.values():
        lines.append(f"📚 {c['name']} - PHP {c['price']:,}")
        lines.append(f"   🔗 {c['url']}\n")
    lines.append("I-click lang po ang link at sundin ang enrollment steps. Salamat! 😊")
    return "\n".join(lines)

# DM Auto-Reply Keywords
# PRIORITY ORDER: Specific course keywords first, then VPN, then generic/support
# This ensures "how much solar" matches "solar" (specific) not "how much" (generic)

# --- TIER 1: Course-specific keywords (checked FIRST) ---
# These are auto-generated from COURSES config below

# --- TIER 2: VPN-specific keywords (checked SECOND) ---
# These are auto-generated from VPN config below

# --- TIER 3: Generic/support keywords (checked LAST) ---
_GENERIC_REPLIES = {
    "hindi makapasok": "Para mag-login sa Student Portal:\n\n1️⃣ Pumunta sa karlcomboy.com\n2️⃣ I-click ang 'Student Login'\n3️⃣ Gamitin ang email mo bilang username\n4️⃣ I-click ang 'Forgot Password' kung nakalimutan mo ang password\n\nKung may problema pa rin, mag-message ka lang! 😊",
}

# Build the final KEYWORD_REPLIES dict with proper priority
# TIER 1: Course-specific keywords (checked FIRST - highest priority)
KEYWORD_REPLIES = {}
for course_key, course in COURSES.items():
    for kw in course["keywords"]:
        if kw not in KEYWORD_REPLIES:
            KEYWORD_REPLIES[kw] = (
                f"\U0001f4da {course['name']}\n"
                f"\U0001f4b0 Price: PHP {course['price']:,}\n"
                f"\U0001f517 Enroll here: {course['url']}\n\n"
                f"Para mag-enroll, i-click lang po ang link! Salamat! \U0001f60a"
            )

# TIER 2: VPN-specific keywords
_vpn_keywords = [
    "vpn", "karlcomvpn", "wireguard", "remote access",
    "vpn subscription", "vpn coins", "vpn coin",
    "vpn top up", "vpn topup", "vpn load", "vpn gcash",
    "device subscription", "vpn.karlc", "karlc.cloud",
    "coins", "coin", "top up", "topup", "top-up", "pag top",
]
for _kw in _vpn_keywords:
    if _kw not in KEYWORD_REPLIES:
        KEYWORD_REPLIES[_kw] = _build_vpn_payment_reply()

# TIER 3: Generic/support keywords (checked LAST)
for _kw, _reply in _GENERIC_REPLIES.items():
    if _kw not in KEYWORD_REPLIES:
        KEYWORD_REPLIES[_kw] = _reply

# === AI Buddy System Prompt ===
AI_BUDDY_SYSTEM_PROMPT = """You are Karl C's AI Buddy - a friendly, helpful assistant for Karl's Facebook Page "Karl C" which is an educational platform for MikroTik, networking, fiber optics, and solar courses.

Your personality:
- Friendly, casual, mix of Tagalog and English (Taglish)
- Professional but approachable
- You call Karl "Boss" or "Bro"
- You're knowledgeable about Karl's courses and business

Karl's Business:
- Website: karlcomboy.com
- Courses: MikroTik Basic (PHP 799), Dual-ISP (PHP 1,999), Hybrid (PHP 1,499), Traffic Control (PHP 749), 10G Core Part 1 (PHP 1,749), OSPF Part 2 (PHP 977), FTTH (PHP 499), DIY Solar (PHP 997)
- Students enroll via systeme.io, pay via Xendit
- Common issue: Students pay but forget to verify email, so they don't receive course access

KarlComVPN Service (vpn.karlc.cloud):
- WireGuard VPN for remote access to MikroTik routers & devices
- Coin-based pricing: 50 coins = ₱50 (1 device/1 month), 150 = ₱150, 300 = ₱300, 600 = ₱600, 1200 = ₱1200
- Yearly plan: 420 coins for 12 months (₱35/mo)
- 150 free coins on signup
- Payment is MANUAL via GCash: 09495446516 (Karl Andrew C.)
- IMPORTANT: When someone asks about VPN payment/top-up/coins, ALWAYS provide the GCash number
- Monitor VPN-related DMs and comments closely - these are paying subscribers

Your capabilities:
- Check Facebook page comments and DMs (real-time data is injected when Karl asks)
- Check recent emails, payments, and enrollment status
- Monitor VPN subscription inquiries and provide GCash payment details
- View and manage student tickets
- Run enrollment comparison (Xendit payments vs systeme.io enrollments)
- Approve/skip suggested comment replies
- Generate reports
- Answer questions about Karl's business

When Karl asks you something:
- Be concise but helpful
- If data is provided in [BRACKETS], use it to give accurate answers - this is REAL DATA from Facebook and email
- Summarize the data clearly - counts, names, key details
- If he asks to check something, report what the data shows
- If he asks about a student, search the tickets
- Always be proactive with suggestions
- If no data is found for a time period, let Karl know honestly
"""
