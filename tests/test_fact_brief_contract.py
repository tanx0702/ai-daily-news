from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from src.briefing.evidence import source_evidence_from_candidate
from src.briefing.models import (
    BriefItem,
    BuiltBrief,
    DraftDecision,
    DraftExecution,
    EvidenceBinding,
    MergedEvent,
    QuarantinedEvent,
    RebuildRequest,
    SourceEvidence,
    ValidationResult,
)


def evidence(**overrides):
    values = {
        "publisher_id": "openai",
        "publisher_name": "OpenAI",
        "channel": "rss",
        "authority": "official",
        "is_official": True,
        "official_identity_source": "config/rss_sources.json",
        "source_title": "OpenAI releases Model 5",
        "evidence_text": "OpenAI releases Model 5 on August 7, 2026.",
        "url": "https://openai.com/news/model-5",
        "published_at": "2026-08-07T08:00:00+00:00",
    }
    values.update(overrides)
    return SourceEvidence(**values)


def binding(**overrides):
    values = {
        "claim": "OpenAI 发布 Model 5",
        "source_quote": "OpenAI releases Model 5",
        "source_url": "https://openai.com/news/model-5",
    }
    values.update(overrides)
    return EvidenceBinding(**values)


def brief_item(**overrides):
    source = evidence()
    values = {
        "event_key": "openai-model-5",
        "chinese_title": "OpenAI 发布 Model 5",
        "brief": "OpenAI 于 2026 年 8 月 7 日发布 Model 5。",
        "canonical_source": source,
        "related_sources": (evidence(publisher_id="reuters", publisher_name="Reuters"),),
        "published_at": source.published_at,
        "evidence_bindings": (binding(),),
        "content_origin": "llm",
        "validation_mode": "rules_only",
    }
    values.update(overrides)
    return BriefItem(**values)


def test_source_evidence_round_trips_timezone_aware_iso_timestamp():
    original = evidence(
        discovered_via="hacker_news",
        evidence_quality="title_only",
        source_item_id="42",
        thread_id="40",
        reply_to_item_id="41",
        quoted_item_id="39",
    )

    restored = SourceEvidence.from_dict(original.to_dict())

    assert restored == original
    assert restored.thread_id == "40"


def test_old_source_evidence_payload_defaults_new_fields():
    payload = evidence().to_dict()
    for field in (
        "discovered_via",
        "evidence_quality",
        "source_item_id",
        "thread_id",
        "reply_to_item_id",
        "quoted_item_id",
    ):
        payload.pop(field, None)

    restored = SourceEvidence.from_dict(payload)

    assert restored.discovered_via == ""
    assert restored.evidence_quality == "ready"
    assert restored.source_item_id == ""
    assert restored.thread_id == ""


def test_ai_update_contract_round_trips():
    source = evidence(content_type="ai_update")

    restored = SourceEvidence.from_dict(source.to_dict())

    assert restored.content_type == "ai_update"


def test_non_x_candidate_preserves_classified_ai_update_type():
    source = source_evidence_from_candidate({
        "title": "H3 Max generates high-quality video",
        "summary": "H3 Max generates high-quality video.",
        "url": "https://example.test/h3-max",
        "source": "Example Media",
        "source_type": "rss",
        "source_tier": "media",
        "published_at": "2026-08-28T00:00:00+00:00",
        "content_type": "ai_update",
    })

    assert source is not None
    assert source.content_type == "ai_update"


def test_source_evidence_rejects_naive_or_invalid_timestamp():
    with pytest.raises(ValueError, match="timezone"):
        evidence(published_at="2026-08-07T08:00:00")

    with pytest.raises(ValueError, match="published_at"):
        evidence(published_at="not-a-date")


def test_merged_event_and_quarantine_use_immutable_sequences():
    source = evidence()
    event = MergedEvent(
        event_key="openai-model-5",
        canonical_evidence=source,
        related_evidence=[evidence(publisher_id="reuters")],
        editorial_score=9.5,
        rank_reasons=["official_source"],
    )
    quarantined = QuarantinedEvent(
        evidence=evidence(publisher_id="community"),
        duplicate_of=event.event_key,
        reason_code="ambiguous_duplicate",
    )

    assert isinstance(event.related_evidence, tuple)
    assert isinstance(event.rank_reasons, tuple)
    assert quarantined.reason_code == "ambiguous_duplicate"
    with pytest.raises(FrozenInstanceError):
        event.event_key = "changed"


def test_built_brief_is_replaced_instead_of_mutated():
    draft = BuiltBrief(
        event_key="openai-model-5",
        input_index=0,
        chinese_title="OpenAI 发布 Model 5",
        brief="OpenAI 发布 Model 5。",
        evidence_bindings=(binding(),),
        content_origin="llm",
    )

    with pytest.raises(FrozenInstanceError):
        draft.brief = "changed"


def test_brief_item_public_projection_excludes_raw_evidence_and_round_trips():
    original = brief_item()

    payload = original.to_dict()
    restored = BriefItem.from_dict(payload)

    assert "evidence_text" not in payload["canonical_source"]
    assert "source_title" not in payload["canonical_source"]
    assert all("evidence_text" not in source for source in payload["related_sources"])
    assert restored.event_key == original.event_key
    assert restored.chinese_title == original.chinese_title
    assert restored.brief == original.brief
    assert restored.canonical_source.url == original.canonical_source.url
    assert restored.evidence_bindings == original.evidence_bindings


