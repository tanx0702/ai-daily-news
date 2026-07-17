import json
import os
import tempfile
import unittest
from unittest.mock import patch

from src import wechat, wechat_draft


class WeChatBoundaryTests(unittest.TestCase):
    def test_trusted_normalized_image_uploads_as_jpeg(self):
        class FakeResponse:
            def json(self):
                return {"url": "https://mmbiz.qpic.cn/article.jpg"}

        fd, image_path = tempfile.mkstemp(suffix=".jpg")
        try:
            with os.fdopen(fd, "wb") as image_file:
                image_file.write(b"\xff\xd8normalized-jpeg")
            with patch("src.wechat_draft.requests.post", return_value=FakeResponse()) as post:
                result = wechat_draft._upload_normalized_image("token", image_path)
        finally:
            os.unlink(image_path)

        self.assertEqual(result, "https://mmbiz.qpic.cn/article.jpg")
        uploaded = post.call_args.kwargs["files"]["media"]
        self.assertEqual(uploaded[0], "article.jpg")
        self.assertEqual(uploaded[1], b"\xff\xd8normalized-jpeg")
        self.assertEqual(uploaded[2], "image/jpeg")

    def test_legacy_wechat_module_reexports_draft_publisher(self):
        self.assertIs(wechat.publish_daily_article, wechat_draft.publish_daily_article)

    def test_draft_title_uses_editorial_column_style(self):
        robot_marker = "\U0001f916"
        original_prefix = os.environ.get("WECHAT_DRAFT_TITLE_PREFIX")
        try:
            os.environ["WECHAT_DRAFT_TITLE_PREFIX"] = "今日要闻"
            title = wechat_draft._build_draft_title("2026-07-12")
        finally:
            if original_prefix is None:
                os.environ.pop("WECHAT_DRAFT_TITLE_PREFIX", None)
            else:
                os.environ["WECHAT_DRAFT_TITLE_PREFIX"] = original_prefix

        self.assertEqual(title, "今日要闻｜7月12日")
        self.assertNotIn(robot_marker, title)
        self.assertNotIn("AI 日报", title)
        self.assertNotIn("AI Daily News", title)
        self.assertNotIn("2026-07-12", title)

    def test_draft_digest_is_compact_without_numbered_template(self):
        digest = wechat_draft._build_draft_digest([
            {"chinese_title": "哈工大教授创业打造人形操作世界模型"},
            {"chinese_title": "英军投巨资建AI实验室"},
            {"title": "OpenAI releases a browser update"},
        ])

        self.assertEqual(
            digest,
            "哈工大教授创业打造人形操作世界模型；英军投巨资建AI实验室；OpenAI releases a browser update",
        )
        self.assertNotIn("1.", digest)
        self.assertNotIn(" · ", digest)

    def test_create_draft_uses_human_author_label(self):
        robot_marker = "\U0001f916"

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"media_id": "draft-id"}

        original_author = os.environ.get("WECHAT_DRAFT_AUTHOR")
        try:
            os.environ["WECHAT_DRAFT_AUTHOR"] = "要闻编辑室"
            with patch("src.wechat_draft.requests.post", return_value=FakeResponse()) as post:
                media_id = wechat_draft._create_draft(
                    "token",
                    "今日要闻｜7月12日",
                    "<p>content</p>",
                    "thumb-id",
                    digest="哈工大教授创业打造人形操作世界模型",
                )
        finally:
            if original_author is None:
                os.environ.pop("WECHAT_DRAFT_AUTHOR", None)
            else:
                os.environ["WECHAT_DRAFT_AUTHOR"] = original_author

        self.assertEqual(media_id, "draft-id")
        payload = json.loads(post.call_args.kwargs["data"].decode("utf-8"))
        article = payload["articles"][0]
        self.assertEqual(article["author"], "要闻编辑室")
        self.assertNotIn("AI Daily News", article["author"])
        self.assertNotIn(robot_marker, article["title"])


if __name__ == "__main__":
    unittest.main()
