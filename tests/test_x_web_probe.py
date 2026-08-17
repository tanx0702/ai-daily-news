import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from scripts.x_web_probe import (
    _read_dom_cards,
    build_report,
    extract_dom_tweets,
    extract_tweets,
    is_allowed_response_url,
    probe_exit_code,
    run_probe,
    validate_target_url,
    write_report,
)


def test_validate_target_url_only_accepts_x_public_hosts():
    assert validate_target_url("https://x.com/OpenAI") == "https://x.com/OpenAI"
    assert validate_target_url("https://www.twitter.com/OpenAI") == "https://www.twitter.com/OpenAI"

    with pytest.raises(ValueError, match="仅支持公开 X 页面"):
        validate_target_url("https://example.com/redirect")

    with pytest.raises(ValueError, match="仅支持公开 X 页面"):
        validate_target_url("https://x.com.example.com/OpenAI")

    with pytest.raises(ValueError, match="仅支持公开 X 页面"):
        validate_target_url("https://api.x.com/i/api/graphql/a/UserTweets")


def test_allowed_response_url_is_limited_to_x_tweet_operations():
    assert is_allowed_response_url("https://x.com/i/api/graphql/a/TweetResultByRestId")
    assert is_allowed_response_url("https://x.com/i/api/graphql/a/UserTweets")
    assert is_allowed_response_url("https://api.x.com/i/api/graphql/a/UserTweets")
    assert not is_allowed_response_url("https://x.com/i/api/graphql/a/Viewer")
    assert not is_allowed_response_url("https://example.com/TweetResultByRestId")
    assert not is_allowed_response_url("https://api.x.com.evil.test/UserTweets")


def test_extract_tweets_reads_nested_payload_and_deduplicates_id():
    payload = {
        "data": {
            "tweetResult": {
                "result": {
                    "rest_id": "42",
                    "legacy": {
                        "full_text": "模型已发布",
                        "created_at": "Sun Aug 03 06:00:00 +0000 2026",
                        "favorite_count": 7,
                        "retweet_count": 3,
                        "reply_count": 2,
                        "quote_count": 1,
                    },
                    "core": {
                        "user_results": {
                            "result": {"legacy": {"screen_name": "OpenAI"}}
                        }
                    },
                }
            }
        },
        "duplicate": {"rest_id": "42", "legacy": {"full_text": "模型已发布"}},
    }

    assert extract_tweets(payload) == [
        {
            "tweet_id": "42",
            "text": "模型已发布",
            "author": "OpenAI",
            "created_at": "Sun Aug 03 06:00:00 +0000 2026",
            "thread_id": "42",
            "reply_to_id": "",
            "quoted_id": "",
            "like_count": 7,
            "repost_count": 3,
            "reply_count": 2,
            "quote_count": 1,
        }
    ]


def test_extract_tweets_preserves_graphql_thread_relationship_ids():
    payload = {
        "data": {
            "tweetResult": {
                "result": {
                    "rest_id": "42",
                    "legacy": {
                        "full_text": "回复并引用",
                        "created_at": "Sun Aug 03 06:00:00 +0000 2026",
                        "conversation_id_str": "40",
                        "in_reply_to_status_id_str": "41",
                        "quoted_status_id_str": "39",
                    },
                }
            }
        }
    }

    assert extract_tweets(payload)[0]["thread_id"] == "40"
    assert extract_tweets(payload)[0]["reply_to_id"] == "41"
    assert extract_tweets(payload)[0]["quoted_id"] == "39"


def test_report_contains_only_public_fields_and_empty_report_fails():
    report = build_report(
        "https://x.com/OpenAI",
        [{"operation": "TweetResultByRestId", "tweets": []}],
        ["response_json_error"],
    )

    serialized = json.dumps(report, ensure_ascii=False).lower()
    assert set(report) == {
        "schema_version",
        "target_url",
        "captured_operations",
        "extraction_method",
        "tweet_count",
        "tweets",
        "errors",
    }
    assert "authorization" not in serialized
    assert "cookie" not in serialized
    assert probe_exit_code(report) == 1


def test_extract_dom_tweets_uses_status_url_and_deduplicates_visible_cards():
    cards = [
        {
            "status_url": "https://x.com/OpenAI/status/42",
            "text": "模型已发布",
            "author": "OpenAI",
            "created_at": "2026-08-03T06:00:00.000Z",
        },
        {
            "status_url": "https://x.com/OpenAI/status/42",
            "text": "重复卡片",
            "author": "OpenAI",
            "created_at": "2026-08-03T06:00:00.000Z",
        },
        {
            "status_url": "https://x.com/OpenAI",
            "text": "没有状态链接的内容",
            "author": "OpenAI",
            "created_at": "",
        },
    ]

    assert extract_dom_tweets(cards) == [
        {
            "tweet_id": "42",
            "text": "模型已发布",
            "author": "OpenAI",
            "created_at": "2026-08-03T06:00:00.000Z",
            "thread_id": "42",
            "reply_to_id": "",
            "quoted_id": "",
            "like_count": 0,
            "repost_count": 0,
            "reply_count": 0,
            "quote_count": 0,
        }
    ]


