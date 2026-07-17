import unittest

from src.media_assets import resolve_article_media


class MediaAssetTests(unittest.TestCase):
    def test_second_article_with_same_image_url_becomes_text_only(self):
        items, _ = resolve_article_media(
            [
                {"title": "First", "article_image_url": "https://example.test/shared.jpg"},
                {"title": "Second", "article_image_url": "https://example.test/shared.jpg"},
            ]
        )

        self.assertEqual(items[0]["image_type"], "original")
        self.assertEqual(items[1]["image_type"], "text_only")
        self.assertEqual(items[1]["image_reason"], "duplicate image URL")


if __name__ == "__main__":
    unittest.main()
