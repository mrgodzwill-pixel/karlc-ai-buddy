"""
Shared course and tag normalization helpers.

This keeps manual Systeme enrollment, Google Sheet write-back, and sheet import
aligned on the same canonical course names and official tags.
"""

from __future__ import annotations

import re

from config import (
    COURSES,
    SYSTEME_TAG_BUNDLE4,
    SYSTEME_TAG_FTTH,
    SYSTEME_TAG_MIKROTIK_10G,
    SYSTEME_TAG_MIKROTIK_BASIC,
    SYSTEME_TAG_MIKROTIK_DUAL_ISP,
    SYSTEME_TAG_MIKROTIK_HYBRID,
    SYSTEME_TAG_MIKROTIK_OSPF,
    SYSTEME_TAG_MIKROTIK_TRAFFIC,
    SYSTEME_TAG_PISOWIFI,
    SYSTEME_TAG_SOLAR,
)


_MOJIBAKE_PREFIXES = ("Ã¢ÂÂ¢", "â¢", "•", "-", "ÃÂÃÂ¢ÃÂÃÂÃÂÃÂ¢")


def _repair_text(text):
    cleaned = str(text or "").replace("\u00a0", " ").strip()
    for _ in range(4):
        try:
            repaired = cleaned.encode("latin1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if repaired == cleaned:
            break
        cleaned = repaired
    return cleaned


def _clean_fragment(text):
    cleaned = _repair_text(text)
    while True:
        original = cleaned
        for prefix in _MOJIBAKE_PREFIXES:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
        if cleaned == original:
            break
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    return cleaned


def _normalize(text):
    return re.sub(r"\s+", " ", _clean_fragment(text).lower())


_CANONICAL_COURSE_NAMES = {
    "mikrotik_basic": "MikroTik QuickStart: Configure From Scratch",
    "mikrotik_dual_isp": "New Dual ISP Load Balancing with Auto Fail-over (CPU Friendly)",
    "mikrotik_hybrid": "Hybrid Access Combo: IPoE + PPPoE",
    "mikrotik_traffic": "MikroTik Traffic Control Basics",
    "mikrotik_10g": "10G Core Part 1: ISP Aggregator",
    "mikrotik_ospf": "10G Core Part 2: OSPF & Advanced Routing",
    "ftth": "PLC & FBT Combo: Budget-Friendly FTTH Design",
    "solar": "DIY Hybrid Solar Setup",
    "pisowifi": "10G Core Part 3: Centralized Pisowifi Setup",
    "bundle4": "Complete MikroTik Mastery Bundle",
}

_OFFICIAL_TAG_NAMES = {
    "mikrotik_basic": SYSTEME_TAG_MIKROTIK_BASIC,
    "mikrotik_dual_isp": SYSTEME_TAG_MIKROTIK_DUAL_ISP,
    "mikrotik_hybrid": SYSTEME_TAG_MIKROTIK_HYBRID,
    "mikrotik_traffic": SYSTEME_TAG_MIKROTIK_TRAFFIC,
    "mikrotik_10g": SYSTEME_TAG_MIKROTIK_10G,
    "mikrotik_ospf": SYSTEME_TAG_MIKROTIK_OSPF,
    "ftth": SYSTEME_TAG_FTTH,
    "solar": SYSTEME_TAG_SOLAR,
    "pisowifi": SYSTEME_TAG_PISOWIFI,
    "bundle4": SYSTEME_TAG_BUNDLE4,
}

_SPECIAL_QUERY_PATTERNS = {
    "mikrotik_basic": [
        "step-by-step kung paano mag-setup ng mikrotik routeros from scratch",
        "mikrotik routeros from scratch",
        "karlc-mikrotik-basic",
        "quickstart",
        "configure from scratch",
    ],
    "mikrotik_dual_isp": [
        "karlc-dual-isp",
        "dual isp",
        "auto fail-over",
    ],
    "mikrotik_hybrid": [
        "karlc-hybrid-access",
        "hybrid access combo",
        "ipoe + pppoe",
    ],
    "mikrotik_traffic": [
        "karlc-traffic-control",
        "traffic control basics",
    ],
    "mikrotik_10g": [
        "karlc-core10g",
        "10g core part 1",
        "isp aggregator",
    ],
    "mikrotik_ospf": [
        "karlc-ospf",
        "10g core part 2",
        "advanced routing",
    ],
    "ftth": [
        "karlc-ftth",
        "plc & fbt combo",
        "budget-friendly ftth design",
    ],
    "solar": [
        "karlc-solar",
        "diy hybrid solar setup",
    ],
    "pisowifi": [
        "build a true centralized pisowifi system",
        "centralized pisowifi system",
        "random mac fix",
        "multi-vendo deployment",
        "pisowifi",
        "karlc-pisowifi",
        "10g core part 3",
    ],
    "bundle4": [
        "complete mikrotik mastery bundle",
        "get all 4 mikrotik courses in one bundle",
        "all 4 mikrotik courses",
        "bundle4",
    ],
}


def course_query_variants(course_query):
    raw = _clean_fragment(course_query)
    variants = []

    def add(value):
        normalized = _normalize(value)
        if normalized and normalized not in variants:
            variants.append(normalized)

    add(raw)
    cleaned = re.sub(r"\s*-\s*invoice\s+for\s+.+$", "", raw, flags=re.IGNORECASE).strip()
    add(cleaned)
    cleaned = re.sub(r"^invoice\s+paid\s*:\s*", "", cleaned, flags=re.IGNORECASE).strip()
    add(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:.")
    add(cleaned)
    return variants


def expand_course_query_values(values):
    expanded = []
    seen = set()

    def add(value):
        cleaned = _clean_fragment(value)
        normalized = _normalize(cleaned)
        if cleaned and normalized not in seen:
            seen.add(normalized)
            expanded.append(cleaned)

    for raw in values or []:
        text = _repair_text(raw)
        add(text)
        for line in str(text).splitlines():
            add(line)
            for part in str(line).split(","):
                add(part)

    return expanded


def course_key_from_query(course_query):
    queries = course_query_variants(course_query)
    if not queries:
        return ""

    exact_title_map = {
        _normalize("MikroTik QuickStart: Configure From Scratch"): "mikrotik_basic",
        _normalize("New Dual ISP Load Balancing with Auto Fail-over (CPU Friendly)"): "mikrotik_dual_isp",
        _normalize("Hybrid Access Combo: IPoE + PPPoE"): "mikrotik_hybrid",
        _normalize("MikroTik Traffic Control Basics"): "mikrotik_traffic",
        _normalize("10G Core Part 1: ISP Aggregator"): "mikrotik_10g",
        _normalize("10G Core Part 2: OSPF & Advanced Routing"): "mikrotik_ospf",
        _normalize("PLC & FBT Combo: Budget-Friendly FTTH Design"): "ftth",
        _normalize("DIY Hybrid Solar Setup"): "solar",
        _normalize("10G Core Part 3: Centralized Pisowifi Setup"): "pisowifi",
        _normalize("Complete MikroTik Mastery Bundle"): "bundle4",
        _normalize("Step-by-step kung paano mag-setup ng MikroTik RouterOS from scratch."): "mikrotik_basic",
        _normalize("Step-by-step kung paano mag-setup ng MikroTik RouterOS from scratch"): "mikrotik_basic",
    }

    for query in queries:
        if query in exact_title_map:
            return exact_title_map[query]

    for query in queries:
        for course_key, patterns in _SPECIAL_QUERY_PATTERNS.items():
            normalized_patterns = [_normalize(pattern) for pattern in patterns]
            if query in normalized_patterns:
                return course_key
            if any(query and (query in pattern or pattern in query) for pattern in normalized_patterns):
                return course_key

    aliases = {}
    for course_key, course in COURSES.items():
        for keyword in course.get("keywords", []):
            normalized = _normalize(keyword)
            if normalized:
                aliases[normalized] = course_key

    for query in queries:
        if query in aliases:
            return aliases[query]

    for query in queries:
        for course_key, canonical in _CANONICAL_COURSE_NAMES.items():
            canonical_norm = _normalize(canonical)
            if query == canonical_norm or query in canonical_norm or canonical_norm in query:
                return course_key

    return ""


def canonical_course_name(course_query, *, allow_old_fallback=True):
    course_query = str(course_query or "").strip()
    if not course_query:
        return ""

    course_key = course_key_from_query(course_query)
    if course_key:
        return _CANONICAL_COURSE_NAMES[course_key]

    if not allow_old_fallback:
        return ""

    query = _normalize(course_query)
    if "bundle" in query or "3-in-1" in query or "3 in 1" in query or "3in1" in query:
        return "OLD Bundle Access"
    if "invoice" in query or "paid" in query:
        return "OLD Course Access"
    return course_query


def official_tag_name_for_course(course_query, *, allow_old_fallback=True):
    course_query = str(course_query or "").strip()
    if not course_query:
        return ""

    course_key = course_key_from_query(course_query)
    if course_key:
        return str(_OFFICIAL_TAG_NAMES.get(course_key) or "").strip()

    if not allow_old_fallback:
        return ""

    query = _normalize(course_query)
    if "bundle" in query or "3-in-1" in query or "3 in 1" in query or "3in1" in query:
        return "OLD_BUNDLE"
    return "OLD_COURSE"


def canonicalize_course_names(course_names, *, allow_old_fallback=True):
    ordered = []
    seen = set()
    for name in expand_course_query_values(course_names):
        canonical = canonical_course_name(name, allow_old_fallback=allow_old_fallback)
        normalized = _normalize(canonical)
        if not canonical or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(canonical)
    return ordered


def official_tag_names_for_courses(course_names, *, allow_old_fallback=True):
    ordered = []
    seen = set()
    for name in canonicalize_course_names(course_names, allow_old_fallback=allow_old_fallback):
        tag_name = official_tag_name_for_course(name, allow_old_fallback=allow_old_fallback)
        normalized = _normalize(tag_name)
        if not tag_name or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(tag_name)
    return ordered
