import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from scripts.x_web_probe import (
    build_report,
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


def test_allowed_response_url_is_limited_to_x_tweet_operations():
    assert is_allowed_response_url("https://x.com/i/api/graphql/a/TweetResultByRestId")
    assert is_allowed_response_url("https://x.com/i/api/graphql/a/UserTweets")
    assert not is_allowed_response_url("https://x.com/i/api/graphql/a/Viewer")
    assert not is_allowed_response_url("https://example.com/TweetResultByRestId")


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
            "like_count": 7,
            "repost_count": 3,
            "reply_count": 2,
            "quote_count": 1,
        }
    ]


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
        "tweet_count",
        "tweets",
        "errors",
    }
    assert "authorization" not in serialized
    assert "cookie" not in serialized
    assert probe_exit_code(report) == 1


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
