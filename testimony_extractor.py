"""
Extract likely testimonial candidates from Facebook comments and DMs.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

from config import DATA_DIR
from fb_agent import get_page_posts, get_post_comments

PHT = timezone(timedelta(hours=8))
MESSAGES_FILE = os.path.join(DATA_DIR, "messages.json")
TESTIMONY_FILE = os.path.join(DATA_DIR, "testimony_candidates.json")

_POSITIVE_PATTERNS = {
    "salamat": 3,
    "thank you": 3,
    "thanks": 2,
    "worth it": 4,
    "sulit": 4,
    "solid": 3,
    "galing": 3,
    "helpful": 4,
    "useful": 4,
    "very useful": 5,
    "recommended": 4,
    "legit": 4,
    "laking tulong": 5,
    "nakatulong": 5,
    "natuto": 4,
    "natutunan": 4,
    "learned a lot": 5,
    "learned alot": 5,
    "grabe": 2,
    "ang ganda": 3,
    "okay na": 3,
    "ok na": 3,
    "gumana": 4,
    "working na": 4,
    "solve": 2,
    "solved": 4,
    "success": 3,
    "successful": 3,
    "ayos": 3,
    "excellent": 4,
    "best": 3,
}

_BENEFIT_PATTERNS = {
    "setup": 2,
    "configured": 3,
    "router": 1,
    "mikrotik": 1,
    "traffic": 1,
    "dual isp": 2,
    "ospf": 2,
    "ftth": 2,
    "solar": 2,
    "pisowifi": 2,
    "network": 1,
    "deployment": 2,
}

_NEGATIVE_PATTERNS = {
    "scam": 8,
    "refund": 5,
    "error": 3,
    "issue": 3,
    "problem": 3,
    "not working": 5,
    "hindi gumana": 5,
    "di gumana": 5,
    "wala pa": 2,
    "hindi pa": 2,
    "not yet": 2,
    "bad": 3,
    "pangit": 4,
    "failed": 4,
    "ayaw": 3,
}


def _now():
    return datetime.now(PHT)


def _parse_timestamp(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=PHT)
    return parsed.astimezone(PHT)


def _normalize_text(text):
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _clean_snippet(text, limit=240):
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _score_text(text):
    normalized = _normalize_text(text)
    if len(normalized) < 12:
        return 0

    score = 0
    for phrase, value in _POSITIVE_PATTERNS.items():
        if phrase in normalized:
            score += value
    for phrase, value in _BENEFIT_PATTERNS.items():
        if phrase in normalized:
            score += value
    for phrase, value in _NEGATIVE_PATTERNS.items():
        if phrase in normalized:
            score -= value

    if "!" in text:
        score += min(text.count("!"), 3)

    return score


def _is_testimonial_candidate(text):
    score = _score_text(text)
    normalized = _normalize_text(text)
    strong_positive = any(phrase in normalized for phrase in ("worth it", "sulit", "laking tulong", "nakatulong", "helpful", "recommended", "legit"))
    return score >= 5 and (strong_positive or len(normalized) >= 24), score


def _load_dms(days_back=30):
    if not os.path.exists(MESSAGES_FILE):
        return []
    with open(MESSAGES_FILE) as handle:
        messages = json.load(handle)

    cutoff = _now() - timedelta(days=days_back)
    rows = []
    for item in messages:
        timestamp = _parse_timestamp(item.get("timestamp", ""))
        if timestamp is None or timestamp < cutoff:
            continue
        rows.append(
            {
                "source": "DM",
                "sender_name": item.get("sender_name", "Unknown"),
                "text": item.get("text", ""),
                "timestamp": timestamp.isoformat(),
                "context": "",
                "id": item.get("mid", ""),
            }
        )
    return rows


def _load_comments(days_back=30, post_limit=25, comment_limit=100):
    cutoff = _now().astimezone(timezone.utc) - timedelta(days=days_back)
    rows = []
    for post in get_page_posts(limit=post_limit):
        post_id = post.get("id", "")
        post_preview = _clean_snippet(post.get("message", ""), limit=80)
        comments = get_post_comments(post_id, limit=comment_limit)
        for comment in comments:
            created = _parse_timestamp(comment.get("created_time", ""))
            if created is None:
                continue
            if created.astimezone(timezone.utc) < cutoff:
                continue
            rows.append(
                {
                    "source": "Comment",
                    "sender_name": (comment.get("from") or {}).get("name", "Unknown"),
                    "text": comment.get("message", ""),
                    "timestamp": created.isoformat(),
                    "context": post_preview,
                    "id": comment.get("id", ""),
                }
            )
    return rows


def extract_testimony_candidates(days_back=30, limit=12):
    checked_at = _now().isoformat()
    raw_items = _load_comments(days_back=days_back) + _load_dms(days_back=days_back)
    candidates = []
    seen = set()

    for item in raw_items:
        is_candidate, score = _is_testimonial_candidate(item["text"])
        if not is_candidate:
            continue
        dedupe_key = (
            item["source"],
            str(item.get("sender_name") or "").strip().lower(),
            _normalize_text(item.get("text", "")),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append(
            {
                "source": item["source"],
                "sender_name": item["sender_name"],
                "text": _clean_snippet(item["text"]),
                "timestamp": item["timestamp"],
                "context": item.get("context", ""),
                "score": score,
                "id": item.get("id", ""),
            }
        )

    candidates.sort(
        key=lambda row: (
            row.get("score", 0),
            _parse_timestamp(row.get("timestamp", "")) or datetime.min.replace(tzinfo=PHT),
        ),
        reverse=True,
    )
    candidates = candidates[:limit]

    payload = {
        "checked_at": checked_at,
        "days_back": days_back,
        "count": len(candidates),
        "candidates": candidates,
    }
    with open(TESTIMONY_FILE, "w") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    return payload


def format_testimony_candidates_telegram(days_back=30, limit=8):
    result = extract_testimony_candidates(days_back=days_back, limit=limit)
    lines = [
        "🌟 *Testimony Candidates*",
        "━━━━━━━━━━━━━━━━━━",
        f"🕐 Window: last {days_back} day(s)",
        f"✅ Found: {result['count']}",
    ]

    if not result["candidates"]:
        lines.extend(
            [
                "",
                "Wala pa akong nakita na strong positive DM/comment in this window.",
                "Try a bigger range like `/testimonies 60`.",
            ]
        )
        return "\n".join(lines)

    for index, item in enumerate(result["candidates"], 1):
        timestamp = _parse_timestamp(item.get("timestamp", ""))
        when = timestamp.strftime("%Y-%m-%d %H:%M") + " PHT" if timestamp else "unknown time"
        lines.extend(
            [
                "",
                f"⭐ *#{index}* [{item['source']}] {item['sender_name']}",
                f"📝 {item['text']}",
                f"🕐 {when}",
            ]
        )
        if item.get("context"):
            lines.append(f"📌 {item['context']}")

    lines.extend(
        [
            "",
            "Tip: reuse the strongest ones for posts, landing pages, or ads after you verify the context.",
        ]
    )
    return "\n".join(lines)
