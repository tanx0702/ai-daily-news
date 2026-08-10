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
            if selector.can_attempt(event):
                batch.append(event)
            elif event.canonical_evidence.channel == "x":
                selector.reject(event.event_key, "x_limit")
        if not batch:
            continue

        result_buckets: dict[str, list[BuildResult]] = {}
        for result in builder.build_batch(batch, attempts, rebuild_reasons):
            result_buckets.setdefault(result.event_key, []).append(result)

        for event in batch:
            if len(selector.accepted_items) >= config.max_items:
                break
            previous_attempt = attempts.get(event.event_key, 0)
            candidates = result_buckets.get(event.event_key, [])
            if len(candidates) != 1:
                consumed_attempt = min(previous_attempt + 1, 2)
                attempts[event.event_key] = consumed_attempt
                _retry_or_exclude(
                    event,
                    "invalid_builder_response",
                    consumed_attempt,
                    queue,
                    selector,
                )
                continue

            result = candidates[0]
            if (
                result.generation_attempt != previous_attempt + 1
                or result.generation_attempt not in {1, 2}
            ):
                selector.reject(event.event_key, "invalid_builder_response")
                continue
            attempts[event.event_key] = result.generation_attempt
            if result.draft is None:
                _retry_or_exclude(
                    event,
                    result.reason_code or "invalid_builder_response",
                    result.generation_attempt,
                    queue,
                    selector,
                )
                continue

            validation_kwargs = {"generation_attempt": result.generation_attempt}
            if now is not None:
                validation_kwargs["now"] = now
            validation = validator.validate(event, result.draft, **validation_kwargs)
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
                else:
                    rebuild_reasons[event.event_key] = validation.reason_codes
                    queue.appendleft(event)
                continue
            if validation.action == "reject":
                selector.reject(
                    event.event_key,
                    _first_reason(validation.reason_codes),
                )
                continue

            accepted = validation.validated_item
            if accepted is None or not _validated_item_matches_event(accepted, event):
                selector.reject(event.event_key, "invalid_builder_response")
                continue
            if not selector.accept(accepted):
                continue

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
    )


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
