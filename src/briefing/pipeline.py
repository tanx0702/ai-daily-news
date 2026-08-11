"""Side-effect-free build, validation, selection, and decision orchestration."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping, Protocol, Sequence

from src.briefing.builder import BuildResult
from src.briefing.config import BriefingConfig
from src.briefing.decision import decide_draft
from src.briefing.models import BriefItem, DraftDecision, MergedEvent, QuarantinedEvent
from src.briefing.selector import BriefSelector


_DEGRADATION_REASONS = {
    "quality_llm_unavailable",
    "quality_llm_invalid_response",
    "rules_only_used",
}


class BriefBuilderProtocol(Protocol):
    def build_batch(
        self,
        events: Sequence[MergedEvent],
        attempts: Mapping[str, int],
        rebuild_reasons: Mapping[str, Sequence[str]] | None = None,
    ) -> tuple[BuildResult, ...]: ...


class BriefValidatorProtocol(Protocol):
    def validate(
        self,
        event: MergedEvent,
        draft: object,
        *,
        generation_attempt: int,
        now: datetime | None = None,
    ): ...


@dataclass(frozen=True, slots=True)
class BriefPipelineResult:
    accepted_items: tuple[BriefItem, ...]
    decision: DraftDecision
    exclusions: Mapping[str, int]
    diagnostics: Mapping[str, int]
    audit_entries: tuple[Mapping[str, object], ...]


def run_brief_pipeline(
    events: Iterable[MergedEvent],
    quarantined: Iterable[QuarantinedEvent],
    config: BriefingConfig,
    builder: BriefBuilderProtocol,
    validator: BriefValidatorProtocol,
    *,
    now: datetime | None = None,
) -> BriefPipelineResult:
    """Build and fully validate events until the target or queue exhaustion."""
    event_values = tuple(events)
    quarantined_values = tuple(quarantined)
    quarantined_urls = {value.evidence.url for value in quarantined_values}
    quarantine_reasons_by_url: dict[str, list[str]] = {}
    for value in quarantined_values:
        quarantine_reasons_by_url.setdefault(value.evidence.url, []).append(
            value.reason_code
        )
    quarantined_keys = tuple(
        event.event_key
        for event in event_values
        if event.canonical_evidence.url in quarantined_urls
    )
    selector = BriefSelector(event_values, config, quarantined_keys=quarantined_keys)
    initial_order = tuple(event.event_key for event in selector.pending())
    initial_positions = {key: index for index, key in enumerate(initial_order, 1)}
    queue = deque(selector.pending())
    attempts: dict[str, int] = {}
    rebuild_reasons: dict[str, tuple[str, ...]] = {}
    audit_entries: list[dict[str, object]] = []
    audit_by_event_identity: dict[int, dict[str, object]] = {}
    seen_event_keys: set[str] = set()
    for position, event in enumerate(event_values, 1):
        audit_entry: dict[str, object] = {
            "candidate_type": "merged_event",
            "candidate_id": f"merged:{position}",
            "event": event.to_dict(),
            "attempts": [],
            "final_state": "not_evaluated",
            "final_reason_codes": [],
        }
        audit_entries.append(audit_entry)
        audit_by_event_identity.setdefault(id(event), audit_entry)
        if event.event_key in seen_event_keys:
            _finalize_audit(audit_entry, "rejected", ("duplicate_event",))
        else:
            seen_event_keys.add(event.event_key)

    quarantined_audit_entries = [
        {
            "candidate_type": "quarantined_event",
            "candidate_id": f"quarantined:{position}",
            "quarantined_event": value.to_dict(),
            "attempts": [],
            "final_state": "quarantined",
            "final_reason_codes": [value.reason_code],
        }
        for position, value in enumerate(quarantined_values, 1)
    ]
    unmatched_builder_audit_entries: list[dict[str, object]] = []
    for event in event_values:
        audit_entry = audit_by_event_identity[id(event)]
        quarantine_reasons = quarantine_reasons_by_url.get(
            event.canonical_evidence.url,
            [],
        )
        if quarantine_reasons:
            _finalize_audit(
                audit_entry,
                "quarantined",
                quarantine_reasons,
            )
        elif event.canonical_evidence.channel == "x" and config.max_x_items == 0:
            _finalize_audit(audit_entry, "rejected", ("x_limit",))
    diagnostics: Counter[str] = Counter(
        reserve_fill_count=0,
        source_fallback_count=0,
        rules_only_count=0,
    )

    while queue and len(selector.accepted_items) < config.max_items:
        batch: list[MergedEvent] = []
        remaining_slots = config.max_items - len(selector.accepted_items)
        batch_limit = min(config.builder_batch_size, remaining_slots)
        while queue and len(batch) < batch_limit:
            event = queue.popleft()
            audit_entry = audit_by_event_identity[id(event)]
            if selector.can_attempt(event):
                batch.append(event)
            elif event.canonical_evidence.channel == "x":
                selector.reject(event.event_key, "x_limit")
                _finalize_audit(audit_entry, "rejected", ("x_limit",))
        if not batch:
            continue

        result_buckets: dict[str, list[BuildResult]] = {}
        batch_by_key = {event.event_key: event for event in batch}
        for result in builder.build_batch(batch, attempts, rebuild_reasons):
            if result.event_key in batch_by_key:
                result_buckets.setdefault(result.event_key, []).append(result)
            else:
                unmatched_builder_audit_entries.append(
                    {
                        "candidate_type": "unmatched_builder_response",
                        "candidate_id": (
                            f"unmatched_builder:{len(unmatched_builder_audit_entries) + 1}"
                        ),
                        "builder_response": _build_response_to_dict(result),
                        "attempts": [],
                        "final_state": "rejected",
                        "final_reason_codes": ["unmatched_builder_response"],
                    }
                )

        for event in batch:
            if len(selector.accepted_items) >= config.max_items:
                break
            audit_entry = audit_by_event_identity[id(event)]
            previous_attempt = attempts.get(event.event_key, 0)
            candidates = result_buckets.get(event.event_key, [])
            if len(candidates) != 1:
                consumed_attempt = min(previous_attempt + 1, 2)
                attempts[event.event_key] = consumed_attempt
                _record_audit_attempt(
                    audit_entry,
                    generation_attempt=consumed_attempt,
                    reason_code="invalid_builder_response",
                    draft=None,
                    validation=None,
                    build_responses=candidates,
                )
                _retry_or_exclude(
                    event,
                    "invalid_builder_response",
                    consumed_attempt,
                    queue,
                    selector,
                )
                if consumed_attempt >= 2:
                    _finalize_audit(
                        audit_entry,
                        "rejected",
                        ("invalid_builder_response",),
                    )
                continue

            result = candidates[0]
            if (
                result.generation_attempt != previous_attempt + 1
                or result.generation_attempt not in {1, 2}
            ):
                _record_audit_attempt(
                    audit_entry,
                    generation_attempt=result.generation_attempt,
                    reason_code=result.reason_code or "invalid_builder_response",
                    draft=result.draft,
                    validation=None,
                )
                selector.reject(event.event_key, "invalid_builder_response")
                _finalize_audit(
                    audit_entry,
                    "rejected",
                    ("invalid_builder_response",),
                )
                continue
            attempts[event.event_key] = result.generation_attempt
            if result.draft is None:
                reason_code = result.reason_code or "invalid_builder_response"
                _record_audit_attempt(
                    audit_entry,
                    generation_attempt=result.generation_attempt,
                    reason_code=reason_code,
                    draft=None,
                    validation=None,
                )
                _retry_or_exclude(
                    event,
                    reason_code,
                    result.generation_attempt,
                    queue,
                    selector,
                )
                if result.generation_attempt >= 2:
                    _finalize_audit(
                        audit_entry,
                        "rejected",
                        (reason_code,),
                    )
                continue

            validation_kwargs = {"generation_attempt": result.generation_attempt}
            if now is not None:
                validation_kwargs["now"] = now
            validation = validator.validate(event, result.draft, **validation_kwargs)
            _record_audit_attempt(
                audit_entry,
                generation_attempt=result.generation_attempt,
                reason_code=result.reason_code,
                draft=result.draft,
                validation=validation,
            )
            if validation.action == "rebuild":
                if (
                    result.generation_attempt >= 2
                    or validation.rebuild_request is None
                    or validation.rebuild_request.event_key != event.event_key
                    or validation.rebuild_request.generation_attempt != 2
                ):
                    selector.reject(
                        event.event_key,
                        _first_reason(validation.reason_codes),
                    )
                    _finalize_audit(audit_entry, "rejected", validation.reason_codes)
                else:
                    rebuild_reasons[event.event_key] = validation.reason_codes
                    queue.appendleft(event)
                continue
            if validation.action == "reject":
                selector.reject(
                    event.event_key,
                    _first_reason(validation.reason_codes),
                )
                _finalize_audit(audit_entry, "rejected", validation.reason_codes)
                continue

            accepted = validation.validated_item
            if accepted is None or not _validated_item_matches_event(accepted, event):
                selector.reject(event.event_key, "invalid_builder_response")
                _finalize_audit(
                    audit_entry,
                    "rejected",
                    ("invalid_builder_response",),
                )
                continue
            if not selector.accept(accepted):
                reason_code = (
                    "x_limit"
                    if event.canonical_evidence.channel == "x"
                    else "selection_rejected"
                )
                _finalize_audit(audit_entry, "rejected", (reason_code,))
                continue

            _finalize_audit(audit_entry, "accepted", validation.reason_codes)

            if result.reason_code == "source_fallback_used":
                diagnostics["source_fallback_count"] += 1
            if accepted.validation_mode == "rules_only":
                diagnostics["rules_only_count"] += 1
            for reason in validation.reason_codes:
                if reason in _DEGRADATION_REASONS:
                    diagnostics[f"{reason}_count"] += 1
            if initial_positions[event.event_key] > config.max_items:
                diagnostics["reserve_fill_count"] += 1

    exclusions = Counter(selector.excluded_counts)
    exclusions.update(value.reason_code for value in quarantined_values)
    for audit_entry in audit_entries:
        if audit_entry["final_state"] == "not_evaluated":
            _finalize_audit(audit_entry, "not_selected", ("target_reached",))
    decision = decide_draft(
        selector.accepted_items,
        config,
        quarantined_keys=quarantined_keys,
        excluded_counts=exclusions,
    )
    return BriefPipelineResult(
        accepted_items=selector.accepted_items,
        decision=decision,
        exclusions=dict(exclusions),
        diagnostics=dict(diagnostics),
        audit_entries=tuple(
            audit_entries + quarantined_audit_entries + unmatched_builder_audit_entries
        ),
    )


def _finalize_audit(
    entry: dict[str, object],
    state: str,
    reason_codes: Sequence[str],
) -> None:
    entry["final_state"] = state
    entry["final_reason_codes"] = list(reason_codes)


def _record_audit_attempt(
    entry: dict[str, object],
    *,
    generation_attempt: int,
    reason_code: str | None,
    draft: object | None,
    validation: object | None,
    build_responses: Sequence[BuildResult] = (),
) -> None:
    attempts = entry["attempts"]
    assert isinstance(attempts, list)
    build: dict[str, object] = {
        "reason_code": reason_code,
        "draft": draft.to_dict() if draft is not None else None,
    }
    if build_responses:
        build["responses"] = [
            _build_response_to_dict(response)
            for response in build_responses
        ]
    attempts.append(
        {
            "generation_attempt": generation_attempt,
            "build": build,
            "validation": validation.to_dict() if validation is not None else None,
        }
    )


def _build_response_to_dict(response: BuildResult) -> dict[str, object]:
    return {
        "event_key": response.event_key,
        "generation_attempt": response.generation_attempt,
        "reason_code": response.reason_code,
        "draft": response.draft.to_dict() if response.draft is not None else None,
    }


def _retry_or_exclude(
    event: MergedEvent,
    reason: str,
    generation_attempt: int,
    queue: deque[MergedEvent],
    selector: BriefSelector,
) -> None:
    if generation_attempt < 2:
        queue.appendleft(event)
        return
    selector.reject(event.event_key, reason)


def _first_reason(reasons: Sequence[str]) -> str:
    return reasons[0] if reasons else "invalid_builder_response"


def _validated_item_matches_event(item: BriefItem, event: MergedEvent) -> bool:
    return (
        item.event_key == event.event_key
        and item.canonical_source == event.canonical_evidence
        and item.related_sources == event.related_evidence
        and item.published_at == event.canonical_evidence.published_at
    )
