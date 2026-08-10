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
    @staticmethod
    def _v2_payload(*, execution_status="dry_run"):
        return {
            "schema_version": 2,
            "brief_items": [
                {
                    "event_key": "openai-model-5",
                    "chinese_title": "OpenAI 发布 Model 5",
                    "brief": "OpenAI 发布 Model 5，并提供新的文本 API。",
                    "canonical_source": {
                        "publisher_id": "openai",
                        "publisher_name": "OpenAI",
                        "channel": "rss",
                        "authority": "official",
                        "is_official": True,
                        "official_identity_source": "rss_source_config",
                        "url": "https://openai.com/news/model-5",
                        "published_at": "2026-08-07T08:00:00+00:00",
                    },
                    "related_sources": [],
                    "published_at": "2026-08-07T08:00:00+00:00",
                    "evidence_bindings": [
                        {
                            "claim": "OpenAI 发布 Model 5",
                            "source_quote": "OpenAI releases Model 5",
                            "source_url": "https://openai.com/news/model-5",
                        }
                    ],
                    "content_origin": "llm",
                    "validation_mode": "rules_only",
                }
            ],
            "draft_decision": {
                "action": "create",
                "selected_count": 5,
                "min_items": 5,
                "max_items": 15,
                "x_count": 0,
                "max_x_items": 5,
                "reasons": [],
                "excluded_counts": {},
                "source_counts": {"OpenAI": 1},
            },
            "draft_execution": {
                "status": execution_status,
                "reason": None if execution_status == "dry_run" else "wechat_draft_failed",
                "started_at": "2026-08-07T08:00:00+00:00",
                "completed_at": "2026-08-07T08:01:00+00:00",
                "media_id": None,
            },
            "diagnostics": {"rules_only_count": 1},
        }

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

    def test_load_news_projects_v2_brief_items_for_customer_messages(self):
        original_data_file = app.NEWS_DATA_FILE
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as file:
                json.dump(self._v2_payload(), file)
                app.NEWS_DATA_FILE = file.name

            news = app._load_news()

            self.assertEqual(news[0]["chinese_title"], "OpenAI 发布 Model 5")
            self.assertEqual(news[0]["summary"], "OpenAI 发布 Model 5，并提供新的文本 API。")
            self.assertEqual(news[0]["source"], "OpenAI")
            self.assertEqual(news[0]["url"], "https://openai.com/news/model-5")
        finally:
            app.NEWS_DATA_FILE = original_data_file
            if "file" in locals() and os.path.exists(file.name):
                os.unlink(file.name)

    def test_health_reports_exact_v2_decision_and_execution(self):
        original_token = app.WECHAT_TOKEN
        original_data_file = app.NEWS_DATA_FILE
        try:
            app.WECHAT_TOKEN = "configured-token"
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as file:
                json.dump(self._v2_payload(execution_status="failed"), file)
                app.NEWS_DATA_FILE = file.name

            response = app.app.test_client().get("/health")
            payload = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["status"], "degraded")
            self.assertTrue(payload["wechat_callback"]["configured"])
            self.assertEqual(payload["draft_decision"]["action"], "create")
            self.assertEqual(payload["draft_execution"]["status"], "failed")
            self.assertNotIn("publication", payload)
        finally:
            app.WECHAT_TOKEN = original_token
            app.NEWS_DATA_FILE = original_data_file
            if "file" in locals() and os.path.exists(file.name):
                os.unlink(file.name)

    def test_api_news_returns_v2_payload_without_legacy_quality_fields(self):
        original_data_file = app.NEWS_DATA_FILE
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as file:
                json.dump(self._v2_payload(), file)
                app.NEWS_DATA_FILE = file.name

            response = app.app.test_client().get("/api/news")
            payload = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["schema_version"], 2)
            self.assertEqual(payload["brief_items"][0]["chinese_title"], "OpenAI 发布 Model 5")
            self.assertNotIn("publication", payload)
            self.assertNotIn("quality_gate", payload)
        finally:
            app.NEWS_DATA_FILE = original_data_file
            if "file" in locals() and os.path.exists(file.name):
                os.unlink(file.name)

    def test_unversioned_v1_file_is_read_only_cold_start_input(self):
        original_data_file = app.NEWS_DATA_FILE
        try:
            legacy = {
                "news": [
                    {
                        "chinese_title": "旧版快讯",
                        "summary": "旧版摘要",
                        "source": "Legacy",
                        "url": "https://example.com/legacy",
                    }
                ],
                "publication": {"status": "draft_created", "ready": True},
            }
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as file:
                json.dump(legacy, file)
                app.NEWS_DATA_FILE = file.name

            news = app._load_news()
            health_payload = app.app.test_client().get("/health").get_json()

            self.assertEqual(news[0]["chinese_title"], "旧版快讯")
            self.assertIsNone(health_payload["draft_decision"])
            self.assertIsNone(health_payload["draft_execution"])
            self.assertNotIn("publication", health_payload)
        finally:
            app.NEWS_DATA_FILE = original_data_file
            if "file" in locals() and os.path.exists(file.name):
                os.unlink(file.name)


if __name__ == "__main__":
    unittest.main()
