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


def test_pipeline_keeps_per_event_audit_for_rebuilt_and_rejected_candidates():
    result = run_brief_pipeline(
        [event(index) for index in range(1, 7)],
        (),
        config(),
        Builder(),
        Validator(),
    )

    audit = {entry["event"]["event_key"]: entry for entry in result.audit_entries}

    rejected = audit["event-1"]
    rebuilt = audit["event-2"]
    assert rejected["event"]["canonical_evidence"]["evidence_text"] == "Source 1 update."
    assert rejected["final_state"] == "rejected"
    assert rejected["final_reason_codes"] == ["unsupported_claim"]
    assert rejected["attempts"][0]["build"]["draft"]["chinese_title"] == "快讯 event-1"
    assert rejected["attempts"][0]["validation"]["action"] == "reject"
    assert [attempt["validation"]["action"] for attempt in rebuilt["attempts"]] == [
        "rebuild",
        "accept",
    ]
    assert rebuilt["final_state"] == "accepted"


def test_pipeline_audits_invalid_builder_responses_before_rejecting():
    class EmptyBuilder:
        def build_batch(self, events, attempts, rebuild_reasons=None):
            return ()

    result = run_brief_pipeline(
        (event(1),),
        (),
        config(),
        EmptyBuilder(),
        Validator(),
    )

    audit = result.audit_entries[0]
    assert audit["final_state"] == "rejected"
    assert audit["final_reason_codes"] == ["invalid_builder_response"]
    assert [entry["generation_attempt"] for entry in audit["attempts"]] == [1, 2]
    assert all(entry["validation"] is None for entry in audit["attempts"])


def test_pipeline_keeps_all_drafts_from_ambiguous_builder_response():
    class DuplicateBuilder:
        def build_batch(self, events, attempts, rebuild_reasons=None):
            value = events[0]
            generation_attempt = attempts.get(value.event_key, 0) + 1
            return (
                BuildResult(value.event_key, generation_attempt, draft(value), None),
                BuildResult(value.event_key, generation_attempt, draft(value), None),
            )

    result = run_brief_pipeline(
        (event(1),),
        (),
        config(),
        DuplicateBuilder(),
        Validator(),
    )

    audit = result.audit_entries[0]
    assert audit["final_state"] == "rejected"
    assert len(audit["attempts"][0]["build"]["responses"]) == 2
    assert all(
        response["draft"]["event_key"] == "event-1"
        for response in audit["attempts"][0]["build"]["responses"]
    )


def test_pipeline_audits_builder_response_with_invalid_attempt_number():
    class InvalidAttemptBuilder:
        def build_batch(self, events, attempts, rebuild_reasons=None):
            value = events[0]
            return (BuildResult(value.event_key, 3, draft(value), None),)

    result = run_brief_pipeline(
        (event(1),),
        (),
        config(),
        InvalidAttemptBuilder(),
        Validator(),
    )

    audit = result.audit_entries[0]
    assert audit["final_state"] == "rejected"
    assert audit["final_reason_codes"] == ["invalid_builder_response"]
    assert audit["attempts"][0]["build"]["draft"]["event_key"] == "event-1"
    assert audit["attempts"][0]["validation"] is None


def test_pipeline_audits_empty_drafts_before_rejecting():
    class EmptyDraftBuilder:
        def build_batch(self, events, attempts, rebuild_reasons=None):
            return tuple(
                BuildResult(
                    value.event_key,
                    attempts.get(value.event_key, 0) + 1,
                    None,
                    "generation_failed",
                )
                for value in events
            )

    result = run_brief_pipeline(
        (event(1),),
        (),
        config(),
        EmptyDraftBuilder(),
        Validator(),
    )

    audit = result.audit_entries[0]
    assert audit["final_state"] == "rejected"
    assert audit["final_reason_codes"] == ["generation_failed"]
    assert [entry["build"]["draft"] for entry in audit["attempts"]] == [None, None]
    assert all(entry["validation"] is None for entry in audit["attempts"])


