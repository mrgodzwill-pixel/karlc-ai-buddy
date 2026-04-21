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

    def test_create_contact_tries_multiple_payload_shapes(self):
        calls = []

        def fake_request(method, url, params=None, headers=None, timeout=None, json=None):
            calls.append({"method": method, "url": url, "params": params, "headers": headers, "json": json})
            if method == "GET":
                return _FakeResponse(200, [])
            if method == "POST" and json == {
                "email": "juan@example.com",
                "name": "Juan Dela Cruz",
                "fields": {
                    "first_name": "Juan",
                    "surname": "Dela Cruz",
                    "phone_number": "09171234567",
                },
            }:
                return _FakeResponse(400, {"message": "Bad payload"})
            if method == "POST" and json == {
                "email": "juan@example.com",
                "first_name": "Juan",
                "surname": "Dela Cruz",
                "phone_number": "09171234567",
                "name": "Juan Dela Cruz",
            }:
                return _FakeResponse(200, {"id": 555, "email": "juan@example.com"})
            return _FakeResponse(400, {"message": "Unexpected"})

        with patch.object(systeme_api, "SYSTEME_API_KEY", "test-key"), patch.object(
            systeme_api, "_AUTH_MODE_CACHE", "x_api_key_header"
        ), patch("systeme_api.requests.request", side_effect=fake_request):
            contact = systeme_api.create_contact(
                "juan@example.com",
                first_name="Juan",
                surname="Dela Cruz",
                full_name="Juan Dela Cruz",
                phone_number="09171234567",
            )

        self.assertEqual(contact["id"], 555)
        self.assertEqual(contact["email"], "juan@example.com")
        post_calls = [call for call in calls if call["method"] == "POST"]
        self.assertEqual(len(post_calls), 2)

    def test_create_enrollment_tries_multiple_payload_shapes(self):
        calls = []

        def fake_request(method, url, params=None, headers=None, timeout=None, json=None):
            calls.append({"method": method, "url": url, "json": json})
            if json == {"contactId": "10", "courseId": "20"}:
                return _FakeResponse(400, {"message": "Bad payload"})
            if json == {"contact_id": "10", "course_id": "20"}:
                return _FakeResponse(200, {"id": 999})
            return _FakeResponse(400, {"message": "Unexpected"})

        with patch.object(systeme_api, "SYSTEME_API_KEY", "test-key"), patch.object(
            systeme_api, "_AUTH_MODE_CACHE", "x_api_key_header"
        ), patch("systeme_api.requests.request", side_effect=fake_request):
            enrollment = systeme_api.create_enrollment("10", "20")

        self.assertEqual(enrollment["id"], 999)
        self.assertEqual(len(calls), 2)

    def test_assign_tag_to_contact_uses_documented_path_and_body(self):
        calls = []

        def fake_request(method, url, params=None, headers=None, timeout=None, json=None):
            calls.append({"method": method, "url": url, "json": json})
            return _FakeResponse(204, {})

        with patch.object(systeme_api, "SYSTEME_API_KEY", "test-key"), patch.object(
            systeme_api, "_AUTH_MODE_CACHE", "x_api_key_header"
        ), patch("systeme_api.requests.request", side_effect=fake_request):
            systeme_api.assign_tag_to_contact("10", "20")

        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["url"].endswith("/contacts/10/tags"))
        self.assertEqual(calls[0]["json"], {"tagId": 20})

    def test_request_retries_transport_error_before_success(self):
        calls = []

        def fake_request(method, url, params=None, headers=None, timeout=None, json=None):
            calls.append({"method": method, "url": url})
            if len(calls) == 1:
                raise TimeoutError("read timed out")
            return _FakeResponse(200, [{"id": 101, "email": "juan@example.com"}])

        with patch.object(systeme_api, "SYSTEME_API_KEY", "test-key"), patch.object(
            systeme_api, "_AUTH_MODE_CACHE", "x_api_key_header"
        ), patch("systeme_api.requests.request", side_effect=fake_request), patch(
            "systeme_api.time.sleep"
        ):
            contacts = systeme_api.list_contacts(limit=100, max_pages=1)

        self.assertEqual(len(contacts), 1)
        self.assertEqual(len(calls), 2)

    def test_list_collection_returns_none_on_transport_failure(self):
        with patch.object(systeme_api, "SYSTEME_API_KEY", "test-key"), patch.object(
            systeme_api, "_AUTH_MODE_CACHE", "x_api_key_header"
        ), patch(
            "systeme_api.requests.request", side_effect=TimeoutError("read timed out")
        ), patch("systeme_api.time.sleep"):
            contacts = systeme_api.list_contacts(limit=100, max_pages=1)

        self.assertIsNone(contacts)


if __name__ == "__main__":
    unittest.main()
