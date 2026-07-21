import unittest

from src.evidence import preserve_source_evidence, source_evidence_text
from src.llm_config import resolve_quality_llm_config


class EvidenceTests(unittest.TestCase):
    def test_source_summary_survives_generated_summary_replacement(self):
        item = preserve_source_evidence(
            {
                "title": "Original title",
                "summary": "Original source facts",
                "url": "https://example.test/story",
                "source": "Example News",
            }
        )

        item["summary"] = "Generated display summary"

        self.assertEqual(item["source_title"], "Original title")
        self.assertEqual(item["source_summary"], "Original source facts")
        self.assertEqual(item["source_url"], "https://example.test/story")
        self.assertEqual(item["source_name"], "Example News")
        self.assertEqual(
            source_evidence_text(item),
            "Original title\nOriginal source facts",
        )

    def test_existing_source_evidence_is_not_overwritten(self):
        item = preserve_source_evidence(
            {
                "title": "New title",
                "summary": "New summary",
                "source_title": "Preserved title",
                "source_summary": "Preserved summary",
            }
        )

        self.assertEqual(item["source_title"], "Preserved title")
        self.assertEqual(item["source_summary"], "Preserved summary")

    def test_quality_model_overrides_text_model(self):
        import os
        from unittest.mock import patch

        with patch.dict(
            os.environ,
            {
                "LLM_API_KEY": "writer-key",
                "LLM_MODEL": "writer-model",
                "LLM_API_BASE": "https://writer.test/v1",
                "QUALITY_LLM_API_KEY": "reviewer-key",
                "QUALITY_LLM_MODEL": "reviewer-model",
                "QUALITY_LLM_API_BASE": "https://reviewer.test/v1",
            },
            clear=True,
        ):
            config = resolve_quality_llm_config()

        self.assertEqual(config.api_key, "reviewer-key")
        self.assertEqual(config.model, "reviewer-model")
        self.assertEqual(config.base_url, "https://reviewer.test/v1")

    def test_quality_model_falls_back_to_text_model(self):
        import os
        from unittest.mock import patch

        with patch.dict(
            os.environ,
            {
                "LLM_API_KEY": "writer-key",
                "LLM_MODEL": "writer-model",
                "LLM_API_BASE": "https://writer.test/v1",
            },
            clear=True,
        ):
            config = resolve_quality_llm_config()

        self.assertEqual(config.api_key, "writer-key")
        self.assertEqual(config.model, "writer-model")
        self.assertEqual(config.base_url, "https://writer.test/v1")


if __name__ == "__main__":
    unittest.main()
