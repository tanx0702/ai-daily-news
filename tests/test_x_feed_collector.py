from datetime import datetime, timedelta, timezone

from src import collector
from src.collectors.x_feed import XFeedCollector


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _feed(generated_at: datetime) -> dict:
    return {
        "schema_version": "x-feed-v1",
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "tweets": [
            {
                "tweet_id": "42",
                "text": "发布新的 AI 模型",
                "author": "OpenAI",
                "created_at": "2026-08-04T00:00:00.000Z",
                "url": "https://x.com/OpenAI/status/42",
                "source_name": "OpenAI",
                "source_handle": "OpenAI",
                "source_tier": "primary",
                "official": True,
            }
        ],
    }


def test_x_feed_collector_normalizes_fresh_public_tweet(monkeypatch):
    now = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: FakeResponse(_feed(now)),
    )

    items = XFeedCollector(
        feed_url="https://raw.githubusercontent.com/tanx0702/ai-daily-news/x-feed/x-feed.json",
        now=now,
    ).fetch()

    assert items == [
        {
            "id": "x-42",
            "title": "OpenAI: 发布新的 AI 模型",
            "url": "https://x.com/OpenAI/status/42",
            "source": "OpenAI (X)",
            "source_type": "x",
            "published_at": datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc),
            "published_source": "x_feed",
            "summary": "发布新的 AI 模型",
            "author": "OpenAI",
            "tags": ["x", "official"],
            "metrics": {
                "hn_score": 0,
                "hn_comments": 0,
                "github_stars": 0,
                "github_stars_recent": 0,
                "hf_likes": 0,
                "hf_downloads": 0,
                "arxiv_signal": 0,
                "cross_source_count": 0,
            },
            "scores": {
                "freshness": 0.0,
                "authority": 0.0,
                "community": 0.0,
                "technical": 0.0,
                "china_relevance": 0.0,
                "final": 0.0,
            },
            "topic_key": "",
            "source_tier": "primary",
            "x_official": True,
        }
    ]


def test_x_feed_collector_rejects_stale_snapshot(monkeypatch):
    now = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)
    stale = now - timedelta(hours=7)
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: FakeResponse(_feed(stale)),
    )

    items = XFeedCollector(feed_url="https://example.com/x-feed.json", now=now).fetch()

    assert items == []


def test_source_balance_limits_x_candidates_to_configured_daily_cap(monkeypatch):
    monkeypatch.setenv("DAILY_X_MAX_ITEMS", "5")
    x_items = [
        {
            "title": f"X update {index}",
            "url": f"https://x.com/source{index}/status/{index}",
            "source": f"X source {index}",
            "source_type": "x",
            "published_at": datetime.now(timezone.utc),
            "summary": "AI update",
            "metrics": {},
        }
        for index in range(8)
    ]
    rss_items = [
        {
            "title": f"RSS update {index}",
            "url": f"https://example.com/{index}",
            "source": f"RSS source {index}",
            "source_type": "rss",
            "published_at": datetime.now(timezone.utc),
            "summary": "AI update",
            "metrics": {},
        }
        for index in range(10)
    ]

    selected = collector._apply_source_balance([*x_items, *rss_items], top_n=10)[:10]

    assert sum(item["source_type"] == "x" for item in selected) == 5


def test_collect_news_merges_x_feed_candidates_with_existing_pipeline(monkeypatch):
    now = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)
    x_candidate = {
        "id": "x-42",
        "title": "OpenAI releases an AI model",
        "url": "https://x.com/OpenAI/status/42",
        "source": "OpenAI (X)",
        "source_type": "x",
        "published_at": now,
        "published_source": "x_feed",
        "summary": "OpenAI released a new model.",
        "author": "OpenAI",
        "tags": ["x", "official"],
        "metrics": {},
        "scores": {},
        "topic_key": "",
        "source_tier": "primary",
        "x_official": True,
    }
    monkeypatch.setattr(collector, "_load_sources", lambda _path: [])
    monkeypatch.setattr(collector, "_fetch_x", lambda _timeout: [x_candidate])
    monkeypatch.setenv("ENABLE_HN_COLLECTOR", "0")
    monkeypatch.setenv("ENABLE_GITHUB_COLLECTOR", "0")
    monkeypatch.setenv("ENABLE_HF_COLLECTOR", "0")
    monkeypatch.setenv("ENABLE_ARXIV_COLLECTOR", "0")
    monkeypatch.setenv("ENABLE_X_COLLECTOR", "1")

    items = collector.collect_news(top_n=1, hours=36)

    assert [item["id"] for item in items] == ["x-42"]


def test_official_x_source_keeps_official_brand_claim_confidence():
    item = {
        "title": "OpenAI releases a new model",
        "url": "https://x.com/OpenAI/status/42",
        "source": "OpenAI (X)",
        "source_type": "x",
        "published_at": datetime.now(timezone.utc),
        "summary": "Official product announcement.",
        "metrics": {"cross_source_count": 0},
        "x_official": True,
    }

    collector._score_item(item, [item])

    assert item["_brand_claim"]["confidence"] == "high"
    assert "_brand_penalty" not in item
