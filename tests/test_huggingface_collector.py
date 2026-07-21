import unittest
from unittest.mock import patch

from src.collectors.huggingface import HuggingFaceCollector


class HuggingFaceCollectorTests(unittest.TestCase):
    def test_fetch_accepts_tag_classified_model_with_high_downloads(self):
        model = {
            "id": "acme/recent-model",
            "tags": ["text-generation", "transformers"],
            "likes": 4,
            "downloads": 8000,
            "lastModified": "2026-07-18T08:00:00Z",
            "description": "A recently updated language model.",
        }
        collector = HuggingFaceCollector()

        with patch.object(collector, "_fetch_models", side_effect=[[model], [model]]):
            candidates = collector.fetch()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["_hf_category"], "文本生成")
        self.assertIn("recently updated language model", candidates[0]["summary"])
        self.assertEqual(candidates[0]["metrics"]["hf_downloads"], 8000)


if __name__ == "__main__":
    unittest.main()
