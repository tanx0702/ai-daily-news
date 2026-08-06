import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.quality_gate import _run_llm_review, review_daily


def _item(title, source_type="rss"):
    return {
        "title": title,
        "source_title": title,
        "source_summary": "This source has enough factual detail to support a publishable summary.",
        "chinese_title": f"中文标题：{title}",
        "summary": "Short summary",
        "source": "Example",
        "source_type": source_type,
        "metrics": {},
    }


def _llm_response(content, finish_reason="stop"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]
    )


class _FakeLlmClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class QualityGatePublishFilterTests(unittest.TestCase):
    def test_llm_review_isolates_a_failed_item_and_continues_with_later_items(self):
        client = _FakeLlmClient([
            _llm_response("", finish_reason="length"),
            _llm_response("", finish_reason="length"),
            _llm_response('{"issues":[{"item_index":1,"severity":"medium"}],'
                          '"fixes":[],"global_notes":["second item reviewed"]}'),
        ])

        with patch("openai.OpenAI", return_value=client):
            issues, fixes, notes, failures = _run_llm_review(
                [_item("One"), _item("Two")],
                api_key="test-key",
                model="test-model",
                base_url="https://example.test/v1",
                timeout=5,
            )

        self.assertEqual(len(client.calls), 3)
        self.assertEqual([issue["item_index"] for issue in issues], [2])
        self.assertEqual(fixes, [])
        self.assertEqual(notes, ["second item reviewed"])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["item_index"], 1)
        self.assertIn("finish_reason=length", failures[0]["reason"])

    def test_llm_review_retries_an_empty_length_response_once(self):
        client = _FakeLlmClient([
            _llm_response("", finish_reason="length"),
            _llm_response('{"issues":[],"fixes":[],"global_notes":[]}'),
        ])

        with patch("openai.OpenAI", return_value=client):
            issues, fixes, notes, failures = _run_llm_review(
                [_item("One")],
                api_key="test-key",
                model="test-model",
                base_url="https://example.test/v1",
                timeout=5,
            )

        self.assertEqual(len(client.calls), 2)
        self.assertEqual((issues, fixes, notes, failures), ([], [], [], []))

    def test_llm_review_keeps_the_fail_closed_fallback_when_client_creation_fails(self):
        with patch("openai.OpenAI", side_effect=RuntimeError("client unavailable")):
            issues, fixes, notes, failures = _run_llm_review(
                [_item("One")],
                api_key="test-key",
                model="test-model",
                base_url="https://example.test/v1",
                timeout=5,
            )

        self.assertEqual((issues, fixes), ([], []))
        self.assertEqual(notes, ["LLM quality review failed: client unavailable"])
        self.assertEqual([failure["item_index"] for failure in failures], [1])

    def test_review_daily_replaces_an_item_with_failed_llm_review(self):
        selected = [
            {**_item("Failed review"), "source": "Source A", "topic_key": "failed", "_score": 100},
            {**_item("Reviewed item"), "source": "Source B", "topic_key": "reviewed", "_score": 90},
        ]
        reserves = [
            {**_item("Reviewed reserve"), "source": "Source C", "topic_key": "reserve", "_score": 80},
        ]
        failures = [{"item_index": 1, "attempts": 2, "reason": "finish_reason=length"}]

        with patch("src.quality_gate._run_llm_review", return_value=([], [], [], failures)):
            reviewed, report = review_daily(
                selected,
                reserves=reserves,
                api_key="test-key",
                model="test-model",
                target_count=2,
                filter_high_risk=True,
                max_items_per_source=2,
                max_items_per_topic=2,
                min_primary_or_research=0,
            )

        self.assertEqual([item["title"] for item in reviewed], ["Reviewed item", "Reviewed reserve"])
        self.assertEqual(report["llm_review_status"], "partial")
        self.assertFalse(report["llm_review_failed"])
        self.assertEqual(report["llm_review_item_failure_count"], 1)
        self.assertEqual(report["publish_filter"]["removed_count"], 1)

    def test_review_daily_keeps_fail_closed_status_when_every_llm_review_fails(self):
        failures = [
            {"item_index": 1, "attempts": 2, "reason": "finish_reason=length"},
            {"item_index": 2, "attempts": 2, "reason": "finish_reason=length"},
        ]

        with patch("src.quality_gate._run_llm_review", return_value=([], [], [], failures)):
            _, report = review_daily(
                [_item("One"), _item("Two")],
                api_key="test-key",
                model="test-model",
                target_count=2,
                filter_high_risk=True,
            )

        self.assertEqual(report["llm_review_status"], "failed")
        self.assertTrue(report["llm_review_failed"])

    def test_high_risk_selected_item_uses_only_quota_compliant_reserve(self):
        selected = [
            {**_item("Unsafe source"), "source": "Unsafe publisher", "topic_key": "unsafe", "_score": 100},
            {**_item("Publisher A first"), "source": "Publisher A", "topic_key": "platform", "_score": 90},
            {**_item("Publisher A second"), "source": "Publisher A", "topic_key": "research", "_score": 80},
        ]
        reserves = [
            {**_item("Publisher A third"), "source": "Publisher A", "topic_key": "models", "_score": 70},
            {**_item("Publisher B replacement"), "source": "Publisher B", "topic_key": "models", "_score": 60},
        ]
        llm_issues = [{
            "type": "unsupported_claim",
            "severity": "high",
            "item_index": 1,
            "field": "summary",
            "message": "Unsupported claim.",
            "evidence": "source evidence",
        }]

        with patch("src.quality_gate._run_llm_review", return_value=(llm_issues, [], [], [])):
            reviewed, report = review_daily(
                selected,
                reserves=reserves,
                api_key="test-key",
                model="test-model",
                target_count=3,
                filter_high_risk=True,
                max_items_per_source=2,
                max_items_per_topic=2,
                min_primary_or_research=0,
            )

        self.assertEqual(
            [item["title"] for item in reviewed],
            ["Publisher A first", "Publisher A second", "Publisher B replacement"],
        )
        self.assertEqual(report["publish_filter"]["replaced_count"], 1)
        self.assertEqual(report["publish_filter"]["selected_count"], 3)
        self.assertFalse(report["blocked_publish"])

    def test_sparse_source_evidence_keeps_existing_translation_without_placeholder_summary(self):
        item = {
            **_item("Original source title"),
            "source_title": "Original source title",
            "source_summary": "",
            "source_url": "https://example.test/source",
            "chinese_title": "保留的中文翻译标题",
            "summary": "生成的细节，原始来源并未提供。",
        }

        reviewed, report = review_daily([item])

        self.assertEqual(reviewed[0]["quality_state"], "source_only")
        self.assertEqual(reviewed[0]["chinese_title"], "保留的中文翻译标题")
        self.assertEqual(reviewed[0]["summary"], "生成的细节，原始来源并未提供。")
        self.assertFalse(report["blocked_publish"])

    def test_sparse_source_only_selected_item_is_replaced_by_ready_reserve(self):
        selected = [{
            **_item("Sparse source"),
            "source_title": "Sparse source",
            "source_summary": "",
            "chinese_title": "稀疏来源的中文标题",
            "summary": "没有足够证据支撑的摘要。",
            "topic_key": "sparse",
            "_score": 100,
        }]
        reserves = [{
            **_item("Ready reserve"),
            "source_title": "Ready reserve",
            "source_summary": "This source contains enough factual detail to support a publishable summary.",
            "chinese_title": "可发布的备用新闻",
            "summary": "这是一条有完整来源证据的摘要。",
            "topic_key": "ready",
            "_score": 80,
        }]

        reviewed, report = review_daily(
            selected,
            reserves=reserves,
            target_count=1,
            filter_high_risk=True,
            max_items_per_source=2,
            max_items_per_topic=2,
            min_primary_or_research=0,
        )

        self.assertEqual([item["title"] for item in reviewed], ["Ready reserve"])
        self.assertEqual(report["publish_filter"]["source_only_excluded_count"], 1)
        self.assertEqual(report["publish_filter"]["selected_count"], 1)

    def test_untranslated_selected_item_is_replaced_by_chinese_ready_reserve(self):
        selected = [{
            **_item("English headline"),
            "chinese_title": "English headline",
            "summary": "The source contains enough English factual detail for publication.",
            "topic_key": "english",
            "_score": 100,
        }]
        reserves = [{
            **_item("Chinese reserve"),
            "chinese_title": "中文备用新闻",
            "summary": "这是一条有完整来源证据的中文摘要。",
            "topic_key": "chinese",
            "_score": 80,
        }]

        reviewed, report = review_daily(
            selected,
            reserves=reserves,
            target_count=1,
            filter_high_risk=True,
            max_items_per_source=2,
            max_items_per_topic=2,
            min_primary_or_research=0,
        )

        self.assertEqual([item["title"] for item in reviewed], ["Chinese reserve"])
        self.assertEqual(report["publish_filter"]["translation_missing_excluded_count"], 1)
        self.assertEqual(report["publish_filter"]["selected_count"], 1)

    def test_strict_mode_never_blocks_the_daily_draft(self):
        news = [_item("Unsafe item")]
        llm_issues = [{
            "type": "unsupported_claim",
            "severity": "high",
            "item_index": 1,
            "field": "summary",
            "message": "Unsupported claim.",
            "evidence": "source evidence",
        }]

        with patch("src.quality_gate._run_llm_review", return_value=(llm_issues, [], [], [])):
            reviewed, report = review_daily(
                news,
                api_key="test-key",
                model="test-model",
                strict=True,
                target_count=1,
                filter_high_risk=True,
            )

        self.assertEqual(reviewed, [])
        self.assertFalse(report["blocked_publish"])
        self.assertTrue(report["publish_filter"]["insufficient_publishable_items"])

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

        with patch("src.quality_gate._run_llm_review", return_value=(llm_issues, [], [], [])):
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
            [
                "中文标题：Official model update",
                "中文标题：Roblox AI tool",
            ],
        )
        self.assertTrue(report["pass"])
        self.assertFalse(report["blocked_publish"])
        self.assertEqual(report["risk_level"], "medium")
        self.assertEqual(report["publish_filter"]["removed_count"], 1)
        self.assertEqual(report["publish_filter"]["selected_count"], 2)
        self.assertTrue(report["publish_filter"]["insufficient_publishable_items"])
        self.assertEqual(
            report["publish_filter"]["removed_items"][0]["title"],
            "中文标题：Community test mentions GPT-5.6",
        )

    def test_llm_review_failure_is_reported_as_medium_risk(self):
        news = [
            _item("Official model update"),
            _item("Roblox AI tool"),
        ]

        with patch(
            "src.quality_gate._run_llm_review",
            return_value=(
                [],
                [],
                ["LLM quality review failed: invalid JSON"],
                [
                    {"item_index": 1, "attempts": 2, "reason": "invalid JSON"},
                    {"item_index": 2, "attempts": 2, "reason": "invalid JSON"},
                ],
            ),
        ):
            reviewed, report = review_daily(
                news,
                api_key="test-key",
                model="test-model",
                strict=True,
                target_count=2,
                filter_high_risk=True,
            )

        self.assertEqual(reviewed, [])
        self.assertTrue(report["pass"])
        self.assertEqual(report["risk_level"], "medium")
        self.assertFalse(report["blocked_publish"])
        self.assertTrue(report["llm_review_failed"])
        self.assertEqual(report["llm_review_item_failure_count"], 2)

    def test_llm_review_skips_candidates_without_publishable_source_evidence(self):
        sparse = {
            **_item("Sparse source"),
            "source_summary": "",
            "chinese_title": "证据不足候选",
            "summary": "不应送入质检模型。",
        }
        ready = _item("Ready source")
        reviewed_inputs = []

        def fake_review(items, **kwargs):
            reviewed_inputs.extend(items)
            return [], [], [], []

        with patch("src.quality_gate._run_llm_review", side_effect=fake_review):
            _, report = review_daily(
                [sparse, ready],
                api_key="test-key",
                model="test-model",
                target_count=2,
                filter_high_risk=True,
            )

        self.assertEqual([item["title"] for item in reviewed_inputs], ["Ready source"])
        self.assertEqual(report["llm_input_count"], 1)


if __name__ == "__main__":
    unittest.main()
