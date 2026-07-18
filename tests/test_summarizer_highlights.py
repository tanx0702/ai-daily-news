import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch

from src.summarizer import generate_cover_title, generate_highlights


class _FakeOpenAI:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
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
            {"items": [
                {"index": 2, "highlight": "Highlight B"},
                {"index": 1, "highlight": "Highlight A"},
                {"index": 3, "highlight": "Highlight C"},
            ]},
            ensure_ascii=False,
        )
        fake_client = _FakeOpenAI(response)

        with patch("src.summarizer.OpenAI", return_value=fake_client):
            highlights = generate_highlights(news, api_key="test-key")

        self.assertEqual(highlights, ["Highlight A", "Highlight B", "Highlight C"])
        self.assertEqual(fake_client.calls[0]["response_format"], {"type": "json_object"})

    def test_generate_cover_title_uses_json_object_response(self):
        news = [{"chinese_title": "模型更新带来企业新用法", "summary": "来源证据支持该产品更新。"}]
        fake_client = _FakeOpenAI(json.dumps({"cover_title": "模型更新带来新用法"}))

        with patch("src.summarizer.OpenAI", return_value=fake_client):
            title = generate_cover_title(news, api_key="test-key")

        self.assertEqual(title, "模型更新带来新用法")
        self.assertEqual(fake_client.calls[0]["response_format"], {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
