import importlib
import sys
import types
import unittest
from unittest.mock import patch

if "requests" not in sys.modules:
    fake_requests = types.ModuleType("requests")
    fake_requests.request = lambda *args, **kwargs: None
    sys.modules["requests"] = fake_requests

systeme_api = importlib.import_module("systeme_api")


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class SystemeAPITests(unittest.TestCase):
    def test_list_contacts_falls_back_to_api_key_header_auth(self):
        calls = []

        def fake_request(method, url, params=None, headers=None, timeout=None):
            calls.append({"method": method, "url": url, "params": params, "headers": headers})
            auth_header = (headers or {}).get("Authorization", "")
            api_key_header = (headers or {}).get("X-API-Key", "")
            if auth_header == "Bearer test-key":
                return _FakeResponse(401, {"message": "Unauthorized"})
            if api_key_header == "test-key":
                return _FakeResponse(200, [{"id": 101, "email": "juan@example.com"}])
            return _FakeResponse(403, {"message": "Forbidden"})

        with patch.object(systeme_api, "SYSTEME_API_KEY", "test-key"), patch.object(
            systeme_api, "_AUTH_MODE_CACHE", ""
        ), patch("systeme_api.requests.request", side_effect=fake_request):
            contacts = systeme_api.list_contacts(limit=100, max_pages=1)

        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["email"], "juan@example.com")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["headers"].get("X-API-Key"), "test-key")


if __name__ == "__main__":
    unittest.main()
