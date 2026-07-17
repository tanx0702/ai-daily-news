import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.summarizer import summarize_news


class _FakeOpenAI:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("No fake LLM response left")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=response)
                )
            ]
        )


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _news(count):
    return [
        {
            "title": f"Original story {i}",
            "url": f"https://example.com/{i}",
            "source": "Example",
        }
        for i in range(1, count + 1)
    ]


class SummarizerBatchValidationTests(unittest.TestCase):
    def test_batch_count_mismatch_falls_back_per_item(self):
        batch_response = _json([
            {"chinese_title": "错位 1", "summary": "摘要 1"},
            {"chinese_title": "错位 2", "summary": "摘要 2"},
            {"chinese_title": "错位 3", "summary": "摘要 3"},
            {"chinese_title": "错位 4", "summary": "摘要 4"},
        ])
        single_responses = [
            _json({"chinese_title": f"单条 {i}", "summary": f"单条摘要 {i}"})
            for i in range(1, 6)
        ]
        fake_client = _FakeOpenAI([batch_response, *single_responses])

        with patch("src.summarizer.OpenAI", return_value=fake_client):
            result = summarize_news(_news(5), api_key="test-key")

        self.assertEqual(
            [item["chinese_title"] for item in result],
            ["单条 1", "单条 2", "单条 3", "单条 4", "单条 5"],
        )
        self.assertEqual(len(fake_client.calls), 6)

    def test_batch_index_maps_unordered_results_to_original_items(self):
        batch_response = _json([
            {"index": 2, "chinese_title": "第二条", "summary": "摘要二"},
            {"index": 1, "chinese_title": "第一条", "summary": "摘要一"},
            {"index": 3, "chinese_title": "第三条", "summary": "摘要三"},
        ])
        fake_client = _FakeOpenAI([batch_response])

        with patch("src.summarizer.OpenAI", return_value=fake_client):
            result = summarize_news(_news(3), api_key="test-key")

        self.assertEqual(
            [item["chinese_title"] for item in result],
            ["第一条", "第二条", "第三条"],
        )
        self.assertEqual([item["summary"] for item in result], ["摘要一", "摘要二", "摘要三"])
        self.assertEqual(len(fake_client.calls), 1)

    def test_duplicate_or_missing_index_falls_back_per_item(self):
        batch_response = _json([
            {"index": 1, "chinese_title": "重复一", "summary": "摘要一"},
            {"index": 1, "chinese_title": "重复二", "summary": "摘要二"},
            {"index": 2, "chinese_title": "第二条", "summary": "摘要三"},
        ])
        single_responses = [
            _json({"chinese_title": f"单条 {i}", "summary": f"单条摘要 {i}"})
            for i in range(1, 4)
        ]
        fake_client = _FakeOpenAI([batch_response, *single_responses])

        with patch("src.summarizer.OpenAI", return_value=fake_client):
            result = summarize_news(_news(3), api_key="test-key")

        self.assertEqual(
            [item["chinese_title"] for item in result],
            ["单条 1", "单条 2", "单条 3"],
        )
        self.assertEqual(len(fake_client.calls), 4)


if __name__ == "__main__":
    unittest.main()
