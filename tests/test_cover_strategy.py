import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from src import cover


class CoverStrategyTests(unittest.TestCase):
    def test_editorial_mode_renders_local_template_and_skips_legacy_sources(self):
        item = {
            "title": "OpenAI launches a new product",
            "chinese_title": "OpenAI 发布新产品",
            "source": "OpenAI",
            "cover_image_url": "https://example.com/cover.jpg",
            "media_state": "trusted",
        }
        subject = {
            "mode": "trusted",
            "story_type": "product",
            "item": item,
            "cover_title": "OpenAI 发布新产品",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "cover.jpg")
            with patch.dict(os.environ, {"COVER_RENDER_MODE": "editorial"}, clear=False):
                with patch("src.cover._generate_cover_from_article_image") as article_image:
                    with patch("src.cover._generate_ai_cover_image") as ai_image:
                        result = cover.generate_cover_from_news(
                            [item],
                            "2026-01-01",
                            output_path=output_path,
                            api_key="image-key",
                            cover_subject=subject,
                        )

            with Image.open(output_path) as saved:
                self.assertEqual(saved.size, (900, 500))

        self.assertEqual(result, output_path)
        self.assertEqual(subject["cover_source"], "editorial_template")
        self.assertEqual(subject["render_mode"], "editorial")
        self.assertEqual(subject["palette_id"], "terracotta")
        self.assertEqual(subject["palette_index"], 0)
        self.assertEqual(subject["diagram_type"], "growth")
        article_image.assert_not_called()
        ai_image.assert_not_called()

    def test_trusted_article_image_is_used_before_ai_generation(self):
        subject = {
            "mode": "trusted",
            "story_type": "product",
            "item": {
                "title": "OpenAI announces a model",
                "chinese_title": "OpenAI 发布新模型",
                "cover_image_url": "https://example.com/cover.jpg",
                "media_state": "trusted",
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

    def test_image_model_env_is_passed_to_ai_generation(self):
        subject = {
            "mode": "generic",
            "story_type": "general",
            "item": None,
            "cover_title": "Today",
        }
        ai_image = Image.new("RGB", (900, 500), (96, 128, 144))

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "cover.jpg")
            with patch.dict(
                os.environ,
                {
                    "ENABLE_AI_COVER_GENERATION": "1",
                    "AI_COVER_MAX_RETRIES": "1",
                    "IMAGE_MODEL": "image-model",
                },
            ):
                with patch("src.cover._generate_ai_cover_image", return_value=ai_image) as generator:
                    cover.generate_cover_from_news(
                        [],
                        "2026-07-12",
                        output_path=output_path,
                        api_key="image-key",
                        base_url="https://image.example",
                        cover_subject=subject,
                    )

        generator.assert_called_once()
        self.assertEqual(generator.call_args.args[0], "https://image.example")
        self.assertEqual(generator.call_args.args[1], "image-key")
        self.assertEqual(generator.call_args.kwargs["model"], "image-model")

    def test_image_endpoint_base_url_is_normalized_before_generation(self):
        subject = {
            "mode": "generic",
            "story_type": "general",
            "item": None,
            "cover_title": "Today",
        }
        ai_image = Image.new("RGB", (900, 500), (96, 128, 144))

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "cover.jpg")
            with patch.dict(
                os.environ,
                {
                    "ENABLE_AI_COVER_GENERATION": "1",
                    "AI_COVER_MAX_RETRIES": "1",
                },
            ):
                with patch("src.cover._generate_ai_cover_image", return_value=ai_image) as generator:
                    cover.generate_cover_from_news(
                        [],
                        "2026-07-12",
                        output_path=output_path,
                        api_key="image-key",
                        base_url="https://apihub.agnes-ai.com/v1/images/generations",
                        cover_subject=subject,
                    )

        generator.assert_called_once()
        self.assertEqual(generator.call_args.args[0], "https://apihub.agnes-ai.com")

    def test_ai_generation_request_uses_agnes_documented_image_params(self):
        class FakeResponse:
            status_code = 200

            def json(self):
                return {"data": [{"url": "https://images.example/cover.png"}]}

        class FakeImageResponse:
            content = b"image-bytes"

            def raise_for_status(self):
                return None

        with patch("src.cover.requests.post", return_value=FakeResponse()) as post:
            with patch("src.cover.requests.get", return_value=FakeImageResponse()):
                with patch("src.cover.Image.open", return_value=Image.new("RGB", (900, 500))):
                    result = cover._generate_ai_cover_image(
                        "https://apihub.agnes-ai.com",
                        "image-key",
                        "prompt",
                        max_retries=1,
                        model="agnes-image-2.1-flash",
                    )

        self.assertIsNotNone(result)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://apihub.agnes-ai.com/v1/images/generations")
        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "model": "agnes-image-2.1-flash",
                "prompt": "prompt",
                "size": "1K",
                "ratio": "16:9",
                "extra_body": {"response_format": "url"},
            },
        )

    def test_ai_generated_cover_is_saved_to_wechat_cover_canvas(self):
        subject = {
            "mode": "generic",
            "story_type": "general",
            "item": None,
            "cover_title": "Today",
        }
        ai_image = Image.new("RGB", (1312, 736), (96, 128, 144))

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "cover.jpg")
            with patch.dict(
                os.environ,
                {
                    "ENABLE_AI_COVER_GENERATION": "1",
                    "AI_COVER_MAX_RETRIES": "1",
                    "FORCE_LOCAL_COVER_ON_BAD_IMAGE": "0",
                },
            ):
                with patch("src.cover._generate_ai_cover_image", return_value=ai_image):
                    cover.generate_cover_from_news(
                        [],
                        "2026-07-12",
                        output_path=output_path,
                        api_key="api-key",
                        cover_subject=subject,
                    )

            with Image.open(output_path) as saved:
                self.assertEqual(saved.size, (900, 500))

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
