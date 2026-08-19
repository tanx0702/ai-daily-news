import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from src.generator import (
    _news_to_markdown,
    render_daily_html,
    render_wechat_article,
    render_wechat_article_ai,
)


SAMPLE_NEWS = [
    {
        "title": "OpenAI announces a new model",
        "chinese_title": "OpenAI 发布新模型",
        "summary": "OpenAI 发布新模型，用于改进推理和多模态任务。",
        "source": "OpenAI",
        "url": "https://example.com/openai",
        "published_at": datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc),
    }
]


class GeneratorTests(unittest.TestCase):
    def test_renderers_label_attributed_opinion_and_keep_original_link(self):
        opinion = {
            **SAMPLE_NEWS[0],
            "content_type": "attributed_opinion",
            "content_label": "圈内观点",
            "opinion_author": "Andrej Karpathy",
            "url": "https://x.com/karpathy/status/42",
        }

        daily = render_daily_html([opinion], date_str="2026-07-11")
        wechat = render_wechat_article([opinion], date_str="2026-07-11")

        self.assertIn("圈内观点", daily)
        self.assertIn("圈内观点", wechat)
        self.assertIn("https://x.com/karpathy/status/42", wechat)

    def test_render_daily_html_contains_news_and_archive(self):
        html = render_daily_html(
            SAMPLE_NEWS,
            date_str="2026-07-11",
            archive_links=["https://example.com/archive/2026-07-10.html"],
            github_repo="tanx0702/ai-daily-news",
        )

        self.assertIn("今日AI要闻", html)
        self.assertIn("2026-07-11", html)
        self.assertIn("OpenAI 发布新模型", html)
        self.assertIn("2026-07-10", html)

    def test_render_daily_html_escapes_external_text_and_rejects_unsafe_url(self):
        html = render_daily_html(
            [{
                "title": "<script>alert(1)</script>",
                "chinese_title": "<img src=x onerror=alert(2)>",
                "summary": "<svg onload=alert(3)>",
                "source": "<b>Untrusted</b>",
                "url": "javascript:alert(4)",
                "published_at": None,
            }],
            date_str="2026-07-11",
        )

        self.assertIn("&lt;img src=x onerror=alert(2)&gt;", html)
        self.assertNotIn("<img src=x onerror=alert(2)>", html)
        self.assertIn("&lt;svg onload=alert(3)&gt;", html)
        self.assertNotIn('href="javascript:alert(4)"', html)

    def test_render_wechat_article_contains_cover_and_source_link(self):
        html = render_wechat_article(
            SAMPLE_NEWS,
            date_str="2026-07-11",
            pages_url="https://daily.example.com",
            cover_image_url="https://tankex.xyz/cover.jpg",
        )

        self.assertIn("https://tankex.xyz/cover.jpg", html)
        self.assertIn("今日AI要闻", html)
        self.assertIn("OpenAI 发布新模型", html)
        self.assertIn("阅读原文", html)
        self.assertIn("https://example.com/openai", html)
        self.assertNotIn("https://daily.example.com", html)
        self.assertNotIn("查看完整日报", html)

    def test_wechat_ai_markdown_keeps_item_sources_without_daily_page_link(self):
        markdown = _news_to_markdown(
            SAMPLE_NEWS,
            date_str="2026-07-11",
            pages_url="https://daily.example.com",
        )

        self.assertIn("https://example.com/openai", markdown)
        self.assertIn("阅读原文", markdown)
        self.assertNotIn("https://daily.example.com", markdown)
        self.assertNotIn("查看完整日报", markdown)

    def test_title_only_mode_omits_summary_but_keeps_title_and_source_link(self):
        title_only = [{
            **SAMPLE_NEWS[0],
            "brief_mode": "title_only",
            "summary": "这段摘要不应显示",
        }]

        daily = render_daily_html(title_only, date_str="2026-07-11")
        wechat = render_wechat_article(
            title_only,
            date_str="2026-07-11",
            pages_url="https://daily.example.com",
        )
        markdown = _news_to_markdown(
            title_only,
            date_str="2026-07-11",
            pages_url="https://daily.example.com",
        )

        for rendered in (daily, wechat, markdown):
            self.assertIn("OpenAI 发布新模型", rendered)
            self.assertIn("https://example.com/openai", rendered)
            self.assertNotIn("这段摘要不应显示", rendered)

    def test_render_wechat_article_rejects_unsafe_urls(self):
        html = render_wechat_article(
            [{
                "chinese_title": "Unsafe link",
                "summary": "Summary",
                "source": "Example",
                "url": "javascript:alert(1)",
                "article_image_url": "javascript:alert(2)",
                "image_type": "original",
            }],
            date_str="2026-07-11",
            pages_url="javascript:alert(3)",
            cover_image_url="javascript:alert(4)",
        )

        self.assertNotIn("javascript:", html)

    def test_render_wechat_article_ai_uses_text_llm_env(self):
        calls = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                calls["client"] = kwargs
                self.chat = SimpleNamespace(
                    completions=SimpleNamespace(create=self._create)
                )

            def _create(self, **kwargs):
                calls["completion"] = kwargs
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="<section>ok</section>")
                        )
                    ]
                )

        env = {
            "LLM_API_KEY": "text-key",
            "LLM_API_BASE": "https://text.example/v1",
            "LLM_MODEL": "text-model",
            "AGNES_API_KEY": "",
            "AGNES_API_BASE": "",
            "AGNES_MODEL": "",
            "OPENAI_API_KEY": "",
            "OPENAI_API_BASE": "",
            "OPENAI_MODEL": "",
        }

        with patch.dict(os.environ, env), patch("openai.OpenAI", FakeOpenAI):
            html = render_wechat_article_ai(
                SAMPLE_NEWS,
                date_str="2026-07-11",
                pages_url="https://tankex.xyz",
            )

        self.assertEqual(html, "<section>ok</section>")
        self.assertEqual(calls["client"]["api_key"], "text-key")
        self.assertEqual(calls["client"]["base_url"], "https://text.example/v1")
        self.assertEqual(calls["completion"]["model"], "text-model")


if __name__ == "__main__":
    unittest.main()
