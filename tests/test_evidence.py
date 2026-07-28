import unittest

from src.evidence import (
    normalize_shadow_evidence,
    preserve_source_evidence,
    source_evidence_text,
)
from src.llm_config import resolve_quality_llm_config


class EvidenceTests(unittest.TestCase):
    def test_github_changelog_pointer_is_not_treated_as_release_evidence(self):
        result = normalize_shadow_evidence(
            {
                "source_type": "github",
                "source_title": "acme/agent-kit v1.2.0",
                "source_url": "https://github.com/acme/agent-kit/releases/tag/v1.2.0",
                "github_evidence": {
                    "project_description": (
                        '<p align="center"><img src="https://example.test/logo.png"></p>\n'
                        "# Agent Kit\n\nA toolkit for reliable coding agents."
                    ),
                    "release_notes": "See CHANGELOG.md for this release's notes.",
                    "release_tag": "v1.2.0",
                },
            }
        )

        self.assertEqual(result.content_quality, "missing")
        self.assertIn("Agent Kit", result.details["project_purpose"])
        self.assertNotIn("http", result.details["project_purpose"])
        self.assertEqual(result.details["release_changes"], "")

    def test_hn_rss_metadata_is_not_treated_as_news_summary(self):
        result = normalize_shadow_evidence(
            {
                "source": "Hacker News AI",
                "source_type": "rss",
                "source_title": "A useful AI launch",
                "source_url": "https://news.ycombinator.com/item?id=123",
                "source_summary": (
                    "Article URL: https://example.com/launch\n"
                    "Comments URL: https://news.ycombinator.com/item?id=123\n"
                    "Points: 77"
                ),
            }
        )

        self.assertEqual(result.content_quality, "metadata_only")
        self.assertEqual(result.url, "https://example.com/launch")
        self.assertEqual(result.summary, "")

    def test_hn_text_is_cleaned_before_being_used_as_shadow_evidence(self):
        result = normalize_shadow_evidence(
            {
                "source": "Hacker News",
                "source_type": "hn",
                "source_title": "Show HN: Useful tool",
                "source_url": "https://news.ycombinator.com/item?id=124",
                "source_summary": "<p>A concise explanation of an AI tool for research teams.</p>",
            }
        )

        self.assertEqual(result.content_quality, "ready")
        self.assertEqual(result.summary, "A concise explanation of an AI tool for research teams.")

    def test_hn_external_article_is_used_when_story_has_no_hn_text(self):
        fetched_urls = []

        def fetch_article_text(url):
            fetched_urls.append(url)
            return "The company released an AI search product with enterprise controls."

        result = normalize_shadow_evidence(
            {
                "source": "Hacker News",
                "source_type": "hn",
                "source_title": "Company launches AI search",
                "source_url": "https://example.com/ai-search",
                "source_summary": "",
            },
            fetch_article_text=fetch_article_text,
        )

        self.assertEqual(fetched_urls, ["https://example.com/ai-search"])
        self.assertEqual(result.content_quality, "ready")
        self.assertEqual(result.summary, "The company released an AI search product with enterprise controls.")

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
