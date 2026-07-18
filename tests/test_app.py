import hashlib
import json
import os
import tempfile
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

    def test_verify_signature_rejects_missing_token_by_default(self):
        original_token = app.WECHAT_TOKEN
        original_allow = os.environ.pop("ALLOW_INSECURE_WECHAT_TOKEN", None)
        try:
            app.WECHAT_TOKEN = ""
            self.assertFalse(app._verify_signature("any", "1", "2"))
        finally:
            app.WECHAT_TOKEN = original_token
            if original_allow is not None:
                os.environ["ALLOW_INSECURE_WECHAT_TOKEN"] = original_allow

    def test_verify_signature_can_allow_explicit_insecure_local_bypass(self):
        original_token = app.WECHAT_TOKEN
        original_allow = os.environ.get("ALLOW_INSECURE_WECHAT_TOKEN")
        try:
            app.WECHAT_TOKEN = ""
            os.environ["ALLOW_INSECURE_WECHAT_TOKEN"] = "1"
            self.assertTrue(app._verify_signature("any", "1", "2"))
        finally:
            app.WECHAT_TOKEN = original_token
            if original_allow is None:
                os.environ.pop("ALLOW_INSECURE_WECHAT_TOKEN", None)
            else:
                os.environ["ALLOW_INSECURE_WECHAT_TOKEN"] = original_allow

    def test_post_rejects_invalid_signature(self):
        original_token = app.WECHAT_TOKEN
        try:
            app.WECHAT_TOKEN = "secret-token"
            client = app.app.test_client()
            resp = client.post(
                "/wechat?signature=bad&timestamp=1700000000&nonce=abc",
                data=b"<xml></xml>",
            )
            self.assertEqual(resp.status_code, 403)
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

    def test_health_reports_callback_configuration_and_latest_publication(self):
        original_token = app.WECHAT_TOKEN
        original_data_file = app.NEWS_DATA_FILE
        try:
            app.WECHAT_TOKEN = ""
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as file:
                json.dump({"publication": {"status": "failed", "reason": "wechat_draft_failed"}}, file)
                app.NEWS_DATA_FILE = file.name

            response = app.app.test_client().get("/health")
            payload = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["status"], "degraded")
            self.assertFalse(payload["wechat_callback"]["configured"])
            self.assertEqual(payload["publication"]["status"], "failed")
        finally:
            app.WECHAT_TOKEN = original_token
            app.NEWS_DATA_FILE = original_data_file
            if "file" in locals() and os.path.exists(file.name):
                os.unlink(file.name)


if __name__ == "__main__":
    unittest.main()
