import json
import os
import tempfile
import unittest
from unittest.mock import patch
import sys
import types

if "requests" not in sys.modules:
    fake_requests = types.ModuleType("requests")
    fake_requests.post = lambda *args, **kwargs: None
    fake_requests.get = lambda *args, **kwargs: None
    sys.modules["requests"] = fake_requests

import testimony_extractor


class TestimonyExtractorTests(unittest.TestCase):
    def test_extract_candidates_from_comments_and_dms(self):
        comments = [
            {
                "id": "c1",
                "message": "Solid sir, sobrang laking tulong nitong course. Sulit!",
                "from": {"name": "Juan Dela Cruz"},
                "created_time": "2026-04-23T09:00:00+08:00",
            }
        ]
        posts = [{"id": "p1", "message": "New MikroTik post"}]
        dms = [
            {
                "mid": "m1",
                "sender_name": "Maria Santos",
                "text": "Thank you boss, very useful and legit. Natutunan ko agad yung setup.",
                "timestamp": "2026-04-22T18:00:00+08:00",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            testimony_extractor, "DATA_DIR", tmpdir
        ), patch.object(
            testimony_extractor, "MESSAGES_FILE", os.path.join(tmpdir, "messages.json")
        ), patch.object(
            testimony_extractor, "TESTIMONY_FILE", os.path.join(tmpdir, "testimony_candidates.json")
        ), patch(
            "testimony_extractor.get_page_posts", return_value=posts
        ), patch(
            "testimony_extractor.get_post_comments", return_value=comments
        ), patch(
            "testimony_extractor._now",
            return_value=testimony_extractor.datetime(2026, 4, 23, 20, 0, tzinfo=testimony_extractor.PHT),
        ):
            with open(testimony_extractor.MESSAGES_FILE, "w") as handle:
                json.dump(dms, handle)

            result = testimony_extractor.extract_testimony_candidates(days_back=30, limit=5)

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["candidates"][0]["source"], "DM")
        self.assertEqual(result["candidates"][1]["source"], "Comment")

    def test_format_candidates_handles_empty_result(self):
        with patch(
            "testimony_extractor.extract_testimony_candidates",
            return_value={"count": 0, "candidates": [], "checked_at": "", "days_back": 30},
        ):
            text = testimony_extractor.format_testimony_candidates_telegram(days_back=30)

        self.assertIn("Found: 0", text)
        self.assertIn("Wala pa akong nakita", text)


if __name__ == "__main__":
    unittest.main()