def test_pipeline_audits_x_limit_before_generation():
    class UnexpectedBuilder:
        def build_batch(self, events, attempts, rebuild_reasons=None):
            raise AssertionError("X-limited event must not be built")

    result = run_brief_pipeline(
        (event(1, channel="x"),),
        (),
        config(max_x_items=0),
        UnexpectedBuilder(),
        Validator(),
    )

    audit = result.audit_entries[0]
    assert audit["attempts"] == []
    assert audit["final_state"] == "rejected"
    assert audit["final_reason_codes"] == ["x_limit"]


def test_pipeline_audits_x_limit_reached_during_selection():
    class AcceptingValidator:
        def validate(self, value, built, *, generation_attempt):
            return accepted(value)

    result = run_brief_pipeline(
        (event(1, channel="x"), event(2, channel="x")),
        (),
        config(max_x_items=1, builder_batch_size=1),
        Builder(),
        AcceptingValidator(),
    )

    audit = {entry["event"]["event_key"]: entry for entry in result.audit_entries}
    assert audit["event-2"]["attempts"] == []
    assert audit["event-2"]["final_state"] == "rejected"
    assert audit["event-2"]["final_reason_codes"] == ["x_limit"]


def test_pipeline_audits_quarantined_event():
    quarantined = QuarantinedEvent(
        evidence=event(1).canonical_evidence,
        duplicate_of="event-duplicate",
        reason_code="ambiguous_duplicate",
    )

    result = run_brief_pipeline(
        (event(1),),
        (quarantined,),
        config(),
        Builder(),
        Validator(),
    )

    audit = result.audit_entries[0]
    assert audit["attempts"] == []
    assert audit["final_state"] == "quarantined"
    assert audit["final_reason_codes"] == ["ambiguous_duplicate"]


def test_pipeline_keeps_standalone_quarantined_candidate_audit():
    quarantined = QuarantinedEvent(
        evidence=event(2).canonical_evidence,
        duplicate_of="event-duplicate",
        reason_code="ambiguous_duplicate",
    )

    result = run_brief_pipeline(
        (event(1),),
        (quarantined,),
        config(),
        Builder(),
        Validator(),
    )

    audit = next(
        entry
        for entry in result.audit_entries
        if entry["candidate_type"] == "quarantined_event"
    )
    assert audit["quarantined_event"]["evidence"]["evidence_text"] == "Source 2 update."
    assert audit["final_state"] == "quarantined"
    assert audit["final_reason_codes"] == ["ambiguous_duplicate"]


def test_pipeline_audits_candidates_skipped_after_target_is_reached():
    class AcceptingValidator:
        def validate(self, value, built, *, generation_attempt):
            return accepted(value)

    result = run_brief_pipeline(
        tuple(event(index) for index in range(1, 7)),
        (),
        config(),
        Builder(),
        AcceptingValidator(),
    )

    audit = {entry["event"]["event_key"]: entry for entry in result.audit_entries}
    assert audit["event-6"]["attempts"] == []
    assert audit["event-6"]["final_state"] == "not_selected"
    assert audit["event-6"]["final_reason_codes"] == ["target_reached"]


def test_pipeline_keeps_unmatched_builder_response_in_audit():
    class ExtraResponseBuilder:
        def build_batch(self, events, attempts, rebuild_reasons=None):
            value = events[0]
            generation_attempt = attempts.get(value.event_key, 0) + 1
            return (
                BuildResult(value.event_key, generation_attempt, draft(value), None),
                BuildResult("unknown-event", generation_attempt, draft(value), None),
            )

    class AcceptingValidator:
        def validate(self, value, built, *, generation_attempt):
            return accepted(value)

    result = run_brief_pipeline(
        (event(1),),
        (),
        config(),
        ExtraResponseBuilder(),
        AcceptingValidator(),
    )

    audit = next(
        entry
        for entry in result.audit_entries
        if entry["candidate_type"] == "unmatched_builder_response"
    )
    assert audit["builder_response"]["event_key"] == "unknown-event"
    assert audit["builder_response"]["draft"]["event_key"] == "event-1"
    assert audit["final_state"] == "rejected"
    assert audit["final_reason_codes"] == ["unmatched_builder_response"]


