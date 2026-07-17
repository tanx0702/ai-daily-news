import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch

from src.summarizer import generate_highlights


class _FakeOpenAI:
    def __init__(self, response):
        self.response = response
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.response)
                )
            ]
        )


class SummarizerHighlightsTests(unittest.TestCase):
    def test_generate_highlights_skips_quality_excluded_items(self):
        news = [
            {
                "chinese_title": "Excluded story",
                "summary": "Should not be highlighted.",
                "_highlight_excluded": "quality gate",
            },
            {
                "chinese_title": "Eligible one",
                "summary": "First eligible story.",
            },
            {
                "chinese_title": "Eligible two",
                "summary": "Second eligible story.",
            },
        ]

        highlights = generate_highlights(news, api_key="")

        self.assertEqual(highlights, ["Eligible one", "Eligible two"])

    def test_generate_highlights_maps_unordered_indexed_results(self):
        news = [
            {"chinese_title": "Story A", "summary": "Summary A"},
            {"chinese_title": "Story B", "summary": "Summary B"},
            {"chinese_title": "Story C", "summary": "Summary C"},
        ]
        response = json.dumps(
            [
                {"index": 2, "highlight": "Highlight B"},
                {"index": 1, "highlight": "Highlight A"},
                {"index": 3, "highlight": "Highlight C"},
            ],
            ensure_ascii=False,
        )

        with patch("src.summarizer.OpenAI", return_value=_FakeOpenAI(response)):
            highlights = generate_highlights(news, api_key="test-key")

        self.assertEqual(highlights, ["Highlight A", "Highlight B", "Highlight C"])


if __name__ == "__main__":
    unittest.main()
