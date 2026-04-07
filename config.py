"""
Configuration for Karl C AI Buddy
All sensitive values are loaded from environment variables for security.
"""

import os

# === Facebook Page Config ===
PAGE_ID = os.environ.get("FB_PAGE_ID", "110704956970982")
PAGE_NAME = os.environ.get("FB_PAGE_NAME", "Karl C")
PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "")
USER_ACCESS_TOKEN = os.environ.get("FB_USER_ACCESS_TOKEN", "")
FB_APP_SECRET = os.environ.get("FB_APP_SECRET", "")
WEBHOOK_VERIFY_TOKEN = os.environ.get("FB_VERIFY_TOKEN", "karlc_agent_2026")
BASE_URL = "https://graph.facebook.com/v19.0"

# === Telegram Config ===
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# === Google Gemini Config ===
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_FALLBACK_MODELS = ["gemini-3-flash-preview", "gemini-2.0-flash-lite", "gemini-2.0-flash", "gemini-2.5-flash"]
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

def get_gemini_url(model=None):
    """Get Gemini API URL for a specific model."""
    m = model or GEMINI_MODEL
    return f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={GEMINI_API_KEY}"

# === Gmail MCP Config (for enrollment checker) ===
# Gmail access is via Manus MCP - for external deployment we use IMAP or API
GMAIL_ENABLED = os.environ.get("GMAIL_ENABLED", "false").lower() == "true"

# === Directories ===
DATA_DIR = os.environ.get("DATA_DIR", "/home/ubuntu/karlc-ai-buddy/data")
REPORT_DIR = os.environ.get("REPORT_DIR", "/home/ubuntu/karlc-ai-buddy/data/reports")

# Ensure directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

# === Course Catalog ===
COURSES = {
    "mikrotik_basic": {
        "name": "MikroTik Basic (QuickStart)",
        "price": 799,
        "url": "https://www.karlcomboy.com/checkout-quickstart",
        "keywords": ["basic", "quickstart", "beginner", "mikrotik basic"],
    },
    "mikrotik_dual_isp": {
        "name": "MikroTik Dual-ISP",
        "price": 1999,
        "url": "https://www.karlcomboy.com/checkout-dual-isp",
        "keywords": ["dual isp", "dual-isp", "load balance", "failover", "2 isp"],
    },
    "mikrotik_hybrid": {
        "name": "MikroTik Hybrid",
        "price": 1499,
        "url": "https://www.karlcomboy.com/checkout-hybrid-access",
        "keywords": ["hybrid access", "mikrotik hybrid"],
    },
    "mikrotik_traffic": {
        "name": "MikroTik Traffic Control",
        "price": 749,
        "url": "https://www.karlcomboy.com/checkout-traffic-control",
        "keywords": ["traffic", "traffic control", "bandwidth", "queue"],
    },
    "mikrotik_10g": {
        "name": "MikroTik 10G Core Part 1",
        "price": 1749,
        "url": "https://www.karlcomboy.com/checkout-core10g",
        "keywords": ["10g", "10g core", "core 10g"],
    },
    "mikrotik_ospf": {
        "name": "MikroTik 10G Core Part 2 (OSPF)",
        "price": 977,
        "url": "https://www.karlcomboy.com/checkout-ospf",
        "keywords": ["ospf", "routing", "advance routing", "ospf setup"],
    },
    "ftth": {
        "name": "Hybrid FTTH (PLC + FBT)",
        "price": 499,
        "url": "https://www.karlcomboy.com/checkout-ftth",
        "keywords": ["ftth", "fiber", "splitter", "plc", "fbt"],
    },
    "solar": {
        "name": "DIY Hybrid Solar",
        "price": 997,
        "url": "https://www.karlcomboy.com/checkout-solar",
        "keywords": ["solar", "hybrid solar", "diy solar"],
    },
}

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

KEYWORD_REPLIES = {
    "magkano": _build_price_list(),
    "how much": _build_price_list(),
    "price": _build_price_list(),
    "presyo": _build_price_list(),
    "enroll": _build_enroll_list(),
    "interested": _build_enroll_list(),
    "link": _build_enroll_list(),
    "course": _build_enroll_list(),
    "login": "Para mag-login sa Student Portal:\n\n1️⃣ Pumunta sa karlcomboy.com\n2️⃣ I-click ang 'Student Login'\n3️⃣ Gamitin ang email mo bilang username\n4️⃣ I-click ang 'Forgot Password' kung nakalimutan mo ang password\n\nKung may problema pa rin, mag-message ka lang! 😊",
    "password": "Para ma-reset ang password mo:\n\n1️⃣ Pumunta sa karlcomboy.com\n2️⃣ I-click ang 'Student Login'\n3️⃣ I-click ang 'Forgot Password'\n4️⃣ I-enter ang email mo at sundin ang instructions\n\nKung hindi gumana, mag-message ka lang! 😊",
    "portal": "Para mag-access sa Student Portal:\n\n1️⃣ Pumunta sa karlcomboy.com\n2️⃣ I-click ang 'Student Login'\n3️⃣ Gamitin ang email mo bilang username\n4️⃣ I-click ang 'Forgot Password' kung nakalimutan mo ang password\n\nKung may problema pa rin, mag-message ka lang! 😊",
    "hindi makapasok": "Para mag-login sa Student Portal:\n\n1️⃣ Pumunta sa karlcomboy.com\n2️⃣ I-click ang 'Student Login'\n3️⃣ Gamitin ang email mo bilang username\n4️⃣ I-click ang 'Forgot Password' kung nakalimutan mo ang password\n\nKung may problema pa rin, mag-message ka lang! 😊",
}

# Add course-specific keyword replies
for course_key, course in COURSES.items():
    for kw in course["keywords"]:
        if kw not in KEYWORD_REPLIES:
            KEYWORD_REPLIES[kw] = (
                f"📚 {course['name']}\n"
                f"💰 Price: PHP {course['price']:,}\n"
                f"🔗 Enroll here: {course['url']}\n\n"
                f"Para mag-enroll, i-click lang po ang link! Salamat! 😊"
            )

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

Your capabilities:
- Check Facebook page comments and DMs
- View and manage student tickets
- Run enrollment comparison (Xendit payments vs systeme.io enrollments)
- Approve/skip suggested comment replies
- Generate reports
- Answer questions about Karl's business

When Karl asks you something:
- Be concise but helpful
- If he asks to check something, do it and report back
- If he asks about a student, search the tickets
- Always be proactive with suggestions
"""
