import os
import unittest
from unittest.mock import Mock, patch

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
    def test_editorial_mode_defaults_to_v1_and_rejects_unknown_values(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(daily_main._editorial_mode(), "v1")

        with patch.dict(os.environ, {"DAILY_EDITORIAL_MODE": "unexpected"}, clear=True):
            self.assertEqual(daily_main._editorial_mode(), "v1")

        with patch.dict(os.environ, {"DAILY_EDITORIAL_MODE": "v2_assist"}, clear=True):
            self.assertEqual(daily_main._editorial_mode(), "v2_assist")

    def test_v2_assist_selection_feeds_summary_quality_gate_and_private_debug_report(self):
        from src.workflows.production_editorial import ProductionEditorialResult

        base_items = _news_items(4)
        v1_selected = {**base_items[0], "id": "v1-selected", "title": "V1 selected", "source": "V1"}
        v1_reserve = {**base_items[1], "id": "v1-reserve", "title": "V1 reserve", "source": "V1 reserve"}
        v2_selected = {**base_items[2], "id": "v2-selected", "title": "V2 selected", "source": "V2"}
        v2_reserve = {**base_items[3], "id": "v2-reserve", "title": "V2 reserve", "source": "V2 reserve"}
        collected = [v1_selected, v1_reserve, v2_selected, v2_reserve]
        production_report = {
            "mode": "v2_assist",
            "status": "applied",
            "fallback_reason": "",
            "added_v2_selected_ids": ["v2-selected"],
        }
        summary_inputs = []
        quality_inputs = []
        debug_reports = []
        rendered_news = []

        def fake_summarize(items, **kwargs):
            summary_inputs.append([item["id"] for item in items])
            return items

        def fake_review(items, **kwargs):
            quality_inputs.append(
                ([item["id"] for item in items], [item["id"] for item in kwargs["reserves"]])
            )
            return [v2_reserve], {
                "pass": True,
                "risk_level": "low",
                "blocked_publish": False,
                "issues": [],
                "applied_fixes": [],
            }

        env = {
            "DAILY_EDITORIAL_MODE": "v2_assist",
            "DAILY_TOP_N": "1",
            "DAILY_CANDIDATE_POOL_N": "4",
            "ENABLE_LLM_QUALITY_GATE": "1",
            "ENABLE_PUBLISH_SAFETY_FILTER": "0",
            "ENABLE_ARTICLE_IMAGE_FETCH": "0",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "test-model",
            "LLM_API_BASE": "https://example.test/v1",
            "IMAGE_API_KEY": "",
            "AGNES_API_KEY": "",
            "OPENAI_API_KEY": "",
        }

        with patch.dict(os.environ, env, clear=True), \
             patch("src.collector.collect_news", return_value=collected), \
             patch(
                 "src.editorial_selection.select_editorial_candidates",
                 return_value=([v1_selected], [v1_reserve], {"selected_count": 1}),
             ), \
             patch(
                 "src.workflows.production_editorial.run_production_editorial",
                 return_value=ProductionEditorialResult(
                     [v2_selected], [v2_reserve], production_report
                 ),
             ) as assist, \
             patch("src.summarizer.summarize_news", side_effect=fake_summarize), \
             patch("src.quality_gate.review_daily", side_effect=fake_review), \
             patch(
                 "src.editorial_review.review_editorial_candidates",
                 return_value={"status": "passed", "applied_count": 0, "notes": []},
             ), \
             patch("src.summarizer.generate_highlights", return_value=[]), \
             patch("src.summarizer.generate_cover_title", return_value="Today"), \
             patch("src.editorial_quality.assess_daily_edition", return_value={"score": 9, "target": 9, "meets_target": True, "reasons": []}), \
             patch("src.collector.apply_final_editorial_dedup", side_effect=lambda items, **_: (items, {})), \
             patch(
                 "src.pipeline_artifacts.render_and_save_daily_html",
                 side_effect=lambda news, *args, **kwargs: rendered_news.append(
                     [item["id"] for item in news]
                 ),
             ), \
             patch("src.pipeline_artifacts.render_and_save_wechat_preview"), \
             patch("src.pipeline_artifacts.build_latest_data", return_value={}), \
             patch("src.pipeline_artifacts.save_latest_data", return_value="latest.json"), \
             patch("src.cover.select_cover_subject", return_value={"mode": "generic", "cover_title": "Today"}), \
             patch("src.cover.generate_cover_from_news", return_value="cover.jpg"), \
             patch("src.wechat_draft.publish_daily_article", return_value={"status": "draft_created"}), \
             patch(
                 "src.main._generate_debug_reports",
                 side_effect=lambda *args, **kwargs: debug_reports.append(kwargs),
             ):
            daily_main._run_pipeline()

        self.assertEqual(summary_inputs, [["v2-selected", "v2-reserve"]])
        self.assertEqual(quality_inputs, [(["v2-selected"], ["v2-reserve"])])
        self.assertEqual(rendered_news, [["v2-reserve"]])
        self.assertEqual(assist.call_args.kwargs["all_candidates"], collected)
        self.assertEqual(debug_reports[0]["production_editorial"], production_report)

    def test_pipeline_exit_code_reports_blocked_and_failed_publication(self):
        self.assertEqual(daily_main._pipeline_exit_code({"status": "draft_created"}), 0)
        self.assertEqual(daily_main._pipeline_exit_code({"status": "dry_run"}), 0)
        self.assertEqual(daily_main._pipeline_exit_code({"status": "blocked"}), 1)
        self.assertEqual(daily_main._pipeline_exit_code({"status": "failed"}), 1)

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

        def fake_editorial_review(news, **kwargs):
            calls["editorial_review"] = kwargs
            return {"status": "passed", "applied_count": len(news), "notes": []}

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
             patch("src.editorial_review.review_editorial_candidates", side_effect=fake_editorial_review), \
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
        self.assertEqual(calls["editorial_review"]["api_key"], "text-key")
        self.assertEqual(calls["editorial_review"]["model"], "text-model")
        self.assertEqual(calls["editorial_review"]["base_url"], "https://text.example/v1")
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

        self.assertEqual(collect_news.call_args.kwargs["top_n"], 30)
        self.assertEqual(review_kwargs["target_count"], 10)
        self.assertTrue(review_kwargs["filter_high_risk"])

    def test_pipeline_separates_editorial_selection_from_quality_reserves(self):
        collected = _news_items(30)
        selected = collected[:10]
        reserves = collected[10:]
        review_kwargs = {}
        annotate = Mock(side_effect=lambda items: items)

        def fake_review(news, **kwargs):
            review_kwargs.update(kwargs)
            return news, {
                "pass": True,
                "risk_level": "low",
                "blocked_publish": False,
                "issues": [],
                "applied_fixes": [],
            }

        env = {
            "DAILY_TOP_N": "10",
            "DAILY_CANDIDATE_POOL_N": "30",
            "ENABLE_LLM_QUALITY_GATE": "1",
            "ENABLE_PUBLISH_SAFETY_FILTER": "1",
            "ENABLE_ARTICLE_IMAGE_FETCH": "0",
            "LLM_API_KEY": "",
            "IMAGE_API_KEY": "",
            "AGNES_API_KEY": "",
            "OPENAI_API_KEY": "",
        }

        with patch.dict(os.environ, env), \
             patch("src.collector.collect_news", return_value=collected), \
             patch("src.editorial_quality.annotate_editorial_candidates", annotate), \
             patch("src.editorial_selection.select_editorial_candidates", return_value=(selected, reserves, {"selected_count": 10})), \
             patch("src.quality_gate.review_daily", side_effect=fake_review), \
             patch("src.pipeline_artifacts.render_and_save_daily_html"), \
             patch("src.pipeline_artifacts.render_and_save_wechat_preview"), \
             patch("src.pipeline_artifacts.build_latest_data", return_value={}), \
             patch("src.pipeline_artifacts.save_latest_data", return_value="latest.json"), \
             patch("src.cover.select_cover_subject", return_value={"mode": "generic", "cover_title": "今日AI要闻"}), \
             patch("src.wechat_draft.publish_daily_article", return_value={"status": "draft_created"}), \
             patch("src.main._generate_debug_reports"):
            daily_main._run_pipeline()

        self.assertEqual(review_kwargs["reserves"], reserves)
        self.assertEqual(review_kwargs["target_count"], 10)
        self.assertEqual(annotate.call_args.args[0], collected)

    def test_pipeline_reviews_only_quality_ready_candidates_before_final_selection(self):
        collected = [
            {
                **_news_items(1)[0],
                "title": "Ready media story",
                "chinese_title": "可发布媒体新闻",
                "source": "Trusted Media",
            },
            {
                **_news_items(1)[0],
                "title": "Source only story",
                "chinese_title": "证据不足新闻",
                "source": "Thin Source",
            },
            {
                **_news_items(1)[0],
                "title": "Replace story",
                "chinese_title": "应移除新闻",
                "source": "Risky Source",
            },
            {
                **_news_items(1)[0],
                "title": "GitHub activity",
                "chinese_title": "GitHub 项目动态",
                "source": "GitHub",
                "source_type": "github",
                "source_tier": "community",
                "metrics": {"github_activity_type": "push"},
            },
        ]
        editorial_inputs = []
        final_titles = []
        final_quality_reports = []

        def fake_review(selected_news, **kwargs):
            for item in [*selected_news, *kwargs["reserves"]]:
                item["quality_state"] = {
                    "Ready media story": "ready",
                    "Source only story": "source_only",
                    "Replace story": "replace",
                    "GitHub activity": "ready",
                }[item["title"]]
            return selected_news, {
                "pass": True,
                "risk_level": "low",
                "blocked_publish": False,
                "issues": [],
                "applied_fixes": [],
                "publish_filter": {"selected_count": len(selected_news)},
            }

        def fake_editorial_review(news, **kwargs):
            editorial_inputs.append([item["title"] for item in news])
            return {"status": "passed", "applied_count": 0, "notes": []}

        def fake_latest_data(news, *args, **kwargs):
            final_titles.append([item["title"] for item in news])
            final_quality_reports.append(kwargs["quality_report"])
            return {}

        env = {
            "DAILY_TOP_N": "2",
            "DAILY_CANDIDATE_POOL_N": "4",
            "ENABLE_LLM_QUALITY_GATE": "1",
            "ENABLE_PUBLISH_SAFETY_FILTER": "1",
            "ENABLE_ARTICLE_IMAGE_FETCH": "0",
            "LLM_API_KEY": "",
            "IMAGE_API_KEY": "",
            "AGNES_API_KEY": "",
            "OPENAI_API_KEY": "",
        }

        with patch.dict(os.environ, env), \
             patch("src.collector.collect_news", return_value=collected), \
             patch("src.quality_gate.review_daily", side_effect=fake_review), \
             patch("src.editorial_review.review_editorial_candidates", side_effect=fake_editorial_review), \
             patch("src.pipeline_artifacts.render_and_save_daily_html"), \
             patch("src.pipeline_artifacts.render_and_save_wechat_preview"), \
             patch("src.pipeline_artifacts.build_latest_data", side_effect=fake_latest_data), \
             patch("src.pipeline_artifacts.save_latest_data", return_value="latest.json"), \
             patch("src.cover.select_cover_subject", return_value={"mode": "generic", "cover_title": "Today"}), \
             patch("src.cover.generate_cover_from_news", return_value="cover.jpg"), \
             patch("src.wechat_draft.publish_daily_article", return_value={"status": "draft_created"}), \
             patch("src.main._generate_debug_reports"):
            daily_main._run_pipeline()

        self.assertEqual(editorial_inputs, [["Ready media story", "GitHub activity"]])
        self.assertEqual(final_titles, [["Ready media story"]])
        publish_filter = final_quality_reports[0]["publish_filter"]
        self.assertEqual(publish_filter["selected_count"], 1)
        self.assertTrue(publish_filter["insufficient_publishable_items"])
        self.assertEqual(publish_filter["selection"]["selected_count"], 1)

    def test_pipeline_generates_local_cover_when_image_key_is_missing(self):
        env = {
            "DAILY_TOP_N": "2",
            "DAILY_CANDIDATE_POOL_N": "2",
            "ENABLE_LLM_QUALITY_GATE": "0",
            "ENABLE_ARTICLE_IMAGE_FETCH": "0",
            "LLM_API_KEY": "",
            "IMAGE_API_KEY": "",
            "AGNES_API_KEY": "",
            "OPENAI_API_KEY": "",
        }

        with patch.dict(os.environ, env), \
             patch("src.collector.collect_news", return_value=_news_items(2)), \
             patch("src.pipeline_artifacts.render_and_save_daily_html"), \
             patch("src.pipeline_artifacts.render_and_save_wechat_preview"), \
             patch("src.pipeline_artifacts.build_latest_data", return_value={}), \
             patch("src.pipeline_artifacts.save_latest_data", return_value="latest.json"), \
             patch("src.cover.select_cover_subject", return_value={"mode": "generic", "cover_title": "今日AI要闻"}), \
             patch("src.cover.generate_cover_from_news", return_value="cover.jpg") as generate_cover, \
             patch("src.wechat_draft.publish_daily_article", return_value={"status": "draft_created"}), \
             patch("src.main._generate_debug_reports"):
            daily_main._run_pipeline()

        self.assertEqual(generate_cover.call_args.kwargs["api_key"], "")

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