def test_pipeline_keeps_distinct_audits_for_duplicate_event_keys():
    first = event(1)
    duplicate = MergedEvent(
        first.event_key,
        event(2).canonical_evidence,
        editorial_score=first.editorial_score - 1,
    )

    result = run_brief_pipeline(
        (first, duplicate),
        (),
        config(),
        Builder(),
        Validator(),
    )

    audit = [
        entry
        for entry in result.audit_entries
        if entry["candidate_type"] == "merged_event"
    ]
    assert len(audit) == 2
    assert audit[1]["event"]["canonical_evidence"]["evidence_text"] == "Source 2 update."
    assert audit[1]["final_state"] == "rejected"
    assert audit[1]["final_reason_codes"] == ["duplicate_event"]


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
    assert result.diagnostics["rules_only_count"] == 5
    assert result.diagnostics["rules_and_llm_count"] == 0
    assert result.diagnostics["build_attempt_count"] == 5
    accepted_audit = [
        entry for entry in result.audit_entries if entry["final_state"] == "accepted"
    ]
    assert all(
        entry["attempts"][-1]["validation"]["validation_mode"] == "rules_only"
        for entry in accepted_audit
    )


def test_pipeline_does_not_double_count_quality_service_diagnostics():
    class DiagnosticDegradedValidator:
        diagnostics = {
            "quality_llm_unavailable_count": 1,
            "quality_llm_circuit_open_count": 4,
        }

        def validate(self, value, built, *, generation_attempt):
            result = accepted(value)
            return ValidationResult(
                "accept",
                ("quality_llm_unavailable", "rules_only_used"),
                "rules_only",
                validated_item=result.validated_item,
            )

    result = run_brief_pipeline(
        [event(index) for index in range(1, 6)],
        (),
        config(),
        Builder(),
        DiagnosticDegradedValidator(),
    )

    assert result.diagnostics["quality_llm_unavailable_count"] == 1
    assert result.diagnostics["quality_llm_circuit_open_count"] == 4
    assert result.diagnostics["rules_only_used_count"] == 5


def test_pipeline_counts_build_attempts_without_valid_drafts():
    class MissingBuilder:
        diagnostics = {}

        def build_batch(self, events, attempts, rebuild_reasons=None):
            return ()

    result = run_brief_pipeline(
        [event(index) for index in range(1, 6)],
        (),
        config(),
        MissingBuilder(),
        Validator(),
    )

    assert result.diagnostics["build_attempt_count"] == 10


def test_pipeline_counts_rules_and_llm_items_separately():
    class ReviewedValidator:
        def validate(self, value, built, *, generation_attempt):
            return accepted(value, validation_mode="rules_and_llm")

    result = run_brief_pipeline(
        [event(index) for index in range(1, 6)],
        (),
        config(),
        Builder(),
        ReviewedValidator(),
    )

    assert result.decision.action == "create"
    assert result.diagnostics["rules_only_count"] == 0
    assert result.diagnostics["rules_and_llm_count"] == 5


def test_pipeline_merges_builder_and_validator_service_diagnostics():
    builder = Builder()
    builder.diagnostics = {
        "content_llm_success_count": 1,
        "content_llm_timeout_count": 1,
    }

    class DiagnosticValidator:
        diagnostics = {
            "quality_llm_success_count": 3,
            "quality_llm_circuit_open_count": 2,
        }

        def validate(self, value, built, *, generation_attempt):
            return accepted(value)

    result = run_brief_pipeline(
        [event(index) for index in range(1, 6)],
        (),
        config(),
        builder,
        DiagnosticValidator(),
    )

    assert result.diagnostics["content_llm_success_count"] == 1
    assert result.diagnostics["content_llm_timeout_count"] == 1
    assert result.diagnostics["quality_llm_success_count"] == 3
    assert result.diagnostics["quality_llm_circuit_open_count"] == 2
