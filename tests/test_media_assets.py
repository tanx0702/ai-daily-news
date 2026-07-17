import io
import unittest
from unittest.mock import patch

from PIL import Image

from src.media_assets import resolve_article_media, validate_media_candidate


class MediaAssetTests(unittest.TestCase):
    def test_validator_reencodes_supported_image_to_jpeg(self):
        webp = io.BytesIO()
        Image.new("RGB", (640, 360), (32, 128, 192)).save(webp, "WEBP")

        class Response:
            content = webp.getvalue()

            def raise_for_status(self):
                return None

        with patch("src.media_assets.requests.get", return_value=Response()):
            result = validate_media_candidate("https://example.test/image.webp", 1)

        self.assertTrue(result["valid"])
        self.assertTrue(result["jpeg_bytes"].startswith(b"\xff\xd8"))
        self.assertEqual(result["width"], 640)
        self.assertEqual(result["height"], 360)
        self.assertTrue(result["sha256"])
        self.assertTrue(result["phash"])

    def test_second_article_with_same_media_hash_becomes_text_only(self):
        validated = {
            "valid": True,
            "jpeg_bytes": b"\xff\xd8fake-jpeg",
            "sha256": "same-hash",
            "phash": "0" * 16,
            "width": 640,
            "height": 360,
            "format": "JPEG",
            "reason": "validated",
        }
        with patch("src.media_assets.validate_media_candidate", return_value=validated):
            items, _ = resolve_article_media(
                [
                    {"title": "First", "article_image_url": "https://example.test/first.jpg"},
                    {"title": "Second", "article_image_url": "https://example.test/second.jpg"},
                ],
            )

        self.assertEqual(items[0]["media_state"], "trusted")
        self.assertEqual(items[1]["image_type"], "text_only")
        self.assertEqual(items[1]["image_reason"], "duplicate media hash")

    def test_validator_rejects_placeholder_url_before_download(self):
        result = validate_media_candidate("https://example.test/logo-placeholder.png", 1)

        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "bad_url_hint")


if __name__ == "__main__":
    unittest.main()
