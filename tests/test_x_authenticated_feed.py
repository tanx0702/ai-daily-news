import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from scripts.x_authenticated_feed import collect_authenticated_feed, write_authenticated_feed


NOW = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)
OPENAI = {
    "name": "OpenAI",
    "handle": "OpenAI",
    "tier": "primary",
    "official": True,
}
ANTHROPIC = {
    "name": "Anthropic",
    "handle": "AnthropicAI",
    "tier": "primary",
    "official": True,
}


def _tweet(tweet_id: int = 42):
    return SimpleNamespace(
        id=tweet_id,
        rawContent="发布新的 AI 模型",
        date=datetime(2026, 8, 18, 5, tweet_id, tzinfo=timezone.utc),
        user=SimpleNamespace(username="OpenAI", displayname="OpenAI"),
        conversationId=40,
        inReplyToTweetId=None,
        quotedTweet=None,
    )


class FakeClient:
    async def user_by_login(self, handle):
        return SimpleNamespace(id=1001, username=handle)

    async def user_tweets(self, _user_id, limit):
        return [_tweet(index) for index in range(21, 0, -1)]


class FailingClient(FakeClient):
    async def user_by_login(self, handle):
        if handle == "AnthropicAI":
            raise TimeoutError("simulated timeout")
        return await super().user_by_login(handle)


def test_collect_authenticated_feed_maps_and_truncates_tweet_objects():
    feed = asyncio.run(
        collect_authenticated_feed(
            FakeClient(),
            [OPENAI],
            per_source_limit=3,
            timeout_seconds=45,
            now=NOW,
        )
    )

    assert feed["schema_version"] == "x-feed-v1"
    assert feed["tweet_count"] == 3
    assert feed["tweets"][0]["url"] == "https://x.com/OpenAI/status/21"
    assert feed["tweets"][0]["created_at"] == "2026-08-18T05:21:00Z"
    assert feed["tweets"][0]["thread_id"] == "40"


def test_source_timeout_is_recorded_without_stopping_other_sources():
    feed = asyncio.run(
        collect_authenticated_feed(
            FailingClient(),
            [OPENAI, ANTHROPIC],
            per_source_limit=1,
            timeout_seconds=45,
            now=NOW,
        )
    )

    assert feed["tweet_count"] == 1
    assert feed["failures"] == [{"handle": "AnthropicAI", "reason": "timeout"}]


def test_atomic_writer_replaces_a_complete_snapshot_without_temp_file(tmp_path: Path):
    target = tmp_path / "feed" / "x-feed.json"
    feed = {
        "schema_version": "x-feed-v1",
        "generated_at": "2026-08-18T06:00:00Z",
        "source_count": 0,
        "successful_source_count": 0,
        "failed_source_count": 0,
        "failures": [],
        "tweet_count": 0,
        "tweets": [],
    }

    write_authenticated_feed(feed, target)

    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == "x-feed-v1"
    assert not list(target.parent.glob(".x-feed.json.*.tmp"))
