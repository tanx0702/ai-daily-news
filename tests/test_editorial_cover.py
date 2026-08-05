import os
import unittest
from unittest.mock import patch

from src.editorial_cover import render_editorial_cover, select_editorial_palette


class EditorialCoverTests(unittest.TestCase):
    def test_eight_consecutive_dates_rotate_through_each_palette_once(self):
        palette_ids = [
            select_editorial_palette(f"2026-01-{day:02d}")["palette_id"]
            for day in range(1, 9)
        ]

        self.assertEqual(
            palette_ids,
            [
                "terracotta",
                "charcoal",
                "sage",
                "paper",
                "ink_blue",
                "deep_teal",
                "burgundy",
                "mustard",
            ],
        )

    def test_renderer_uses_story_type_for_diagram_without_changing_palette(self):
        with patch.dict(os.environ, {}, clear=True):
            product_image, product_metadata = render_editorial_cover(
                title="OpenAI 发布新的产品",
                date_str="2026-01-01",
                source_label="OpenAI",
                story_type="product",
            )
            model_image, model_metadata = render_editorial_cover(
                title="OpenAI 发布新的模型",
                date_str="2026-01-01",
                source_label="OpenAI",
                story_type="model",
            )

        self.assertEqual(product_image.size, (900, 500))
        self.assertEqual(product_metadata["palette_id"], "terracotta")
        self.assertEqual(model_metadata["palette_id"], "terracotta")
        self.assertEqual(product_metadata["diagram_type"], "growth")
        self.assertEqual(model_metadata["diagram_type"], "funnel")
        self.assertNotEqual(product_image.tobytes(), model_image.tobytes())


if __name__ == "__main__":
    unittest.main()
