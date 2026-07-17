import os
import unittest
from unittest.mock import patch

import src.main as daily_main


def _news_items(count):
    return [
        {
            "title": f"Story {i}",
            "chinese_title": f"Story {i}",
            "summary": "Summary",
            "source": "Example",
            "source_type": "rss",
            "metrics": {},
            "scores": {},
        }
        for i in range(count)
    ]


class MainPublishFilterTests(unittest.TestCase):
    def test_skip_wechat_draft_flag_is_opt_in(self):
        with patch.dict(os.environ, {"SKIP_WECHAT_DRAFT": "true"}, clear=True):
            self.assertTrue(daily_main._should_skip_wechat_draft())

        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(daily_main._should_skip_wechat_draft())

    def test_pipeline_uses_separate_text_and_image_model_config(self):
        collected = _news_items(10)
        calls = {}

        def fake_summarize(news, **kwargs):
            calls["summarize"] = kwargs
            return news

        def fake_review(news, **kwargs):
            calls["review"] = kwargs
            return news, {
                "pass": True,
                "risk_level": "low",
                "blocked_publish": False,
                "issues": [],
                "applied_fixes": [],
            }

        def fake_highlights(news, **kwargs):
            calls["highlights"] = kwargs
            return []

        def fake_cover_title(news, **kwargs):
            calls["cover_title"] = kwargs
            return "Cover title"

        def fake_cover(*args, **kwargs):
            calls["cover"] = kwargs
            return kwargs.get("output_path")

        env = {
            "DAILY_TOP_N": "10",
            "DAILY_SAFETY_RESERVE_N": "0",
            "ENABLE_LLM_QUALITY_GATE": "1",
            "ENABLE_PUBLISH_SAFETY_FILTER": "0",
            "ENABLE_ARTICLE_IMAGE_FETCH": "0",
            "LLM_API_KEY": "text-key",
            "LLM_API_BASE": "https://text.example/v1",
            "LLM_MODEL": "text-model",
            "IMAGE_API_KEY": "image-key",
            "IMAGE_API_BASE": "https://image.example",
            "IMAGE_MODEL": "image-model",
            "AGNES_API_KEY": "",
            "AGNES_API_BASE": "",
            "AGNES_MODEL": "",
            "OPENAI_API_KEY": "",
            "OPENAI_API_BASE": "",
            "OPENAI_MODEL": "",
        }

        with patch.dict(os.environ, env), \
             patch("src.collector.collect_news", return_value=collected), \
             patch("src.summarizer.summarize_news", side_effect=fake_summarize), \
             patch("src.quality_gate.review_daily", side_effect=fake_review), \
             patch("src.summarizer.generate_highlights", side_effect=fake_highlights), \
             patch("src.summarizer.generate_cover_title", side_effect=fake_cover_title), \
             patch("src.pipeline_artifacts.render_and_save_daily_html"), \
             patch("src.pipeline_artifacts.render_and_save_wechat_preview"), \
             patch("src.pipeline_artifacts.build_latest_data", return_value={}), \
             patch("src.pipeline_artifacts.save_latest_data", return_value="latest.json"), \
             patch("src.cover.select_cover_subject", return_value={"mode": "generic", "cover_title": "Today"}), \
             patch("src.cover.generate_cover_from_news", side_effect=fake_cover), \
             patch("src.wechat_draft.publish_daily_article", return_value={"status": "draft_created"}), \
             patch("src.main._generate_debug_reports"):
            daily_main._run_pipeline()

        self.assertEqual(calls["summarize"]["api_key"], "text-key")
        self.assertEqual(calls["summarize"]["model"], "text-model")
        self.assertEqual(calls["summarize"]["base_url"], "https://text.example/v1")
        self.assertEqual(calls["review"]["api_key"], "text-key")
        self.assertEqual(calls["review"]["model"], "text-model")
        self.assertEqual(calls["review"]["base_url"], "https://text.example/v1")
        self.assertEqual(calls["highlights"]["api_key"], "text-key")
        self.assertEqual(calls["highlights"]["model"], "text-model")
        self.assertEqual(calls["highlights"]["base_url"], "https://text.example/v1")
        self.assertEqual(calls["cover_title"]["api_key"], "text-key")
        self.assertEqual(calls["cover_title"]["model"], "text-model")
        self.assertEqual(calls["cover_title"]["base_url"], "https://text.example/v1")
        self.assertEqual(calls["cover"]["api_key"], "image-key")
        self.assertEqual(calls["cover"]["base_url"], "https://image.example")
        self.assertEqual(calls["cover"]["model"], "image-model")

    def test_pipeline_collects_reserve_candidates_and_enables_publish_filter(self):
        collected = _news_items(16)
        review_kwargs = {}

        def fake_review(news, **kwargs):
            review_kwargs.update(kwargs)
            return news[:10], {
                "pass": True,
                "risk_level": "low",
                "blocked_publish": False,
                "issues": [],
                "applied_fixes": [],
            }

        env = {
            "DAILY_TOP_N": "10",
            "DAILY_SAFETY_RESERVE_N": "6",
            "ENABLE_LLM_QUALITY_GATE": "1",
            "QUALITY_GATE_STRICT": "1",
            "ENABLE_ARTICLE_IMAGE_FETCH": "0",
            "LLM_API_KEY": "",
            "IMAGE_API_KEY": "",
            "AGNES_API_KEY": "",
            "OPENAI_API_KEY": "",
        }

        with patch.dict(os.environ, env), \
             patch("src.collector.collect_news", return_value=collected) as collect_news, \
             patch("src.quality_gate.review_daily", side_effect=fake_review), \
             patch("src.pipeline_artifacts.render_and_save_daily_html"), \
             patch("src.pipeline_artifacts.render_and_save_wechat_preview"), \
             patch("src.pipeline_artifacts.build_latest_data", return_value={}), \
             patch("src.pipeline_artifacts.save_latest_data", return_value="latest.json"), \
             patch("src.cover.select_cover_subject", return_value={"mode": "generic", "cover_title": "今日AI要闻"}), \
             patch("src.wechat_draft.publish_daily_article", return_value={"status": "draft_created"}), \
             patch("src.main._generate_debug_reports"):
            daily_main._run_pipeline()

        self.assertEqual(collect_news.call_args.kwargs["top_n"], 16)
        self.assertEqual(review_kwargs["target_count"], 10)
        self.assertTrue(review_kwargs["filter_high_risk"])

    def test_annotate_reasons_includes_publish_risk(self):
        item = {
            "title": "Story",
            "source_type": "rss",
            "metrics": {},
            "scores": {"freshness": 100},
            "_publish_risk": {
                "category": "single_source_financial_claim",
                "reason": "single-source financial claim",
            },
        }

        daily_main._annotate_reasons([item])

        self.assertIn("[发布风险]", item["selected_reason"])
        self.assertIn("single-source financial claim", item["selected_reason"])


if __name__ == "__main__":
    unittest.main()
