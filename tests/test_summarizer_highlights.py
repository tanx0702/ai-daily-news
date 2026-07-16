import unittest

from src.summarizer import generate_highlights


class SummarizerHighlightsTests(unittest.TestCase):
    def test_generate_highlights_skips_quality_excluded_items(self):
        news = [
            {
                "chinese_title": "Excluded story",
                "summary": "Should not be highlighted.",
                "_highlight_excluded": "quality gate",
            },
            {
                "chinese_title": "Eligible one",
                "summary": "First eligible story.",
            },
            {
                "chinese_title": "Eligible two",
                "summary": "Second eligible story.",
            },
        ]

        highlights = generate_highlights(news, api_key="")

        self.assertEqual(highlights, ["Eligible one", "Eligible two"])


if __name__ == "__main__":
    unittest.main()