def test_read_dom_cards_supports_current_schema_org_tweet_cards():
    # 当前 X 公开页面用 Schema.org 属性标识推文卡片，不再提供旧 data-testid。
    cards = [{"status_url": "https://x.com/OpenAI/status/42", "text": "模型已发布"}]

    class FakeLocator:
        @staticmethod
        def evaluate_all(_expression):
            return cards

    class FakePage:
        @staticmethod
        def locator(selector):
            assert selector == (
                "article[data-testid='tweet'], "
                "article[data-tweet-id][itemtype='https://schema.org/SocialMediaPosting']"
            )
            return FakeLocator()

    assert _read_dom_cards(FakePage()) == cards


def test_report_marks_dom_fallback_when_xhr_has_no_tweets():
    report = build_report(
        "https://x.com/OpenAI",
        [],
        [],
        dom_tweets=[
            {
                "tweet_id": "42",
                "text": "模型已发布",
                "author": "OpenAI",
                "created_at": "2026-08-03T06:00:00.000Z",
            }
        ],
    )

    assert report["extraction_method"] == "dom_fallback"
    assert report["tweet_count"] == 1
    assert report["tweets"][0]["tweet_id"] == "42"


def test_write_report_creates_only_probe_report_json(tmp_path: Path):
    path = write_report({"schema_version": "x-web-probe-v1", "tweet_count": 0}, tmp_path)

    assert path == tmp_path / "probe-report.json"
    assert json.loads(path.read_text(encoding="utf-8"))["tweet_count"] == 0
    assert [item.name for item in tmp_path.iterdir()] == ["probe-report.json"]


def test_run_probe_writes_failure_screenshot_before_closing_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class FakeResponse:
        url = "https://x.com/i/api/graphql/a/UserTweets"

        @staticmethod
        def json():
            return {"data": {}}

    class FakePage:
        def __init__(self, browser):
            self.browser = browser
            self.callback = None

        def on(self, event, callback):
            assert event == "response"
            self.callback = callback

        def goto(self, *_args, **_kwargs):
            self.callback(FakeResponse())

        @staticmethod
        def wait_for_timeout(_timeout):
            return None

        def screenshot(self, path, full_page):
            assert full_page is True
            assert self.browser.closed is False
            Path(path).write_bytes(b"failure screenshot")

    class FakeBrowser:
        def __init__(self):
            self.closed = False
            self.page = FakePage(self)

        def new_page(self):
            return self.page

        def close(self):
            self.closed = True

    class FakePlaywright:
        def __init__(self):
            self.browser = FakeBrowser()
            self.chromium = self

        def __enter__(self):
            return self

        @staticmethod
        def __exit__(_exc_type, _exc_value, _traceback):
            return None

        def launch(self, headless):
            assert headless is True
            return self.browser

    playwright = FakePlaywright()
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: playwright
    package = ModuleType("playwright")
    package.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    assert run_probe("https://x.com/OpenAI", tmp_path) == 1
    assert (tmp_path / "failure.png").read_bytes() == b"failure screenshot"
    assert playwright.browser.closed is True


def test_run_probe_uses_dom_fallback_after_empty_xhr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class FakeResponse:
        url = "https://api.x.com/i/api/graphql/a/UserTweets"

        @staticmethod
        def json():
            return {"data": {}}

    class FakeLocator:
        @staticmethod
        def evaluate_all(_expression):
            return [
                {
                    "status_url": "https://x.com/OpenAI/status/42",
                    "text": "模型已发布",
                    "author": "OpenAI",
                    "created_at": "2026-08-03T06:00:00.000Z",
                }
            ]

    class FakePage:
        def __init__(self):
            self.callback = None

        def on(self, event, callback):
            assert event == "response"
            self.callback = callback

        def goto(self, *_args, **_kwargs):
            self.callback(FakeResponse())

        @staticmethod
        def wait_for_timeout(_timeout):
            return None

        @staticmethod
        def locator(selector):
            assert selector == (
                "article[data-testid='tweet'], "
                "article[data-tweet-id][itemtype='https://schema.org/SocialMediaPosting']"
            )
            return FakeLocator()

        @staticmethod
        def screenshot(*_args, **_kwargs):
            raise AssertionError("DOM 回退成功时不应生成失败截图")

    class FakeBrowser:
        def __init__(self):
            self.closed = False
            self.page = FakePage()

        def new_page(self):
            return self.page

        def close(self):
            self.closed = True

    class FakePlaywright:
        def __init__(self):
            self.browser = FakeBrowser()
            self.chromium = self

        def __enter__(self):
            return self

        @staticmethod
        def __exit__(_exc_type, _exc_value, _traceback):
            return None

        def launch(self, headless):
            assert headless is True
            return self.browser

    playwright = FakePlaywright()
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: playwright
    package = ModuleType("playwright")
    package.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    assert run_probe("https://x.com/OpenAI", tmp_path) == 0
    report = json.loads((tmp_path / "probe-report.json").read_text(encoding="utf-8"))
    assert report["extraction_method"] == "dom_fallback"
    assert report["tweet_count"] == 1
    assert not (tmp_path / "failure.png").exists()


def test_workflow_is_manual_and_does_not_reference_vps_or_secrets():
    workflow = Path(".github/workflows/x-web-probe.yml").read_text(encoding="utf-8")
    normalized = workflow.lower()

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "secrets." not in normalized
    assert "x_bearer_token" not in normalized
    assert "ssh " not in normalized
    assert "scp " not in normalized
    assert "vps" not in normalized
    assert "actions/upload-artifact@v4" in workflow
    assert "if: always()" in workflow
