from datetime import datetime, timezone

from src.briefing.config import BriefingConfig
from src.briefing.decision import (
    blocked_execution,
    decide_draft,
    draft_created_execution,
    dry_run_execution,
    failed_execution,
)
from src.briefing.models import BriefItem, EvidenceBinding, SourceEvidence


def config(**values) -> BriefingConfig:
    defaults = {
        "min_items": 5,
        "max_items": 15,
        "candidate_pool_size": 45,
        "max_x_items": 5,
        "x_feed_max_age_hours": 6,
    }
    defaults.update(values)
    return BriefingConfig(**defaults)


def item(
    index: int,
    *,
    channel: str = "rss",
    event_key: str | None = None,
    validation_mode: str = "rules_only",
) -> BriefItem:
    source = SourceEvidence(
        publisher_id=f"publisher-{index}",
        publisher_name=f"Publisher {index}",
        channel=channel,
        authority="official",
        is_official=True,
        official_identity_source="source_config",
        source_title=f"Source {index}",
        evidence_text=f"Source {index} update.",
        url=f"https://example.test/{index}",
        published_at="2026-08-07T08:00:00+00:00",
    )
    return BriefItem(
        event_key=event_key or f"event-{index}",
        chinese_title=f"快讯 {index}",
        brief=f"快讯 {index}。",
        canonical_source=source,
        related_sources=(),
        published_at=source.published_at,
        evidence_bindings=(EvidenceBinding(f"快讯 {index}", source.evidence_text, source.url),),
        content_origin="source",
        validation_mode=validation_mode,
    )


def test_exactly_five_valid_items_create_without_source_concentration_blocker():
    items = [item(index) for index in range(1, 6)]

    decision = decide_draft(items, config())

    assert decision.action == "create"
    assert decision.selected_count == 5
    assert decision.reasons == ()


def test_rules_only_quality_degradation_does_not_block_five_valid_items():
    items = [
        item(index, validation_mode="rules_only" if index < 4 else "rules_and_llm")
        for index in range(1, 6)
    ]

    decision = decide_draft(items, config())

    assert decision.action == "create"
    assert decision.reasons == ()


def test_too_few_items_blocks_only_for_insufficient_items():
    decision = decide_draft([item(index) for index in range(1, 5)], config())

    assert decision.action == "block"
    assert decision.reasons == ("insufficient_items",)


def test_duplicate_quarantined_over_limit_and_invalid_items_block():
    duplicate = [item(index) for index in range(1, 5)] + [item(5, event_key="event-1")]
    over_limit = [item(index) for index in range(1, 17)]
    quarantined = [item(index) for index in range(1, 6)]
    invalid = [item(index) for index in range(1, 5)] + [object()]

    assert "duplicate_event_remaining" in decide_draft(duplicate, config()).reasons
    assert "invalid_final_item" in decide_draft(over_limit, config()).reasons
    assert "duplicate_event_remaining" in decide_draft(
        quarantined, config(), quarantined_keys=("event-3",)
    ).reasons
    assert "invalid_final_item" in decide_draft(invalid, config()).reasons


def test_structurally_invalid_brief_items_cannot_create_a_draft():
    values = [item(index) for index in range(1, 6)]
    invalid = values[0]
    values[0] = BriefItem(
        event_key=invalid.event_key,
        chinese_title="",
        brief="",
        canonical_source=invalid.canonical_source,
        related_sources=(),
        published_at=invalid.published_at,
        evidence_bindings=(),
        content_origin="source",
        validation_mode="rules_only",
    )

    decision = decide_draft(values, config())

    assert decision.action == "block"
    assert "invalid_final_item" in decision.reasons


def test_execution_helpers_return_aware_stable_statuses():
    at = datetime(2026, 8, 7, 8, tzinfo=timezone.utc)

    assert blocked_execution("insufficient_items", now=at).status == "blocked"
    assert dry_run_execution(now=at).status == "dry_run"
    assert draft_created_execution("media-id", now=at).media_id == "media-id"
    assert failed_execution("wechat_draft_failed", now=at).reason == "wechat_draft_failed"


def test_incomplete_semantic_dedup_blocks_creation():
    decision = decide_draft(
        [item(index) for index in range(1, 6)],
        config(),
        semantic_dedup_complete=False,
    )

    assert decision.action == "block"
    assert decision.reasons == ("duplicate_event_remaining",)


def test_remaining_semantic_conflict_keys_block_creation():
    decision = decide_draft(
        [item(index) for index in range(1, 6)],
        config(),
        semantic_conflict_keys=("event-2", "event-3"),
    )

    assert decision.action == "block"
    assert decision.reasons == ("duplicate_event_remaining",)


def test_resolved_uncertain_duplicate_exclusion_does_not_block_clean_items():
    decision = decide_draft(
        [item(index) for index in range(1, 6)],
        config(),
        excluded_counts={"semantic_duplicate_unresolved": 1},
        semantic_dedup_complete=True,
    )

    assert decision.action == "create"
    assert decision.reasons == ()
