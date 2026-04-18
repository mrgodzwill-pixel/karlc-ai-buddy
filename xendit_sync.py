"""
Xendit webhook handling and payment-store sync helpers.
"""

import logging
from datetime import datetime, timedelta, timezone

import xendit_api
from xendit_payments import (
    build_record_from_invoice_data,
    build_record_from_payment_data,
    upsert_payment_records,
)

logger = logging.getLogger(__name__)
PHT = timezone(timedelta(hours=8))


def _normalize_payment_webhook_payload(payload):
    payload = dict(payload or {})
    if "event" in payload and "data" in payload:
        return payload

    for value in payload.values():
        if not isinstance(value, dict):
            continue
        candidate = value.get("value") if isinstance(value.get("value"), dict) else value
        if isinstance(candidate, dict) and "event" in candidate and "data" in candidate:
            return candidate

    return payload


def process_invoice_webhook(payload, checked_at=None):
    records = _expand_invoice_records(payload, source="xendit_invoice_webhook")
    if not records:
        return None
    store, merged = upsert_payment_records(records, checked_at=checked_at or datetime.now(PHT).isoformat())
    return merged[-1] if merged else None


def process_payment_webhook(payload, checked_at=None):
    normalized = _normalize_payment_webhook_payload(payload)
    payment_data = normalized.get("data") or {}
    customer = None

    customer_id = payment_data.get("customer_id")
    if customer_id and xendit_api.available():
        customer = xendit_api.get_customer(customer_id, timeout=5)

    record = build_record_from_payment_data(payment_data, customer=customer, source="xendit_payment_webhook")
    if not record:
        return None
    store, records = upsert_payment_records([record], checked_at=checked_at or datetime.now(PHT).isoformat())
    return records[0] if records else None


def _expand_invoice_records(invoice, source="xendit_invoice_api"):
    """Build invoice records and optionally enrich them with payment/customer data."""
    base_record = build_record_from_invoice_data(invoice, source=source)
    if not base_record:
        return []

    records = [base_record]
    payment_id = str(invoice.get("payment_id") or "").strip()
    needs_payment_enrichment = not (
        base_record.get("payer_name") and (base_record.get("phone") or base_record.get("phone_normalized"))
    )
    if not payment_id or not xendit_api.available() or not needs_payment_enrichment:
        return records

    payment_data = xendit_api.get_payment(payment_id, timeout=5)
    if not payment_data:
        return records

    customer = None
    customer_id = payment_data.get("customer_id")
    if customer_id:
        customer = xendit_api.get_customer(customer_id, timeout=5)

    payment_record = build_record_from_payment_data(
        payment_data,
        customer=customer,
        source=f"{source}_payment_api",
    )
    if payment_record:
        records.append(payment_record)

    return records


def sync_recent_invoice_payments(days_back=7):
    """Fetch recent paid invoices from Xendit's legacy invoice API and cache them locally."""
    if not xendit_api.available():
        return None

    invoices = xendit_api.list_paid_invoices(days_back=days_back)
    if invoices is None:
        return None

    checked_at = datetime.now(PHT).isoformat()
    records = []
    for invoice in invoices:
        records.extend(_expand_invoice_records(invoice, source="xendit_invoice_api"))

    store, merged = upsert_payment_records(records, checked_at=checked_at)
    logger.info("Synced %s recent Xendit invoice/payment record(s)", len(records))
    return merged
