from src.briefing.builder import BuildResult
from src.briefing.config import BriefingConfig
from src.briefing.models import (
    BriefItem,
    BuiltBrief,
    EvidenceBinding,
    MergedEvent,
    RebuildRequest,
    QuarantinedEvent,
    SourceEvidence,
    ValidationResult,
)
from src.briefing.pipeline import run_brief_pipeline


def config(**values) -> BriefingConfig:
    defaults = {
        "min_items": 5,
        "max_items": 5,
        "candidate_pool_size": 6,
        "max_x_items": 5,
        "x_feed_max_age_hours": 6,
    }
    defaults.update(values)
    return BriefingConfig(**defaults)


def event(index: int, *, channel: str = "rss") -> MergedEvent:
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
    return MergedEvent(f"event-{index}", source, editorial_score=10 - index)


def draft(value: MergedEvent) -> BuiltBrief:
    source = value.canonical_evidence
    return BuiltBrief(
        event_key=value.event_key,
        input_index=1,
        chinese_title=f"快讯 {value.event_key}",
        brief=f"快讯 {value.event_key}。",
        evidence_bindings=(
            EvidenceBinding(f"快讯 {value.event_key}", source.evidence_text, source.url),
        ),
        content_origin="source",
    )


def accepted(value: MergedEvent, *, validation_mode: str = "rules_only") -> ValidationResult:
    source = value.canonical_evidence
    return ValidationResult(
        "accept",
        (),
        validation_mode,
        validated_item=BriefItem(
            event_key=value.event_key,
            chinese_title=f"快讯 {value.event_key}",
            brief=f"快讯 {value.event_key}。",
            canonical_source=source,
            related_sources=(),
            published_at=source.published_at,
            evidence_bindings=(
                EvidenceBinding(f"快讯 {value.event_key}", source.evidence_text, source.url),
            ),
            content_origin="source",
            validation_mode=validation_mode,
        ),
    )


class Builder:
    def __init__(self):
        self.calls = []

    def build_batch(self, events, attempts, rebuild_reasons=None):
        self.calls.append(([value.event_key for value in events], dict(attempts)))
        return tuple(
            BuildResult(value.event_key, attempts.get(value.event_key, 0) + 1, draft(value), None)
            for value in events
        )


class Validator:
    def __init__(self):
        self.seen = {}

    def validate(self, value, built, *, generation_attempt):
        self.seen[value.event_key] = self.seen.get(value.event_key, 0) + 1
        if value.event_key == "event-1":
            return ValidationResult("reject", ("unsupported_claim",), "rules_only")
        if value.event_key == "event-2" and generation_attempt == 1:
            return ValidationResult(
                "rebuild",
                ("unsupported_claim",),
                "rules_only",
                rebuild_request=RebuildRequest(value.event_key, ("unsupported_claim",), 2),
            )
        return accepted(value)


def test_pipeline_rejects_backfills_and_rebuilds_before_the_single_decision():
    builder = Builder()
    validator = Validator()

    result = run_brief_pipeline(
        [event(index) for index in range(1, 7)],
        (),
        config(),
        builder,
        validator,
    )

    assert result.decision.action == "create"
    assert [item.event_key for item in result.accepted_items] == [
        "event-2", "event-3", "event-4", "event-5", "event-6",
    ]
    assert validator.seen["event-2"] == 2
    assert result.exclusions["unsupported_claim"] == 1
    assert result.diagnostics["reserve_fill_count"] == 1
    assert result.diagnostics["rules_only_count"] == 5


def test_pipeline_keeps_rules_only_items_and_enforces_x_limit_after_acceptance():
    class RulesOnlyValidator:
        def validate(self, value, built, *, generation_attempt):
            return accepted(value, validation_mode="rules_only")

    result = run_brief_pipeline(
        [event(1, channel="x"), event(2, channel="x")] + [event(index) for index in range(3, 8)],
        (),
        config(max_x_items=1),
        Builder(),
        RulesOnlyValidator(),
    )

    assert result.decision.action == "create"
    assert result.decision.x_count == 1
    assert len(result.accepted_items) == 5
    assert result.exclusions["x_limit"] == 1


