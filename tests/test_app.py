import hashlib
import unittest
import xml.etree.ElementTree as ET

try:
    import app
except ModuleNotFoundError as exc:
    if exc.name == "flask":
        raise unittest.SkipTest("Flask is not installed in this Python environment")
    raise


class WeChatAppTests(unittest.TestCase):
    def test_build_xml_reply_contains_recipient_and_content(self):
        xml_text = app._build_xml_reply("openid-1", "今日摘要")

        root = ET.fromstring(xml_text)
        self.assertEqual(root.findtext("ToUserName"), "openid-1")
        self.assertEqual(root.findtext("MsgType"), "text")
        self.assertEqual(root.findtext("Content"), "今日摘要")

    def test_verify_signature_uses_configured_token(self):
        original_token = app.WECHAT_TOKEN
        try:
            app.WECHAT_TOKEN = "secret-token"
            timestamp = "1700000000"
            nonce = "abc"
            raw = "".join(sorted([app.WECHAT_TOKEN, timestamp, nonce]))
            signature = hashlib.sha1(raw.encode()).hexdigest()

            self.assertTrue(app._verify_signature(signature, timestamp, nonce))
            self.assertFalse(app._verify_signature("bad-signature", timestamp, nonce))
        finally:
            app.WECHAT_TOKEN = original_token

    def test_format_summary_uses_current_branding(self):
        summary = app._format_summary([
            {
                "chinese_title": "OpenAI 发布新模型",
                "source": "OpenAI",
                "summary": "模型能力更新。",
                "url": "https://example.com/news",
            }
        ])

        self.assertIn("今日AI要闻", summary)
        self.assertIn("共 1 条精选", summary)
        self.assertIn("OpenAI 发布新模型", summary)
        self.assertIn("完整内容:", summary)


if __name__ == "__main__":
    unittest.main()
