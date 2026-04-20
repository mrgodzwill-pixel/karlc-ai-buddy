import hashlib
import hmac
import importlib
import json
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


class WebhookServerSystemeTests(unittest.TestCase):
    def test_systeme_webhook_dedupes_same_message_id(self):
        payload = {"contact": {"id": 123, "email": "john@example.com"}}
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(b"secret-key", raw_body, hashlib.sha256).hexdigest()
        calls = []

        request_stub = types.SimpleNamespace(
            headers={
                "X-Webhook-Signature": signature,
                "X-Webhook-Message-Id": "msg_123",
                "X-Webhook-Event": "CONTACT_CREATED",
                "X-Webhook-Event-Timestamp": "2026-04-21T10:00:00+00:00",
            },
            get_json=lambda silent=True: payload,
            get_data=lambda: raw_body,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            processed_file = os.path.join(tmpdir, "processed_systeme_webhooks.json")
            with patch.object(webhook_server, "request", request_stub), patch.object(
                webhook_server, "SYSTEME_WEBHOOK_SECRET", "secret-key"
            ), patch.object(
                webhook_server, "PROCESSED_SYSTEME_WEBHOOKS_FILE", processed_file
            ), patch(
                "webhook_server._process_systeme_webhook",
                side_effect=lambda payload, webhook_key: calls.append((payload, webhook_key)),
            ):
                first = webhook_server.handle_systeme_webhook()
                second = webhook_server.handle_systeme_webhook()

        self.assertEqual(first, ("OK", 200))
        self.assertEqual(second, ("OK", 200))
        self.assertEqual(len(calls), 1)

    def test_systeme_webhook_rejects_bad_signature(self):
        payload = {"contact": {"id": 123, "email": "john@example.com"}}
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        request_stub = types.SimpleNamespace(
            headers={"X-Webhook-Signature": "bad-signature"},
            get_json=lambda silent=True: payload,
            get_data=lambda: raw_body,
        )

        with patch.object(webhook_server, "request", request_stub), patch.object(
            webhook_server, "SYSTEME_WEBHOOK_SECRET", "secret-key"
        ):
            with self.assertRaises(RuntimeError) as raised:
                webhook_server.handle_systeme_webhook()

        self.assertEqual(str(raised.exception), "abort:403")


if __name__ == "__main__":
    unittest.main()
