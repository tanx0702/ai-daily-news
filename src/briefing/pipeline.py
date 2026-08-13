"""Side-effect-free build, validation, selection, and decision orchestration."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping, Protocol, Sequence

from src.briefing.builder import BuildResult
from src.briefing.clusterer import ClusteredDuplicate
from src.briefing.config import BriefingConfig
from src.briefing.deduplicator import AcceptedItemDeduplicator, DeduplicationOutcome
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


class BriefDeduplicatorProtocol(Protocol):
    diagnostics: Mapping[str, int]

    def evaluate(
        self,
        candidate: BriefItem,
        accepted: Sequence[BriefItem],
    ) -> DeduplicationOutcome: ...

    def can_replace_any(
        self,
        candidate: MergedEvent,
        accepted: Sequence[BriefItem],
    ) -> bool: ...


def _pop_next_event(
    queue: deque[MergedEvent],
    *,
    prefer_x: bool,
) -> MergedEvent:
    if prefer_x:
        for event in queue:
            if event.canonical_evidence.channel == "x":
                queue.remove(event)
                return event
    return queue.popleft()


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
    semantic_deduplicator: BriefDeduplicatorProtocol | None = None,
    clustered_duplicates: Iterable[ClusteredDuplicate] = (),
) -> BriefPipelineResult:
    """Build and fully validate events until the target or queue exhaustion."""
    event_values = tuple(events)
    quarantined_values = tuple(quarantined)
    clustered_duplicate_values = tuple(clustered_duplicates)
    quarantined_urls = {value.evidence.url for value in quarantined_values}
    quarantine_reasons_by_url: dict[str, list[str]] = {}
    quarantine_details_by_url: dict[str, QuarantinedEvent] = {}
    for value in quarantined_values:
        quarantine_reasons_by_url.setdefault(value.evidence.url, []).append(
            value.reason_code
        )
        quarantine_details_by_url.setdefault(value.evidence.url, value)
    quarantined_keys = tuple(
        event.event_key
        for event in event_values
        if event.canonical_evidence.url in quarantined_urls
    )
    selector = BriefSelector(event_values, config, quarantined_keys=quarantined_keys)
    semantic_deduplicator = semantic_deduplicator or AcceptedItemDeduplicator(
        config
    )
    initial_order = tuple(event.event_key for event in selector.pending())
    initial_positions = {key: index for index, key in enumerate(initial_order, 1)}
    queue = deque(selector.pending())
    retry_queue: deque[MergedEvent] = deque()
    deferred_x: deque[MergedEvent] = deque()
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
            "duplicate_of": value.duplicate_of,
            "relationship": value.relationship,
            "comparison_mode": value.comparison_mode,
            "attempts": [],
            "final_state": "quarantined",
            "final_reason_codes": [value.reason_code],
        }
        for position, value in enumerate(quarantined_values, 1)
    ]
    clustered_duplicate_audit_entries = [
        {
            "candidate_type": "clustered_duplicate",
            "candidate_id": f"clustered_duplicate:{position}",
            "evidence": value.evidence.to_dict(),
            "duplicate_of": value.duplicate_of,
            "relationship": value.relationship,
            "comparison_mode": value.comparison_mode,
            "attempts": [],
            "final_state": "merged",
            "final_reason_codes": [value.reason_code],
        }
        for position, value in enumerate(clustered_duplicate_values, 1)
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
            quarantine = quarantine_details_by_url[event.canonical_evidence.url]
            audit_entry["duplicate_of"] = quarantine.duplicate_of
            audit_entry["relationship"] = quarantine.relationship
            audit_entry["comparison_mode"] = quarantine.comparison_mode
        elif event.canonical_evidence.channel == "x" and config.max_x_items == 0:
            _finalize_audit(audit_entry, "rejected", ("x_limit",))
    diagnostics: Counter[str] = Counter(
        reserve_fill_count=0,
        source_fallback_count=0,
        rules_only_count=0,
        rules_and_llm_count=0,
        build_attempt_count=0,
    )

    semantic_dedup_complete = True
    semantic_conflict_keys: set[str] = set()
    source_fallback_event_keys: set[str] = set()
    while retry_queue or queue:
        batch: list[MergedEvent] = []
        remaining_slots = max(config.max_items - len(selector.accepted_items), 0)
        remaining_x_slots = max(config.max_x_items - selector.x_count, 0)
        batched_x_count = 0
        single_x_retry = bool(
            retry_queue
            and retry_queue[0].canonical_evidence.channel == "x"
        )
        batch_limit = 1 if single_x_retry else (
            config.builder_batch_size if remaining_slots else 1
        )
        batch_limit = min(batch_limit, max(remaining_slots, 1))
        while (retry_queue or queue) and len(batch) < batch_limit:
            if (
                batch
                and retry_queue
                and retry_queue[0].canonical_evidence.channel == "x"
            ):
                break
            event = retry_queue.popleft() if retry_queue else _pop_next_event(
                queue,
                prefer_x=(
                    selector.x_count + batched_x_count < config.target_x_items
                ),
            )
            audit_entry = audit_by_event_identity[id(event)]
            if not selector.can_attempt(event):
                if event.canonical_evidence.channel == "x":
                    deferred_x.append(event)
                continue
            if (
                event.canonical_evidence.channel == "x"
                and batched_x_count >= remaining_x_slots
            ):
                deferred_x.append(event)
                continue
            if remaining_slots or semantic_deduplicator.can_replace_any(
                event,
                selector.accepted_items,
            ):
                batch.append(event)
                if event.canonical_evidence.channel == "x":
                    batched_x_count += 1
            else:
                _finalize_audit(audit_entry, "not_selected", ("target_reached",))
        if not batch:
            continue

        diagnostics["build_attempt_count"] += len(batch)
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
                if consumed_attempt < 2:
                    rebuild_reasons[event.event_key] = ("invalid_builder_response",)
                _retry_or_exclude(
                    event,
                    "invalid_builder_response",
                    consumed_attempt,
                    retry_queue,
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
                    source_fallback_used=result.source_fallback_used,
                )
                selector.reject(event.event_key, "invalid_builder_response")
                _finalize_audit(
                    audit_entry,
                    "rejected",
                    ("invalid_builder_response",),
                )
                continue
            attempts[event.event_key] = result.generation_attempt
            if result.source_fallback_used:
                source_fallback_event_keys.add(event.event_key)
            if result.draft is None:
                reason_code = result.reason_code or "invalid_builder_response"
                _record_audit_attempt(
                    audit_entry,
                    generation_attempt=result.generation_attempt,
                    reason_code=reason_code,
                    draft=None,
                    validation=None,
                    source_fallback_used=result.source_fallback_used,
                )
                if result.generation_attempt < 2:
                    rebuild_reasons[event.event_key] = (reason_code,)
                _retry_or_exclude(
                    event,
                    reason_code,
                    result.generation_attempt,
                    retry_queue,
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
                source_fallback_used=result.source_fallback_used,
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
                    retry_queue.append(event)
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
            deduplication = semantic_deduplicator.evaluate(
                accepted,
                selector.accepted_items,
            )
            if deduplication.removed_event_keys and not deduplication.accept_candidate:
                removed_reason = deduplication.reason_code or "semantic_duplicate"
                if not selector.remove_accepted(
                    remove_event_keys=deduplication.removed_event_keys,
                    reason_code=removed_reason,
                ):
                    semantic_dedup_complete = False
                    semantic_conflict_keys.update(deduplication.removed_event_keys)
                    semantic_conflict_keys.add(event.event_key)
                else:
                    _mark_removed_audits(
                        audit_entries,
                        deduplication.removed_event_keys,
                        duplicate_of=event.event_key,
                        reason_code=removed_reason,
                        comparison_mode=deduplication.comparison_mode,
                    )
            if not deduplication.accept_candidate:
                selector.reject(
                    event.event_key,
                    deduplication.reason_code or "semantic_duplicate",
                )
                _finalize_audit(
                    audit_entry,
                    "rejected",
                    (deduplication.reason_code or "semantic_duplicate",),
                )
                audit_entry["duplicate_of"] = deduplication.duplicate_of
                audit_entry["relationship"] = "uncertain" if (
                    deduplication.reason_code == "semantic_duplicate_unresolved"
                ) else "same_event"
                audit_entry["comparison_mode"] = deduplication.comparison_mode
                if deferred_x and selector.x_count < config.max_x_items:
                    queue.extendleft(reversed(deferred_x))
                    deferred_x.clear()
                continue
            if deduplication.removed_event_keys:
                if not selector.replace_accepted(
                    accepted,
                    remove_event_keys=deduplication.removed_event_keys,
                    reason_code=deduplication.reason_code or "semantic_duplicate",
                ):
                    semantic_dedup_complete = False
                    semantic_conflict_keys.update(deduplication.removed_event_keys)
                    semantic_conflict_keys.add(event.event_key)
                    selector.reject(event.event_key, "selection_rejected")
                    _finalize_audit(audit_entry, "rejected", ("selection_rejected",))
                    continue
                _mark_removed_audits(
                    audit_entries,
                    deduplication.removed_event_keys,
                    duplicate_of=event.event_key,
                    reason_code=deduplication.reason_code or "semantic_duplicate",
                    comparison_mode=deduplication.comparison_mode,
                )
            elif len(selector.accepted_items) >= config.max_items:
                _finalize_audit(audit_entry, "not_selected", ("target_reached",))
                continue
            elif not selector.accept(accepted):
                reason_code = (
                    "x_limit"
                    if event.canonical_evidence.channel == "x"
                    else "selection_rejected"
                )
                if reason_code == "x_limit":
                    deferred_x.append(event)
                else:
                    _finalize_audit(audit_entry, "rejected", (reason_code,))
                continue

            _finalize_audit(audit_entry, "accepted", validation.reason_codes)

            if accepted.validation_mode == "rules_only":
                diagnostics["rules_only_count"] += 1
            else:
                diagnostics["rules_and_llm_count"] += 1
            for reason in validation.reason_codes:
                if reason in _DEGRADATION_REASONS:
                    diagnostics[f"{reason}_count"] += 1
            if initial_positions[event.event_key] > config.max_items:
                diagnostics["reserve_fill_count"] += 1
            if deferred_x and selector.x_count < config.max_x_items:
                queue.extendleft(reversed(deferred_x))
                deferred_x.clear()

        if deferred_x and selector.x_count < config.max_x_items:
            queue.extendleft(reversed(deferred_x))
            deferred_x.clear()

    for event in deferred_x:
        selector.reject(event.event_key, "x_limit")
        _finalize_audit(
            audit_by_event_identity[id(event)],
            "rejected",
            ("x_limit",),
        )
    exclusions = Counter(selector.excluded_counts)
    exclusions.update(value.reason_code for value in quarantined_values)
    for audit_entry in audit_entries:
        if audit_entry["final_state"] == "not_evaluated":
            _finalize_audit(audit_entry, "not_selected", ("target_reached",))
    diagnostics["rules_only_count"] = sum(
        item.validation_mode == "rules_only" for item in selector.accepted_items
    )
    diagnostics["source_fallback_count"] = len(source_fallback_event_keys)
    diagnostics["rules_and_llm_count"] = sum(
        item.validation_mode == "rules_and_llm" for item in selector.accepted_items
    )
    decision = decide_draft(
        selector.accepted_items,
        config,
        quarantined_keys=quarantined_keys,
        excluded_counts=exclusions,
        semantic_dedup_complete=semantic_dedup_complete,
        semantic_conflict_keys=semantic_conflict_keys,
    )
    for component in (builder, validator):
        component_diagnostics = getattr(component, "diagnostics", {})
        if isinstance(component_diagnostics, Mapping):
            for name, count in component_diagnostics.items():
                if isinstance(count, int) and not isinstance(count, bool):
                    diagnostics[str(name)] = count
    for name, count in getattr(semantic_deduplicator, "diagnostics", {}).items():
        if isinstance(count, int) and not isinstance(count, bool):
            diagnostics[str(name)] = count
    return BriefPipelineResult(
        accepted_items=selector.accepted_items,
        decision=decision,
        exclusions=dict(exclusions),
        diagnostics=dict(diagnostics),
        audit_entries=tuple(
            audit_entries
            + clustered_duplicate_audit_entries
            + quarantined_audit_entries
            + unmatched_builder_audit_entries
        ),
    )


def _mark_removed_audits(
    audit_entries: Sequence[dict[str, object]],
    removed_event_keys: Sequence[str],
    *,
    duplicate_of: str,
    reason_code: str,
    comparison_mode: str,
) -> None:
    removed = set(removed_event_keys)
    for entry in audit_entries:
        event = entry.get("event", {})
        if not isinstance(event, Mapping) or event.get("event_key") not in removed:
            continue
        _finalize_audit(entry, "rejected", (reason_code,))
        entry["duplicate_of"] = duplicate_of
        entry["relationship"] = (
            "uncertain"
            if reason_code == "semantic_duplicate_unresolved"
            else "same_event"
        )
        entry["comparison_mode"] = comparison_mode


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
    source_fallback_used: bool = False,
) -> None:
    attempts = entry["attempts"]
    assert isinstance(attempts, list)
    build: dict[str, object] = {
        "reason_code": reason_code,
        "source_fallback_used": source_fallback_used,
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
        "source_fallback_used": response.source_fallback_used,
        "draft": response.draft.to_dict() if response.draft is not None else None,
    }


def _retry_or_exclude(
    event: MergedEvent,
    reason: str,
    generation_attempt: int,
    retry_queue: deque[MergedEvent],
    selector: BriefSelector,
) -> None:
    if generation_attempt < 2:
        retry_queue.append(event)
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
