import unittest
from unittest.mock import patch

from src.quality_gate import _build_llm_input, review_daily


class EvidenceQualityGateTests(unittest.TestCase):
    def test_llm_input_compares_source_and_generated_summaries(self):
        payload = _build_llm_input(
            [
                {
                    "source_title": "NotebookLM is now Gemini Notebook",
                    "source_summary": "The announcement only confirms a rename.",
                    "summary": "The product gained deeper integrations.",
                    "chinese_title": "NotebookLM 更名",
                }
            ]
        )

        self.assertEqual(
            payload[0]["original_summary"],
            "The announcement only confirms a rename.",
        )
        self.assertEqual(
            payload[0]["generated_summary"],
            "The product gained deeper integrations.",
        )

    def test_failed_llm_review_is_not_reported_as_reviewed(self):
        item = {
            "source_title": "Original",
            "source_summary": "Original summary",
            "title": "Original",
            "summary": "Generated summary",
            "chinese_title": "Generated title",
            "source_type": "rss",
            "metrics": {},
        }
        with patch("src.quality_gate._run_llm_review", return_value=([], [], ["LLM review failed: timeout"])):
            _, report = review_daily(
                [item],
                api_key="key",
                model="model",
                base_url="https://example.test/v1",
            )

        self.assertEqual(report["llm_review_status"], "failed")
        self.assertFalse(report["llm_reviewed"])


if __name__ == "__main__":
    unittest.main()
