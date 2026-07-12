import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from src import cover


class CoverStrategyTests(unittest.TestCase):
    def test_trusted_article_image_is_used_before_ai_generation(self):
        subject = {
            "mode": "trusted",
            "story_type": "product",
            "item": {
                "title": "OpenAI announces a model",
                "chinese_title": "OpenAI 发布新模型",
                "cover_image_url": "https://example.com/cover.jpg",
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "cover.jpg")
            with patch("src.cover._generate_cover_from_article_image", return_value=output_path) as article_image:
                with patch("src.cover._generate_ai_cover_image") as ai_image:
                    result = cover.generate_cover_from_news(
                        [subject["item"]],
                        "2026-07-12",
                        output_path=output_path,
                        api_key="api-key",
                        cover_subject=subject,
                    )

        self.assertEqual(result, output_path)
        self.assertEqual(subject["cover_source"], "first_article_image")
        article_image.assert_called_once()
        ai_image.assert_not_called()

    def test_generic_subject_uses_ai_even_when_safe_cover_is_enabled(self):
        subject = {
            "mode": "generic",
            "story_type": "general",
            "item": None,
            "cover_title": "今日AI要闻",
        }
        ai_image = Image.new("RGB", (900, 500), (96, 128, 144))

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "cover.jpg")
            with patch.dict(
                os.environ,
                {
                    "ENABLE_AI_COVER_GENERATION": "1",
                    "ENABLE_SAFE_COVER": "1",
                    "FORCE_LOCAL_COVER_ON_BAD_IMAGE": "1",
                    "AI_COVER_MAX_RETRIES": "1",
                },
            ):
                with patch("src.cover._generate_ai_cover_image", return_value=ai_image) as generator:
                    result = cover.generate_cover_from_news(
                        [],
                        "2026-07-12",
                        output_path=output_path,
                        api_key="api-key",
                        cover_subject=subject,
                    )
            file_exists = os.path.isfile(output_path)

        self.assertEqual(result, output_path)
        self.assertTrue(file_exists)
        self.assertEqual(subject["cover_source"], "ai_generated")
        generator.assert_called_once()

    def test_ai_failure_uses_minimal_text_free_background_not_title_card(self):
        subject = {
            "mode": "generic",
            "story_type": "research",
            "item": None,
            "cover_title": "今日AI要闻",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "cover.jpg")
            with patch.dict(
                os.environ,
                {
                    "ENABLE_AI_COVER_GENERATION": "1",
                    "AI_COVER_MAX_RETRIES": "1",
                },
            ):
                with patch("src.cover._generate_ai_cover_image", return_value=None):
                    result = cover.generate_cover_from_news(
                        [],
                        "2026-07-12",
                        output_path=output_path,
                        api_key="api-key",
                        cover_subject=subject,
                    )
            file_exists = os.path.isfile(output_path)

        self.assertEqual(result, output_path)
        self.assertTrue(file_exists)
        self.assertEqual(subject["cover_source"], "minimal_text_free_background")


if __name__ == "__main__":
    unittest.main()
