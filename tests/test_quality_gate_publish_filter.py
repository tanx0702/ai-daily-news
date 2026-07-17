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
