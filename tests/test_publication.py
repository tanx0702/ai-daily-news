import unittest

from src.publication import evaluate_publish_readiness


def _item(source, state="ready"):
    return {"source": source, "quality_state": state}


class PublishReadinessTests(unittest.TestCase):
    def test_allows_diverse_short_edition_after_successful_quality_review(self):
        items = [
            _item("Source A"), _item("Source A"), _item("Source A"),
            _item("Source B"), _item("Source C"), _item("Source D"),
        ]

        result = evaluate_publish_readiness(
            items,
            {"llm_review_status": "passed", "risk_level": "low"},
        )

        self.assertTrue(result["ready"])
        self.assertEqual(result["selected_count"], 6)
        self.assertEqual(result["source_counts"], {"Source A": 3, "Source B": 1, "Source C": 1, "Source D": 1})

    def test_blocks_draft_when_quality_review_fails_or_one_source_dominates(self):
        result = evaluate_publish_readiness(
            [_item("Only Source") for _ in range(6)],
            {"llm_review_status": "failed", "risk_level": "medium"},
        )

        self.assertFalse(result["ready"])
        self.assertIn("quality_review_failed", result["reasons"])
        self.assertIn("source_concentration", result["reasons"])

    def test_allows_draft_after_failed_review_items_are_replaced(self):
        items = [
            _item("Source A"), _item("Source A"), _item("Source A"),
            _item("Source B"), _item("Source C"), _item("Source D"),
        ]

        result = evaluate_publish_readiness(
            items,
            {"llm_review_status": "partial", "risk_level": "low"},
        )

        self.assertTrue(result["ready"])
        self.assertEqual(result["quality_review_status"], "partial")

    def test_blocks_draft_when_too_few_ready_items_remain(self):
        result = evaluate_publish_readiness(
            [_item("Source A"), _item("Source B"), _item("Source C", state="source_only")],
            {"llm_review_status": "passed", "risk_level": "low"},
        )

        self.assertFalse(result["ready"])
        self.assertIn("insufficient_items", result["reasons"])
        self.assertIn("non_ready_item", result["reasons"])


if __name__ == "__main__":
    unittest.main()
