import importlib
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


class _FakeFlaskApp:
    def route(self, *args, **kwargs):
        def decorator(fn):
            return fn
        return decorator


def _fake_flask_factory(name):
    return _FakeFlaskApp()


def _fake_abort(code):
    raise RuntimeError(f"abort:{code}")


if "flask" not in sys.modules:
    fake_flask = types.ModuleType("flask")
    fake_flask.Flask = _fake_flask_factory
    fake_flask.request = types.SimpleNamespace(
        headers={},
        args={},
        get_json=lambda silent=True: {},
        get_data=lambda: b"",
    )
    fake_flask.jsonify = lambda payload: payload
    fake_flask.abort = _fake_abort
    sys.modules["flask"] = fake_flask

webhook_server = importlib.import_module("webhook_server")


class WebhookServerXenditTests(unittest.TestCase):
    def test_invoice_webhook_dedupes_same_webhook_id(self):
        payload = {
            "id": "inv-123",
            "external_id": "order-123",
            "status": "PAID",
        }
        calls = []

        request_stub = types.SimpleNamespace(
            headers={
                "x-callback-token": "secret-token",
                "webhook-id": "wh_123",
            },
            get_json=lambda silent=True: payload,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            processed_file = os.path.join(tmpdir, "processed_xendit_webhooks.json")
            with patch.object(webhook_server, "request", request_stub):
                with patch.object(webhook_server, "XENDIT_INVOICE_WEBHOOK_TOKEN", "secret-token"):
                    with patch.object(webhook_server, "PROCESSED_XENDIT_WEBHOOKS_FILE", processed_file):
                        with patch(
                            "webhook_server._process_xendit_invoice_webhook",
                            side_effect=lambda payload, webhook_key: calls.append((payload, webhook_key)),
                        ):
                            first = webhook_server.handle_xendit_invoice_webhook()
                            second = webhook_server.handle_xendit_invoice_webhook()

        self.assertEqual(first, ("OK", 200))
        self.assertEqual(second, ("OK", 200))
        self.assertEqual(len(calls), 1)

    def test_invoice_webhook_failure_is_not_marked_processed(self):
        payload = {
            "id": "inv-123",
            "external_id": "order-123",
            "status": "PAID",
        }
        request_stub = types.SimpleNamespace(
            headers={
                "x-callback-token": "secret-token",
                "webhook-id": "wh_123",
            },
            get_json=lambda silent=True: payload,
        )
        calls = []

        def flaky_handler(payload, webhook_key):
            calls.append(webhook_key)
            if len(calls) == 1:
                raise RuntimeError("temporary failure")
            return {"xendit_invoice_id": "inv-123"}

        with tempfile.TemporaryDirectory() as tmpdir:
            processed_file = os.path.join(tmpdir, "processed_xendit_webhooks.json")
            with patch.object(webhook_server, "request", request_stub):
                with patch.object(webhook_server, "XENDIT_INVOICE_WEBHOOK_TOKEN", "secret-token"):
                    with patch.object(webhook_server, "PROCESSED_XENDIT_WEBHOOKS_FILE", processed_file):
                        with patch("webhook_server._notify_xendit_webhook_failure"):
                            with patch("webhook_server._process_xendit_invoice_webhook", side_effect=flaky_handler):
                                first = webhook_server.handle_xendit_invoice_webhook()
                                second = webhook_server.handle_xendit_invoice_webhook()

        self.assertEqual(first, ("Webhook processing failed", 500))
        self.assertEqual(second, ("OK", 200))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
