import json

import pytest

from scripts.x_web_probe import (
    build_report,
    extract_tweets,
    is_allowed_response_url,
    probe_exit_code,
    validate_target_url,
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
