from datetime import datetime, timezone

from src.editorial_quality import annotate_editorial_candidates
from src.editorial_review import _parse_json_object, apply_editorial_review


NOW = datetime(2026, 7, 18, 8, tzinfo=timezone.utc)


def _item(title, summary):
    return {
        "title": title,
        "source_title": title,
        "source_summary": summary,
        "source_url": "https://example.test/article",
        "published_at": NOW,
        "source": "Example Media",
        "source_type": "rss",
        "source_tier": "media",
        "metrics": {},
    }


def test_editorial_review_groups_semantic_duplicates_and_blends_scores():
    cloud = _item(
        "Tencent announces an embodied AI platform at WAIC",
        "Tencent introduced cloud, model, and platform capabilities at the conference.",
    )
    glasses = _item(
        "Tencent shows WorkBuddy memory glasses at the World AI Conference",
        "Tencent presented a WorkBuddy hardware partner at the same conference.",
    )
    annotate_editorial_candidates([cloud, glasses], now=NOW)

    report = apply_editorial_review(
        [cloud, glasses],
        {
            "items": [
                {
                    "index": 1,
                    "event_key": "event:tencent:waic-2026",
                    "score": 9.2,
                    "reason": "The platform announcement has wider reader value.",
                },
                {
                    "index": 2,
                    "event_key": "event:tencent:waic-2026",
                    "score": 6.0,
                    "reason": "This is a narrower update from the same event.",
                },
            ]
        },
    )

    assert report["applied_count"] == 2
    assert cloud["_editorial"]["event_key"] == "event:tencent:waic-2026"
    assert glasses["_editorial"]["event_key"] == "event:tencent:waic-2026"
    assert cloud["_editorial"]["score"] > glasses["_editorial"]["score"]
    assert "llm_editorial_review" in cloud["_editorial"]["reasons"]


def test_editorial_review_ignores_invalid_indices_and_event_keys():
    item = _item("A source-backed update", "The source contains a concrete update.")
    annotate_editorial_candidates([item], now=NOW)
    original = item["_editorial"].copy()

    report = apply_editorial_review(
        [item],
        {"items": [{"index": 9, "event_key": "bad key with spaces", "score": 10}]},
    )

    assert report["applied_count"] == 0
    assert item["_editorial"] == original


def test_json_parser_extracts_the_first_valid_object_from_wrapped_response():
    payload = _parse_json_object(
        "模型说明：{\"items\":[{\"index\":1,\"event_key\":\"event:openai:scorecard\",\"score\":9}]} 后续说明 {not json}"
    )

    assert payload == {
        "items": [{"index": 1, "event_key": "event:openai:scorecard", "score": 9}]
    }
