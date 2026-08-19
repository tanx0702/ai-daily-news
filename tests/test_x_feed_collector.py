from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from src import collector
from src.briefing.evidence import source_evidence_from_candidate
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


def _empty_feed(generated_at: datetime) -> dict:
    payload = _feed(generated_at)
    payload["tweets"] = []
    return payload


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
            "x_handle": "OpenAI",
            "x_official_source": "config/x_sources.json",
            "x_tweet_id": "42",
            "x_thread_id": "42",
            "x_reply_to_id": "",
            "x_quoted_id": "",
            "x_is_repost": False,
            "x_context_complete": False,
            "opinion_eligible": False,
            "content_type": "fact_event",
            "opinion_author": "",
            "opinion_original_post": False,
            "opinion_context_complete": False,
            "opinion_stance_type": "",
            "opinion_reason_codes": ["opinion_author_not_allowed"],
        }
    ]


@pytest.mark.parametrize(
    "text,extra",
    [
        ("RT @tomaarsen: released Sentence Transformers v6.0", {}),
        ("Congrats to our long-term partner on the launch of Miles v0.1!", {}),
        ("转发：OpenAI 发布新的 AI 模型", {"is_repost": True}),
    ],
)
def test_x_feed_collector_drops_reposts_and_promotional_posts(
    monkeypatch, text, extra
):
    now = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)
    payload = _feed(now)
    payload["tweets"][0].update({"text": text, **extra})
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: FakeResponse(payload),
    )

    assert XFeedCollector(
        feed_url="https://example.com/x-feed.json", now=now
    ).fetch() == []


def test_x_feed_collector_prefers_fresh_local_snapshot_without_http(tmp_path: Path, monkeypatch):
    now = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)
    local_path = tmp_path / "x-feed.json"
    local_path.write_text(json.dumps(_feed(now)), encoding="utf-8")
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: pytest.fail("fresh local snapshot must not use HTTP"),
    )

    items = XFeedCollector(
        feed_url="https://example.com/x-feed.json",
        local_snapshot_path=str(local_path),
        now=now,
    ).fetch()

    assert len(items) == 1


def test_x_feed_collector_treats_fresh_local_empty_snapshot_as_authoritative(
    tmp_path: Path, monkeypatch
):
    now = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)
    local_path = tmp_path / "x-feed.json"
    local_path.write_text(json.dumps(_empty_feed(now)), encoding="utf-8")
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: pytest.fail("fresh local empty snapshot must be authoritative"),
    )

    assert XFeedCollector(
        feed_url="https://example.com/x-feed.json",
        local_snapshot_path=str(local_path),
        now=now,
    ).fetch() == []


def test_x_feed_collector_falls_back_to_http_for_stale_local_snapshot(tmp_path: Path, monkeypatch):
    now = datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc)
    local_path = tmp_path / "x-feed.json"
    local_path.write_text(json.dumps(_feed(now - timedelta(hours=7))), encoding="utf-8")
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: FakeResponse(_feed(now)),
    )

    items = XFeedCollector(
        feed_url="https://example.com/x-feed.json",
        local_snapshot_path=str(local_path),
        now=now,
    ).fetch()

    assert len(items) == 1


def test_x_feed_collector_accepts_legacy_x_date_and_maps_thread_fields(monkeypatch):
    now = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)
    payload = _feed(now)
    payload["tweets"][0].update(
        {
            "created_at": "Sun Aug 03 06:00:00 +0000 2026",
            "thread_id": "40",
            "reply_to_id": "41",
            "quoted_id": "39",
        }
    )
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: FakeResponse(payload),
    )

    items = XFeedCollector(
        feed_url="https://example.com/x-feed.json",
        now=now,
    ).fetch()

    assert len(items) == 1
    assert items[0]["published_at"] == datetime(2026, 8, 3, 6, tzinfo=timezone.utc)
    assert items[0]["x_thread_id"] == "40"
    assert items[0]["x_reply_to_id"] == "41"
    assert items[0]["x_quoted_id"] == "39"

    evidence = source_evidence_from_candidate(items[0], trusted_x_collector=True)

    assert evidence is not None
    assert evidence.source_item_id == "42"
    assert evidence.thread_id == "40"
    assert evidence.reply_to_item_id == "41"
    assert evidence.quoted_item_id == "39"


def test_x_feed_collector_rejects_stale_snapshot(monkeypatch):
    now = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)
    stale = now - timedelta(hours=7)
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: FakeResponse(_feed(stale)),
    )

    items = XFeedCollector(feed_url="https://example.com/x-feed.json", now=now).fetch()

    assert items == []


def test_x_feed_collector_accepts_snapshot_at_exact_six_hour_boundary(monkeypatch):
    now = datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: FakeResponse(_feed(now - timedelta(hours=6))),
    )

    items = XFeedCollector(
        feed_url="https://example.com/x-feed.json",
        max_age_hours=6,
        now=now,
    ).fetch()

    assert len(items) == 1


def test_x_feed_collector_rejects_snapshot_over_six_hours(monkeypatch):
    now = datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: FakeResponse(
            _feed(now - timedelta(hours=6, microseconds=1))
        ),
    )

    items = XFeedCollector(
        feed_url="https://example.com/x-feed.json",
        max_age_hours=6,
        now=now,
    ).fetch()

    assert items == []


def test_x_feed_collector_rejects_snapshot_too_far_in_future(monkeypatch):
    now = datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: FakeResponse(
            _feed(now + timedelta(minutes=5, microseconds=1))
        ),
    )

    items = XFeedCollector(
        feed_url="https://example.com/x-feed.json",
        max_age_hours=6,
        now=now,
    ).fetch()

    assert items == []


def test_x_feed_collector_rejects_invalid_schema(monkeypatch):
    now = datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc)
    payload = _feed(now)
    payload["schema_version"] = "unexpected"
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: FakeResponse(payload),
    )

    assert XFeedCollector(
        feed_url="https://example.com/x-feed.json",
        now=now,
    ).fetch() == []


def test_x_feed_collector_ignores_uncontrolled_official_claim(monkeypatch):
    now = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)
    payload = _feed(now)
    payload["tweets"][0].update(
        {
            "source_name": "Unknown AI",
            "source_handle": "unknown_ai",
            "author": "Unknown AI",
            "url": "https://x.com/unknown_ai/status/42",
            "official": True,
        }
    )
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: FakeResponse(payload),
    )

    items = XFeedCollector(
        feed_url="https://example.com/x-feed.json",
        now=now,
    ).fetch()

    assert len(items) == 1
    assert items[0]["x_official"] is False
    assert items[0]["x_official_source"] == ""


def test_x_feed_collector_rejects_handle_that_does_not_match_status_url(monkeypatch):
    now = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)
    payload = _feed(now)
    payload["tweets"][0]["url"] = "https://x.com/not_openai/status/42"
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: FakeResponse(payload),
    )

    items = XFeedCollector(
        feed_url="https://example.com/x-feed.json",
        now=now,
    ).fetch()

    assert items == []


def test_x_feed_collector_honors_explicit_empty_controlled_registry(monkeypatch):
    now = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "src.collectors.x_feed.requests.get",
        lambda *_args, **_kwargs: FakeResponse(_feed(now)),
    )

    items = XFeedCollector(
        feed_url="https://example.com/x-feed.json",
        now=now,
        source_registry={},
    ).fetch()

    assert len(items) == 1
    assert items[0]["x_official"] is False
    assert items[0]["x_official_source"] == ""


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
    now = datetime.now(timezone.utc)
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
