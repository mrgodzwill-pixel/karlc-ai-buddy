"""
Minimal Xendit API helpers used for webhook enrichment and invoice sync.
"""

import logging
from datetime import datetime, timedelta, timezone

import requests

from config import (
    XENDIT_API_BASE_URL,
    XENDIT_CUSTOMER_API_VERSION,
    XENDIT_PAYMENT_API_VERSION,
    XENDIT_SECRET_KEY,
)

logger = logging.getLogger(__name__)


def available():
    return bool(XENDIT_SECRET_KEY)


def _request(method, path, *, params=None, headers=None, timeout=15):
    if not available():
        return None

    url = f"{XENDIT_API_BASE_URL.rstrip('/')}{path}"
    merged_headers = dict(headers or {})

    try:
        response = requests.request(
            method,
            url,
            params=params,
            headers=merged_headers,
            auth=(XENDIT_SECRET_KEY, ""),
            timeout=timeout,
        )
    except Exception:
        logger.exception("Xendit request failed: %s %s", method, path)
        return None

    if response.status_code >= 400:
        logger.warning(
            "Xendit request failed: %s %s -> %s %s",
            method,
            path,
            response.status_code,
            response.text[:200],
        )
        return None

    try:
        return response.json()
    except ValueError:
        logger.warning("Xendit returned non-JSON response for %s %s", method, path)
        return None


def get_customer(customer_id, timeout=15):
    if not customer_id:
        return None
    return _request(
        "GET",
        f"/customers/{customer_id}",
        headers={"api-version": XENDIT_CUSTOMER_API_VERSION},
        timeout=timeout,
    )


def get_payment(payment_id, timeout=15):
    if not payment_id:
        return None
    return _request(
        "GET",
        f"/v3/payments/{payment_id}",
        headers={"api-version": XENDIT_PAYMENT_API_VERSION},
        timeout=timeout,
    )


def get_invoice(invoice_id, timeout=15):
    if not invoice_id:
        return None
    return _request("GET", f"/v2/invoices/{invoice_id}", timeout=timeout)


def list_invoices(*, statuses=None, limit=100, paid_after=None, paid_before=None, last_invoice_id=None, timeout=15):
    params = {"limit": limit}
    if statuses:
        params["statuses"] = list(statuses)
    if paid_after:
        params["paid_after"] = paid_after
    if paid_before:
        params["paid_before"] = paid_before
    if last_invoice_id:
        params["last_invoice_id"] = last_invoice_id
    return _request("GET", "/v2/invoices", params=params, timeout=timeout)


def list_paid_invoices(days_back=7, limit=100, max_pages=10, timeout=15):
    """List recent paid/settled legacy invoice records from Xendit."""
    if not available():
        return None

    now = datetime.now(timezone.utc)
    paid_after = (now - timedelta(days=days_back)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    paid_before = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    all_invoices = []
    last_invoice_id = None

    for _ in range(max_pages):
        batch = list_invoices(
            statuses=["PAID", "SETTLED"],
            limit=limit,
            paid_after=paid_after,
            paid_before=paid_before,
            last_invoice_id=last_invoice_id,
            timeout=timeout,
        )
        if batch is None:
            return None if not all_invoices else all_invoices
        if not batch:
            break

        all_invoices.extend(batch)
        if len(batch) < limit:
            break

        last_invoice_id = batch[-1].get("id")
        if not last_invoice_id:
            break

    return all_invoices
