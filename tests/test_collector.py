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

    def test_final_editorial_dedup_merges_title_variants_with_spacing_and_prefix(self):
        items = [
            {
                "title": "独家 | 智谱 ARR 达到 10 亿美元，半年增长 15 倍",
                "url": "https://example.com/zhipu-exclusive",
                "source": "Source A",
                "source_type": "rss",
                "summary": "",
                "metrics": {},
                "_score": 90,
            },
            {
                "title": "智谱ARR达到10亿美元，半年增长15倍",
                "url": "https://example.com/zhipu-arr",
                "source": "Source B",
                "source_type": "rss",
                "summary": "",
                "metrics": {},
                "_score": 80,
            },
        ]

        deduped, report = collector.apply_final_editorial_dedup(items, top_n=2)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(report["merged_groups"], 1)
        self.assertEqual(deduped[0]["merged_count"], 1)

    def test_score_marks_hn_only_model_comparison_as_publish_risk(self):
        item = {
            "title": "$100 AI Music Video: Claude Fable 5 vs. GPT-5.6 Sol",
            "url": "https://news.ycombinator.com/item?id=123",
            "source": "Hacker News",
            "source_type": "hn",
            "published_at": datetime.now(timezone.utc),
            "summary": "",
            "metrics": {
                "hn_score": 200,
                "hn_comments": 120,
                "cross_source_count": 0,
            },
        }

        collector._score_item(item, [item])

        self.assertEqual(item["_publish_risk"]["category"], "community_model_comparison")
        self.assertGreater(item["scores"]["publish_risk_penalty"], 0)

    def test_score_marks_hn_rss_model_comparison_as_publish_risk(self):
        item = {
            "title": "$100 AI Music Video: Claude Fable 5 vs. GPT-5.6 Sol",
            "url": "https://example.com/community-test",
            "source": "Hacker News AI + Hacker News",
            "source_type": "rss",
            "published_at": datetime.now(timezone.utc),
            "summary": "",
            "metrics": {
                "hn_score": 200,
                "hn_comments": 120,
                "cross_source_count": 1,
            },
        }

        collector._score_item(item, [item])

        self.assertEqual(item["_publish_risk"]["category"], "community_model_comparison")
        self.assertGreater(item["scores"]["publish_risk_penalty"], 0)

    def test_score_marks_single_source_financial_claim_as_publish_risk(self):
        item = {
            "title": "智谱 ARR 达到10亿美元，半年增长15倍",
            "url": "https://36kr.com/p/example",
            "source": "36氪",
            "source_type": "rss",
            "published_at": datetime.now(timezone.utc),
            "summary": "",
            "metrics": {
                "cross_source_count": 0,
            },
        }

        collector._score_item(item, [item])

        self.assertEqual(item["_publish_risk"]["category"], "single_source_financial_claim")
        self.assertGreater(item["scores"]["publish_risk_penalty"], 0)


if __name__ == "__main__":
    unittest.main()
