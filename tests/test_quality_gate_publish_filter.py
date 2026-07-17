import unittest
from unittest.mock import patch

from src.quality_gate import review_daily


def _item(title, source_type="rss"):
    return {
        "title": title,
        "chinese_title": title,
        "summary": "Short summary",
        "source": "Example",
        "source_type": source_type,
        "metrics": {},
    }


class QualityGatePublishFilterTests(unittest.TestCase):
    def test_high_risk_selected_item_uses_only_quota_compliant_reserve(self):
        selected = [
            {**_item("Unsafe source"), "source": "Unsafe publisher", "topic_key": "unsafe", "_score": 100},
            {**_item("Publisher A first"), "source": "Publisher A", "topic_key": "platform", "_score": 90},
            {**_item("Publisher A second"), "source": "Publisher A", "topic_key": "research", "_score": 80},
        ]
        reserves = [
            {**_item("Publisher A third"), "source": "Publisher A", "topic_key": "models", "_score": 70},
            {**_item("Publisher B replacement"), "source": "Publisher B", "topic_key": "models", "_score": 60},
        ]
        llm_issues = [{
            "type": "unsupported_claim",
            "severity": "high",
            "item_index": 1,
            "field": "summary",
            "message": "Unsupported claim.",
            "evidence": "source evidence",
        }]

        with patch("src.quality_gate._run_llm_review", return_value=(llm_issues, [], [])):
            reviewed, report = review_daily(
                selected,
                reserves=reserves,
                api_key="test-key",
                model="test-model",
                target_count=3,
                filter_high_risk=True,
                max_items_per_source=2,
                max_items_per_topic=2,
                min_primary_or_research=0,
            )

        self.assertEqual(
            [item["title"] for item in reviewed],
            ["Publisher A first", "Publisher A second", "Publisher B replacement"],
        )
        self.assertEqual(report["publish_filter"]["replaced_count"], 1)
        self.assertEqual(report["publish_filter"]["selected_count"], 3)
        self.assertFalse(report["blocked_publish"])

    def test_sparse_source_evidence_is_downgraded_to_source_only(self):
        item = {
            **_item("Original source title"),
            "source_title": "Original source title",
            "source_summary": "",
            "source_url": "https://example.test/source",
            "chinese_title": "生成的夸张标题",
            "summary": "生成的细节，原始来源并未提供。",
        }

        reviewed, report = review_daily([item])

        self.assertEqual(reviewed[0]["quality_state"], "source_only")
        self.assertEqual(reviewed[0]["chinese_title"], "Original source title")
        self.assertIn("原始来源未提供足够摘要", reviewed[0]["summary"])
        self.assertFalse(report["blocked_publish"])

    def test_strict_mode_never_blocks_the_daily_draft(self):
        news = [_item("Unsafe item")]
        llm_issues = [{
            "type": "unsupported_claim",
            "severity": "high",
            "item_index": 1,
            "field": "summary",
            "message": "Unsupported claim.",
            "evidence": "source evidence",
        }]

        with patch("src.quality_gate._run_llm_review", return_value=(llm_issues, [], [])):
            reviewed, report = review_daily(
                news,
                api_key="test-key",
                model="test-model",
                strict=True,
                target_count=1,
                filter_high_risk=True,
            )

        self.assertEqual(reviewed, [])
        self.assertFalse(report["blocked_publish"])
        self.assertTrue(report["publish_filter"]["insufficient_publishable_items"])

    def test_high_risk_llm_item_is_removed_and_reserve_backfills(self):
        news = [
            _item("Community test mentions GPT-5.6", source_type="hn"),
            _item("Official model update"),
            _item("Roblox AI tool"),
            _item("NotebookLM update"),
        ]
        llm_issues = [
            {
                "type": "rumor_as_fact",
                "severity": "high",
                "item_index": 1,
                "field": "chinese_title",
                "message": "Unreleased model version should not be published.",
                "evidence": "GPT-5.6",
            }
        ]

        with patch("src.quality_gate._run_llm_review", return_value=(llm_issues, [], [])):
            reviewed, report = review_daily(
                news,
                api_key="test-key",
                model="test-model",
                strict=True,
                target_count=3,
                filter_high_risk=True,
            )

        self.assertEqual(
            [item["chinese_title"] for item in reviewed],
            ["Official model update", "Roblox AI tool", "NotebookLM update"],
        )
        self.assertTrue(report["pass"])
        self.assertFalse(report["blocked_publish"])
        self.assertEqual(report["risk_level"], "low")
        self.assertEqual(report["publish_filter"]["removed_count"], 1)
        self.assertEqual(report["publish_filter"]["selected_count"], 3)
        self.assertEqual(
            report["publish_filter"]["removed_items"][0]["title"],
            "Community test mentions GPT-5.6",
        )

    def test_llm_review_failure_is_reported_as_medium_risk(self):
        news = [
            _item("Official model update"),
            _item("Roblox AI tool"),
        ]

        with patch(
            "src.quality_gate._run_llm_review",
            return_value=([], [], ["LLM 质检请求失败: invalid JSON"]),
        ):
            reviewed, report = review_daily(
                news,
                api_key="test-key",
                model="test-model",
                strict=True,
                target_count=2,
                filter_high_risk=True,
            )

        self.assertEqual([item["chinese_title"] for item in reviewed], ["Official model update", "Roblox AI tool"])
        self.assertTrue(report["pass"])
        self.assertEqual(report["risk_level"], "medium")
        self.assertFalse(report["blocked_publish"])
        self.assertTrue(report["llm_review_failed"])
        self.assertIn("LLM 质检失败", report["summary"])


if __name__ == "__main__":
    unittest.main()
