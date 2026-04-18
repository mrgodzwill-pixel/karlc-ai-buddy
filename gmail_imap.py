"""
Gmail IMAP helper.

Uses a Gmail App Password + Python's built-in imaplib to read the inbox. The
search path uses Gmail's X-GM-RAW IMAP extension so Gmail-style queries
(`from:...`, `newer_than:7d`, `subject:...`) keep working unchanged from the
old MCP-based implementation.

Environment:
    GMAIL_USER             - full gmail address
    GMAIL_APP_PASSWORD     - 16-char app password (NOT the account password)

If either is unset, `available()` returns False and callers treat Gmail
lookups as unavailable — same semantics as the old `manus-mcp-cli` check.
"""

import email
import imaplib
import logging
import os
from email.header import decode_header

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


def available() -> bool:
    return bool(os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD"))


def _connect():
    user = os.environ.get("GMAIL_USER", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not user or not password:
        return None
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(user, password)
        # Use "All Mail" (not INBOX) so messages auto-filtered to labels — e.g.
        # a Gmail filter that moves Xendit invoices out of the inbox — are
        # still found. INBOX-only would miss anything with a "skip inbox" rule.
        typ, _ = mail.select("[Gmail]/All Mail", readonly=True)
        if typ != "OK":
            logger.warning("Could not select [Gmail]/All Mail — falling back to INBOX")
            mail.select("INBOX", readonly=True)
        return mail
    except Exception:
        logger.exception("Gmail IMAP connect failed")
        return None


def _close(mail) -> None:
    if mail is None:
        return
    try:
        mail.close()
    except Exception:
        pass
    try:
        mail.logout()
    except Exception:
        pass


def _decode(header_val: str) -> str:
    if not header_val:
        return ""
    out = []
    for text, charset in decode_header(header_val):
        if isinstance(text, bytes):
            try:
                text = text.decode(charset or "utf-8", errors="replace")
            except Exception:
                text = text.decode("utf-8", errors="replace")
        out.append(text)
    return "".join(out)


def _extract_body(msg) -> str:
    """Prefer text/plain; fall back to raw text/html if that's all we have."""
    plain = ""
    html = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if ctype == "text/plain":
                plain += text
            elif ctype == "text/html":
                html += text
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            plain = payload.decode(charset, errors="replace") if payload else ""
        except Exception:
            plain = msg.get_payload() or ""
    return plain if plain else html


def search(gmail_query: str, limit: int = 20):
    """Run a Gmail-style search and return parsed messages (newest first).

    Each item: {"subject", "from", "date", "body"}.
    Returns None when Gmail IMAP is not configured so callers can distinguish
    "nothing found" from "couldn't check".
    """
    mail = _connect()
    if mail is None:
        return None

    results = []
    try:
        # X-GM-RAW accepts Gmail's full search syntax (including `newer_than:7d`,
        # `from:`, `subject:`, boolean operators, etc.).
        typ, data = mail.search(None, "X-GM-RAW", f'"{gmail_query}"')
        if typ != "OK" or not data or not data[0]:
            return results

        ids = data[0].split()
        # IMAP returns oldest→newest; we want newest→oldest, capped.
        ids = list(reversed(ids))[:limit]

        for mid in ids:
            try:
                typ, fdata = mail.fetch(mid, "(RFC822)")
                if typ != "OK" or not fdata or not fdata[0]:
                    continue
                raw = fdata[0][1]
                msg = email.message_from_bytes(raw)
                results.append({
                    "subject": _decode(msg.get("Subject", "")),
                    "from": _decode(msg.get("From", "")),
                    "date": msg.get("Date", ""),
                    "body": _extract_body(msg),
                })
            except Exception:
                logger.exception("Fetch failed for id=%s", mid)
    except Exception:
        logger.exception("Gmail search failed: %s", gmail_query)
    finally:
        _close(mail)

    return results
