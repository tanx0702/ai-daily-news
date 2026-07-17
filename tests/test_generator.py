import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from src.generator import render_daily_html, render_wechat_article, render_wechat_article_ai


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

    def test_render_wechat_article_contains_cover_and_source_link(self):
        html = render_wechat_article(
            SAMPLE_NEWS,
            date_str="2026-07-11",
            pages_url="https://tankex.xyz",
            cover_image_url="https://tankex.xyz/cover.jpg",
        )

        self.assertIn("https://tankex.xyz/cover.jpg", html)
        self.assertIn("今日AI要闻", html)
        self.assertIn("OpenAI 发布新模型", html)
        self.assertIn("阅读原文", html)
        self.assertIn("https://example.com/openai", html)

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
