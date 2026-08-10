from datetime import datetime, timedelta, timezone

from src.editorial_quality import annotate_editorial_candidates
from src.editorial_selection import select_editorial_candidates


NOW = datetime(2026, 7, 18, 8, tzinfo=timezone.utc)


def _item(title, summary, *, source="Example", source_type="rss", tier="media", **extra):
    item = {
        "title": title,
        "source_title": title,
        "source_summary": summary,
        "source_url": "https://example.test/article",
        "published_at": NOW - timedelta(hours=4),
        "source": source,
        "source_type": source_type,
        "source_tier": tier,
        "metrics": {},
    }
    item.update(extra)
    return item


def test_recent_github_push_is_labeled_activity_and_cannot_reach_nine_points():
    item = _item(
        "acme/agent: A useful coding agent",
        "GitHub recent activity project with 500 stars.",
        source="GitHub",
        source_type="github",
        tier="community",
        metrics={"github_activity_type": "recent_push", "github_stars": 500},
    )

    annotate_editorial_candidates([item], now=NOW)

    editorial = item["_editorial"]
    assert editorial["event_type"] == "github_activity"
    assert editorial["score"] < 9
    assert "github_activity_not_release" in editorial["reasons"]


def test_same_company_and_conference_share_an_event_key():
    cloud = _item(
        "腾讯发布具身智能全栈方案",
        "腾讯在世界人工智能大会（WAIC）介绍云、模型与平台方案。",
        source="36氪",
    )
    glasses = _item(
        "腾讯云 WorkBuddy 发布 AI 记忆眼镜",
        "腾讯在 WAIC 展示 WorkBuddy 生态中的 AI 记忆眼镜。",
        source="36氪",
    )

    annotate_editorial_candidates([cloud, glasses], now=NOW)

    assert cloud["_editorial"]["event_key"] == "event:tencent:waic"
    assert glasses["_editorial"]["event_key"] == "event:tencent:waic"


def test_primary_source_with_complete_evidence_can_reach_nine_points():
    item = _item(
        "OpenAI introduces a scorecard for the AI age",
        "OpenAI describes concrete measurements for successful AI work, cost, reliability, and return on compute.",
        source="OpenAI Blog",
        tier="primary",
    )

    annotate_editorial_candidates([item], now=NOW)

    editorial = item["_editorial"]
    assert editorial["evidence_complete"] is True
    assert editorial["score"] >= 9


def test_selection_prefers_editorial_evidence_over_raw_heat():
    activity = _item(
        "acme/agent: A useful coding agent",
        "GitHub recent activity project with 500 stars.",
        source="GitHub",
        source_type="github",
        tier="community",
        metrics={"github_activity_type": "recent_push", "github_stars": 500},
        _score=100,
    )
    primary = _item(
        "OpenAI introduces a scorecard for the AI age",
        "OpenAI describes concrete measurements for successful AI work.",
        source="OpenAI Blog",
        tier="primary",
        _score=10,
    )
    annotate_editorial_candidates([activity, primary], now=NOW)

    selected, _, _ = select_editorial_candidates(
        [activity, primary],
        target_count=1,
        min_primary_or_research=0,
    )

    assert selected == [primary]


def test_selection_keeps_one_story_for_one_company_conference_event():
    cloud = _item(
        "腾讯发布具身智能全栈方案",
        "腾讯在世界人工智能大会（WAIC）介绍云、模型与平台方案。",
        source="36氪",
        _score=100,
    )
    glasses = _item(
        "腾讯云 WorkBuddy 发布 AI 记忆眼镜",
        "腾讯在 WAIC 展示 WorkBuddy 生态中的 AI 记忆眼镜。",
        source="36氪",
        _score=90,
    )
    independent = _item(
        "OpenAI introduces a scorecard for the AI age",
        "OpenAI describes concrete measurements for successful AI work.",
        source="OpenAI Blog",
        tier="primary",
        _score=80,
    )
    annotate_editorial_candidates([cloud, glasses, independent], now=NOW)

    selected, reserves, report = select_editorial_candidates(
        [cloud, glasses, independent],
        target_count=3,
        min_primary_or_research=0,
    )

    assert {item["title"] for item in selected} == {cloud["title"], independent["title"]}
    assert reserves == [glasses]
    assert report["event_counts"] == {"event:tencent:waic": 1, independent["_editorial"]["event_key"]: 1}
