import unittest
from datetime import datetime, timezone

from src import collector


class CollectorTests(unittest.TestCase):
    def test_is_ai_related_matches_high_confidence_terms(self):
        self.assertTrue(
            collector._is_ai_related(
                "OpenAI releases a new reasoning model",
                "The model improves multimodal benchmark results.",
            )
        )

    def test_is_ai_related_rejects_unrelated_news(self):
        self.assertFalse(
            collector._is_ai_related(
                "City council approves new bike lanes",
                "The project starts next month.",
            )
        )

    def test_parse_published_multi_prefers_struct_time(self):
        parsed, source = collector._parse_published_multi({
            "published_parsed": datetime(2026, 7, 11, 8, 30, tzinfo=timezone.utc).timetuple()
        })

        self.assertEqual(source, "published_parsed")
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.year, 2026)
        self.assertEqual(parsed.month, 7)
        self.assertEqual(parsed.day, 11)

    def test_parse_rss_item_strips_html_and_extracts_images(self):
        item = collector._parse_rss_item(
            {
                "title": "Anthropic shares AI safety update",
                "link": "https://example.com/2026/07/11/story",
                "summary": "<p>New <b>AI</b> safety notes.</p><img src=\"https://example.com/a.jpg\">",
            },
            name_hint="Example Feed",
        )

        self.assertIsNotNone(item)
        self.assertEqual(item["source"], "Example Feed")
        self.assertEqual(item["summary"], "New AI safety notes.")
        self.assertEqual(item["published_source"], "url_date")
        self.assertEqual(item["published_at"].date().isoformat(), "2026-07-11")
        self.assertEqual(item["image_candidates"][0]["url"], "https://example.com/a.jpg")


if __name__ == "__main__":
    unittest.main()
