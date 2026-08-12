import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from src import collector


class CollectorTests(unittest.TestCase):
    def _ranked_item(self, title, source, score, publish_risk_category=None):
        item = {
            "title": title,
            "url": f"https://example.com/{score}",
            "source": source,
            "source_type": "rss",
            "published_at": datetime.now(timezone.utc),
            "summary": "AI product update",
            "metrics": {},
            "_score": score,
        }
        if publish_risk_category:
            item["_publish_risk"] = {
                "category": publish_risk_category,
                "severity": "medium",
                "reason": "single-source financial or growth claim",
            }
        return item

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

    def test_final_editorial_dedup_merges_same_companies_legal_event(self):
        items = [
            {
                "title": "Apple sues OpenAI over executive hiring",
                "url": "https://example.com/apple-openai-lawsuit-a",
                "source": "The Verge",
                "source_type": "rss",
                "summary": "Apple filed a lawsuit against OpenAI.",
                "metrics": {},
                "_score": 90,
            },
            {
                "title": "Apple's lawsuit could not come at a worse time for OpenAI",
                "url": "https://example.com/apple-openai-lawsuit-b",
                "source": "TechCrunch",
                "source_type": "rss",
                "summary": "The legal action concerns Apple and OpenAI.",
                "metrics": {},
                "_score": 80,
            },
        ]

        deduped, report = collector.apply_final_editorial_dedup(items, top_n=2)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(report["details"][0]["reason"], "same_companies_legal_event")

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

    def test_source_balance_limits_single_publisher_when_alternatives_exist(self):
        items = [
            self._ranked_item(f"36氪 AI 产品观察 {i}", "36氪", 100 - i)
            for i in range(6)
        ] + [
            self._ranked_item(f"AI 技术进展 {i}", f"来源 {i}", 80 - i)
            for i in range(8)
        ]

        selected = collector._apply_source_balance(items, top_n=10)[:10]

        self.assertEqual(len(selected), 10)
        self.assertLessEqual(
            sum(1 for item in selected if item["source"] == "36氪"),
            3,
        )

    def test_source_balance_limits_single_source_financial_claims_when_alternatives_exist(self):
        finance_sources = ["36氪", "钛媒体", "虎嗅", "品玩", "量子位", "机器之心"]
        items = [
            self._ranked_item(
                f"AI 公司融资进展 {i}",
                finance_sources[i],
                100 - i,
                publish_risk_category="single_source_financial_claim",
            )
            for i in range(6)
        ] + [
            self._ranked_item(f"AI 开源工具进展 {i}", f"技术媒体 {i}", 80 - i)
            for i in range(8)
        ]

        selected = collector._apply_source_balance(items, top_n=10)[:10]

        self.assertEqual(len(selected), 10)
        self.assertLessEqual(
            sum(
                1
                for item in selected
                if item.get("_publish_risk", {}).get("category") == "single_source_financial_claim"
            ),
            2,
        )

    def test_collect_candidates_preserves_cross_source_reports_before_clustering(self):
        now = datetime.now(timezone.utc)
        rss_item = {
            "title": "OpenAI releases Model 5",
            "url": "https://openai.com/news/model-5",
            "source": "OpenAI Blog",
            "source_tier": "primary",
            "published_at": now,
            "published_source": "published_parsed",
            "summary": "OpenAI releases Model 5 for developers.",
        }
        x_item = {
            "id": "x-42",
            "title": "OpenAI releases Model 5",
            "url": "https://x.com/OpenAI/status/42",
            "source": "OpenAI (X)",
            "source_type": "x",
            "source_tier": "primary",
            "published_at": now,
            "published_source": "x_feed",
            "summary": "OpenAI releases Model 5 for developers.",
            "metrics": {},
            "scores": {},
            "x_handle": "OpenAI",
            "x_official": True,
            "x_official_source": "config/x_sources.json",
        }

        env = {
            "ENABLE_HN_COLLECTOR": "0",
            "ENABLE_GITHUB_COLLECTOR": "0",
            "ENABLE_HF_COLLECTOR": "0",
            "ENABLE_ARXIV_COLLECTOR": "0",
            "ENABLE_X_COLLECTOR": "1",
            "DAILY_X_MAX_ITEMS": "0",
        }
        diagnostics = {}
        with patch.dict(os.environ, env, clear=False), \
             patch.object(collector, "_load_sources", return_value=[{"name": "OpenAI"}]), \
             patch.object(collector, "_fetch_source", return_value=[rss_item]), \
             patch.object(collector, "_fetch_x", return_value=[x_item]):
            items = collector.collect_candidates(
                limit=10,
                hours=36,
                diagnostics=diagnostics,
                now=now,
            )

        self.assertEqual(len(items), 2)
        self.assertEqual({item["source_type"] for item in items}, {"rss", "x"})
        self.assertTrue(all(item["source_title"] == "OpenAI releases Model 5" for item in items))
        self.assertTrue(all(" + " not in item["source"] for item in items))
        self.assertEqual(diagnostics["returned_candidate_count"], 2)

    def test_collect_candidates_does_not_apply_legacy_publish_risk_penalties(self):
        now = datetime.now(timezone.utc)
        candidate = {
            "title": "OpenAI 融资达到 10 亿美元",
            "url": "https://example.com/funding",
            "source": "示例来源",
            "source_type": "rss",
            "source_tier": "media",
            "published_at": now,
            "summary": "OpenAI 人工智能模型公司融资信息。",
            "metrics": {"cross_source_count": 0},
            "scores": {},
        }
        with patch.object(collector, "_fetch_raw_candidates", return_value=[candidate]):
            items = collector.collect_candidates(limit=1, hours=36, now=now)

        self.assertEqual(len(items), 1)
        self.assertNotIn("_publish_risk", items[0])
        self.assertNotIn("publish_risk_penalty", items[0]["scores"])


if __name__ == "__main__":
    unittest.main()
