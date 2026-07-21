import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.summarizer import _apply_summary_item, summarize_news, validate_summary_facts


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
    def test_non_retryable_auth_error_stops_all_batch_fallback_requests(self):
        class AuthenticationFailure(Exception):
            status_code = 401

        fake_client = _FakeOpenAI([AuthenticationFailure("invalid API key")])

        with patch("src.summarizer.OpenAI", return_value=fake_client):
            result = summarize_news(_news(6), api_key="test-key")

        self.assertEqual(len(fake_client.calls), 1)
        self.assertEqual(
            [item["chinese_title"] for item in result],
            [f"Original story {index}" for index in range(1, 7)],
        )
        self.assertTrue(all(item["llm_summary_status"] == "non_retryable_error" for item in result))

    def test_non_retryable_error_preserves_chinese_source_summary(self):
        class AuthenticationFailure(Exception):
            status_code = 401

        fake_client = _FakeOpenAI([AuthenticationFailure("invalid API key")])
        news = [{
            "title": "中文原始标题",
            "source_title": "中文原始标题",
            "source_summary": "原始中文摘要已经说明了发布内容和适用范围。",
            "source": "中文媒体",
        }]

        with patch("src.summarizer.OpenAI", return_value=fake_client):
            result = summarize_news(news, api_key="test-key")

        self.assertEqual(result[0]["chinese_title"], "中文原始标题")
        self.assertEqual(result[0]["summary"], "原始中文摘要已经说明了发布内容和适用范围。")

    def test_summary_validation_uses_immutable_source_evidence(self):
        original = {
            "title": "Original announcement",
            "summary": "GPT-7 appears in an earlier generated summary.",
            "source_title": "Original announcement",
            "source_summary": "The source only confirms a rename.",
        }

        validation = validate_summary_facts("GPT-7 正式发布", original)

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["action"], "fallback")

    def test_recent_github_push_uses_activity_wording_not_release_wording(self):
        news = {
            "title": "acme/agent: A useful coding agent",
            "source_title": "acme/agent: A useful coding agent",
            "source_summary": "GitHub 近期推送活跃项目，当前 500 stars、20 forks。A useful coding agent.",
            "source_type": "github",
            "metrics": {"github_activity_type": "recent_push"},
        }

        _apply_summary_item(
            news,
            {"chinese_title": "开源 AI 助手 acme/agent 正式发布", "summary": "该项目正式发布，面向代码开发工作流。"},
            "test",
        )

        self.assertEqual(news["chinese_title"], "GitHub 项目近期活跃：acme/agent")
        self.assertIn("近期推送活跃项目", news["summary"])
        self.assertNotIn("正式发布", news["summary"])

    def test_batch_count_mismatch_retries_once_then_keeps_source_fallback(self):
        batch_response = _json({"items": [
            {"chinese_title": "错位 1", "summary": "摘要 1"},
            {"chinese_title": "错位 2", "summary": "摘要 2"},
            {"chinese_title": "错位 3", "summary": "摘要 3"},
            {"chinese_title": "错位 4", "summary": "摘要 4"},
        ]})
        fake_client = _FakeOpenAI([batch_response, batch_response])

        with patch("src.summarizer.OpenAI", return_value=fake_client):
            result = summarize_news(_news(5), api_key="test-key")

        self.assertEqual(
            [item["chinese_title"] for item in result],
            [f"Original story {index}" for index in range(1, 6)],
        )
        self.assertEqual(len(fake_client.calls), 2)
        self.assertTrue(all(item["llm_summary_status"] == "invalid_response" for item in result))

    def test_batch_index_maps_unordered_results_to_original_items(self):
        batch_response = _json({"items": [
            {"index": 2, "chinese_title": "第二条", "summary": "摘要二"},
            {"index": 1, "chinese_title": "第一条", "summary": "摘要一"},
            {"index": 3, "chinese_title": "第三条", "summary": "摘要三"},
        ]})
        fake_client = _FakeOpenAI([batch_response])

        with patch("src.summarizer.OpenAI", return_value=fake_client):
            result = summarize_news(_news(3), api_key="test-key")

        self.assertEqual(
            [item["chinese_title"] for item in result],
            ["第一条", "第二条", "第三条"],
        )
        self.assertEqual([item["summary"] for item in result], ["摘要一", "摘要二", "摘要三"])
        self.assertEqual(len(fake_client.calls), 1)

    def test_batch_prompt_includes_source_evidence_for_each_news_item(self):
        response = _json({"items": [
            {"index": 1, "chinese_title": "来源证据新闻", "summary": "来源证据支持的中文摘要。"},
        ]})
        fake_client = _FakeOpenAI([response])
        news = [{
            "title": "Original title",
            "source_title": "Original title",
            "source_summary": "The official announcement confirms a product rename and rollout date.",
            "source": "Example News",
            "source_type": "rss",
            "url": "https://example.com/original",
        }]

        with patch("src.summarizer.OpenAI", return_value=fake_client):
            summarize_news(news, api_key="test-key")

        prompt = fake_client.calls[0]["messages"][1]["content"]
        self.assertIn("来源: Example News", prompt)
        self.assertIn("原始摘要: The official announcement confirms a product rename and rollout date.", prompt)
        self.assertEqual(fake_client.calls[0]["response_format"], {"type": "json_object"})

    def test_github_release_prompt_includes_project_and_change_evidence(self):
        response = _json({"items": [{
            "index": 1,
            "chinese_title": "Release Agent 发布 v1.4.0",
            "summary": "Release Agent 是为编码智能体应用仓库规则的工具。本次新增规则包和 TypeScript 集成，方便团队统一开发约束。",
        }]})
        fake_client = _FakeOpenAI([response])
        news = [{
            "title": "acme/release-agent v1.4.0",
            "source_title": "acme/release-agent v1.4.0",
            "source_summary": "GitHub Release v1.4.0.",
            "source": "GitHub",
            "source_type": "github",
            "url": "https://github.com/acme/release-agent/releases/tag/v1.4.0",
            "github_evidence": {
                "project_description": "A tool that applies repository rules to coding agents.",
                "release_notes": "Adds repository rule packs and a TypeScript integration for coding agents.",
                "release_tag": "v1.4.0",
            },
        }]

        with patch("src.summarizer.OpenAI", return_value=fake_client):
            summarize_news(news, api_key="test-key")

        system_prompt = fake_client.calls[0]["messages"][0]["content"]
        evidence_prompt = fake_client.calls[0]["messages"][1]["content"]
        self.assertIn("项目是什么", system_prompt)
        self.assertIn("项目用途: A tool that applies repository rules to coding agents.", evidence_prompt)
        self.assertIn("本次 Release v1.4.0: Adds repository rule packs", evidence_prompt)

    def test_duplicate_or_missing_index_retries_once_then_uses_source_fallback(self):
        batch_response = _json({"items": [
            {"index": 1, "chinese_title": "重复一", "summary": "摘要一"},
            {"index": 1, "chinese_title": "重复二", "summary": "摘要二"},
            {"index": 2, "chinese_title": "第二条", "summary": "摘要三"},
        ]})
        fake_client = _FakeOpenAI([batch_response, batch_response])

        with patch("src.summarizer.OpenAI", return_value=fake_client):
            result = summarize_news(_news(3), api_key="test-key")

        self.assertEqual(
            [item["chinese_title"] for item in result],
            ["Original story 1", "Original story 2", "Original story 3"],
        )
        self.assertEqual(len(fake_client.calls), 2)


if __name__ == "__main__":
    unittest.main()