def test_empty_brief_is_a_serializable_title_only_item():
    original = brief_item(
        brief="",
        brief_mode="title_only",
        brief_reason="brief_empty",
    )

    restored = BriefItem.from_dict(original.to_dict())

    assert restored.brief == ""
    assert restored.brief_mode == "title_only"
    assert restored.brief_reason == "brief_empty"


def test_brief_item_round_trips_content_type_and_opinion_author():
    source = evidence(
        channel="x",
        authority="research",
        is_official=False,
        official_identity_source="",
        content_type="attributed_opinion",
        opinion_author="Andrej Karpathy",
        opinion_eligible=True,
        original_post=True,
        context_complete=True,
        stance_type="opinion",
    )
    original = brief_item(
        canonical_source=source,
        related_sources=(),
        published_at=source.published_at,
        content_type="attributed_opinion",
        opinion_author="Andrej Karpathy",
    )

    restored = BriefItem.from_dict(original.to_dict())

    assert restored.content_type == "attributed_opinion"
    assert restored.opinion_author == "Andrej Karpathy"


def test_brief_item_rejects_invalid_brief_mode():
    with pytest.raises(ValueError, match="brief_mode"):
        brief_item(brief_mode="summary")


def test_brief_item_requires_mode_to_match_brief_content():
    with pytest.raises(ValueError, match="title_only"):
        brief_item(brief_mode="title_only")

    with pytest.raises(ValueError, match="expanded"):
        brief_item(brief="", brief_mode="expanded")


def test_brief_item_rejects_invalid_content_and_validation_modes():
    with pytest.raises(ValueError, match="content_origin"):
        brief_item(content_origin="mixed")

    with pytest.raises(ValueError, match="validation_mode"):
        brief_item(validation_mode="llm_passed")


def test_validation_result_enforces_action_payload_invariants():
    item = brief_item()
    rebuild = RebuildRequest(
        event_key=item.event_key,
        reason_codes=("unsupported_claim",),
        generation_attempt=2,
    )

    accepted = ValidationResult(
        action="accept",
        reason_codes=(),
        validation_mode="rules_only",
        validated_item=item,
    )
    rebuilding = ValidationResult(
        action="rebuild",
        reason_codes=("unsupported_claim",),
        validation_mode="rules_only",
        rebuild_request=rebuild,
    )
    rejected = ValidationResult(
        action="reject",
        reason_codes=("missing_evidence",),
        validation_mode="rules_only",
    )

    assert accepted.validated_item is item
    assert rebuilding.rebuild_request is rebuild
    assert rejected.validated_item is None

    with pytest.raises(ValueError, match="accept"):
        ValidationResult("accept", (), "rules_only")
    with pytest.raises(ValueError, match="rebuild"):
        ValidationResult("rebuild", (), "rules_only")
    with pytest.raises(ValueError, match="reject"):
        ValidationResult("reject", (), "rules_only", validated_item=item)


def test_rebuild_request_only_allows_the_second_generation_attempt():
    with pytest.raises(ValueError, match="generation_attempt"):
        RebuildRequest("event", ("unsupported_claim",), 1)


def test_draft_decision_is_serializable_and_has_frozen_diagnostics():
    decision = DraftDecision(
        action="create",
        selected_count=5,
        min_items=5,
        max_items=15,
        x_count=1,
        max_x_items=5,
        update_count=3,
        max_update_items=8,
        target_update_items=5,
        target_opinion_items=5,
        reasons=(),
        excluded_counts={"translation_failed": 2},
        source_counts={"OpenAI": 1},
    )

    restored = DraftDecision.from_dict(decision.to_dict())

    assert restored == decision
    assert isinstance(decision.excluded_counts, MappingProxyType)
    with pytest.raises(TypeError):
        decision.source_counts["Other"] = 1
    with pytest.raises(ValueError, match="action"):
        DraftDecision("review", 5, 5, 15, 0, 5)


def test_legacy_draft_decision_defaults_content_mix_counts():
    payload = DraftDecision(
        action="create",
        selected_count=5,
        min_items=5,
        max_items=20,
        x_count=1,
        max_x_items=5,
    ).to_dict()
    for field in (
        "update_count",
        "max_update_items",
        "target_update_items",
        "target_opinion_items",
    ):
        payload.pop(field)

    restored = DraftDecision.from_dict(payload)

    assert (
        restored.update_count,
        restored.max_update_items,
        restored.target_update_items,
        restored.target_opinion_items,
    ) == (0, 0, 0, 0)


def test_draft_execution_round_trips_and_validates_status_contract():
    execution = DraftExecution(
        status="draft_created",
        reason=None,
        started_at="2026-08-07T08:00:00+00:00",
        completed_at="2026-08-07T08:01:00+00:00",
        media_id="media-1",
    )

    assert DraftExecution.from_dict(execution.to_dict()) == execution

    with pytest.raises(ValueError, match="media_id"):
        DraftExecution(
            status="draft_created",
            reason=None,
            started_at="2026-08-07T08:00:00+00:00",
            completed_at="2026-08-07T08:01:00+00:00",
        )
    with pytest.raises(ValueError, match="status"):
        DraftExecution(
            status="pending",
            reason=None,
            started_at="2026-08-07T08:00:00+00:00",
            completed_at="2026-08-07T08:01:00+00:00",
        )
