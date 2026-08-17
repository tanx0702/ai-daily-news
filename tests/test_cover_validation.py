import os
import tempfile
import unittest
from unittest.mock import patch

from src import cover


class CoverValidationTests(unittest.TestCase):
    def test_rejected_media_cannot_become_cover_subject(self):
        subject = cover.select_cover_subject([
            {
                "title": "OpenAI announces a model update",
                "chinese_title": "OpenAI 发布模型更新",
                "summary": "The company described a model update.",
                "media_state": "rejected",
            }
        ])

        self.assertEqual(subject["mode"], "generic")
        self.assertIn("untrusted media", subject["excluded"][0]["reason"])

    def test_untrusted_cover_image_uses_local_fallback(self):
        subject = {
            "mode": "trusted",
            "story_type": "product",
            "item": {
                "title": "OpenAI announces a model",
                "cover_image_url": "https://example.com/cover.jpg",
                "media_state": "rejected",
            },
            "cover_title": "OpenAI 新进展",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "cover.jpg")
            with patch.dict(os.environ, {"ENABLE_AI_COVER_GENERATION": "0"}):
                with patch("src.cover._generate_cover_from_article_image") as article_image:
                    with patch("src.cover._fallback_cover", return_value=output_path) as fallback:
                        result = cover.generate_cover_from_news(
                            [subject["item"]],
                            "2026-07-17",
                            output_path=output_path,
                            cover_subject=subject,
                        )

        self.assertEqual(result, output_path)
        article_image.assert_not_called()
        fallback.assert_called_once()

    def test_non_retryable_image_api_error_stops_after_one_attempt(self):
        class Response:
            status_code = 400
            text = "unsupported model"

            def json(self):
                return {"error": {"code": "unsupported_model", "message": "unsupported model"}}

        with patch("src.cover.requests.post", return_value=Response()) as post:
            result = cover._generate_ai_cover_image(
                "https://images.example",
                "key",
                "prompt",
                max_retries=3,
            )

        self.assertIsNone(result)
        self.assertEqual(post.call_count, 1)

    def test_payment_required_image_api_error_stops_after_one_attempt(self):
        class Response:
            status_code = 402
            text = "subscription request not allowed"

            def json(self):
                return {
                    "error": {
                        "code": "subscription_not_found",
                        "message": "subscription request not allowed",
                    }
                }

        with patch("src.cover.requests.post", return_value=Response()) as post:
            result = cover._generate_ai_cover_image(
                "https://images.example",
                "key",
                "prompt",
                max_retries=3,
            )

        self.assertIsNone(result)
        self.assertEqual(post.call_count, 1)

    def test_image_generation_uses_configured_request_timeout(self):
        class Response:
            status_code = 400
            text = "bad request"

            def json(self):
                return {"error": {"code": "invalid_model", "message": "bad request"}}

        with patch.dict(os.environ, {"IMAGE_GENERATION_TIMEOUT": "12"}):
            with patch("src.cover.requests.post", return_value=Response()) as post:
                cover._generate_ai_cover_image(
                    "https://images.example",
                    "key",
                    "prompt",
                    max_retries=1,
                )

        self.assertEqual(post.call_args.kwargs["timeout"], 12)


if __name__ == "__main__":
    unittest.main()