def test_pipeline_rejects_validated_item_that_does_not_match_current_event():
    events = [event(index) for index in range(1, 7)]

    class MismatchedValidator:
        def validate(self, value, built, *, generation_attempt):
            return accepted(events[1] if value.event_key == "event-1" else value)

    result = run_brief_pipeline(events, (), config(), Builder(), MismatchedValidator())

    assert "event-1" not in [item.event_key for item in result.accepted_items]
    assert result.exclusions["invalid_builder_response"] == 1


def test_pipeline_rejects_item_whose_canonical_channel_does_not_match_event():
    events = [event(1, channel="x")] + [event(index) for index in range(2, 7)]

    class SpoofedValidator:
        def validate(self, value, built, *, generation_attempt):
            valid = accepted(value).validated_item
            rss_source = SourceEvidence(
                publisher_id=valid.canonical_source.publisher_id,
                publisher_name=valid.canonical_source.publisher_name,
                channel="rss",
                authority=valid.canonical_source.authority,
                is_official=valid.canonical_source.is_official,
                official_identity_source=valid.canonical_source.official_identity_source,
                source_title=valid.canonical_source.source_title,
                evidence_text=valid.canonical_source.evidence_text,
                url=valid.canonical_source.url,
                published_at=valid.canonical_source.published_at,
            )
            return ValidationResult(
                "accept",
                (),
                "rules_only",
                validated_item=BriefItem(
                    event_key=valid.event_key,
                    chinese_title=valid.chinese_title,
                    brief=valid.brief,
                    canonical_source=rss_source,
                    related_sources=(),
                    published_at=valid.published_at,
                    evidence_bindings=valid.evidence_bindings,
                    content_origin=valid.content_origin,
                    validation_mode=valid.validation_mode,
                ),
            )

    result = run_brief_pipeline(
        events,
        (),
        config(max_x_items=5),
        Builder(),
        SpoofedValidator(),
    )

    assert "event-1" not in [item.event_key for item in result.accepted_items]
    assert result.decision.x_count == 0
    assert result.exclusions["invalid_builder_response"] == 1


def test_pipeline_never_allows_a_third_generation_attempt():
    class BoundedBuilder(Builder):
        def build_batch(self, events, attempts, rebuild_reasons=None):
            if any(attempts.get(value.event_key, 0) >= 2 for value in events):
                raise AssertionError("third generation attempt")
            return super().build_batch(events, attempts, rebuild_reasons)

    class AlwaysRebuildValidator:
        def validate(self, value, built, *, generation_attempt):
            return ValidationResult(
                "rebuild",
                ("unsupported_claim",),
                "rules_only",
                rebuild_request=RebuildRequest(
                    value.event_key,
                    ("unsupported_claim",),
                    2,
                ),
            )

    builder = BoundedBuilder()
    result = run_brief_pipeline(
        [event(index) for index in range(1, 7)],
        (),
        config(),
        builder,
        AlwaysRebuildValidator(),
    )

    assert max(len([call for call in builder.calls if f"event-{index}" in call[0]]) for index in range(1, 7)) == 2
    assert result.decision.action == "block"


def test_pipeline_records_quarantine_and_quality_degradation_diagnostics():
    events = [event(index) for index in range(1, 6)]
    quarantine = QuarantinedEvent(
        evidence=event(6).canonical_evidence,
        duplicate_of=events[0].event_key,
        reason_code="ambiguous_duplicate",
    )

    class DegradedValidator:
        def validate(self, value, built, *, generation_attempt):
            result = accepted(value)
            return ValidationResult(
                "accept",
                ("quality_llm_unavailable", "rules_only_used"),
                "rules_only",
                validated_item=result.validated_item,
            )

    result = run_brief_pipeline(
        events,
        (quarantine,),
        config(),
        Builder(),
        DegradedValidator(),
    )

    assert result.exclusions["ambiguous_duplicate"] == 1
    assert result.diagnostics["quality_llm_unavailable_count"] == 5
    assert result.diagnostics["rules_only_used_count"] == 5
