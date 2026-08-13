"""Semantic duplicate handling for already validated brief items."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence, Protocol

from src.briefing.config import BriefingConfig
from src.briefing.models import BriefItem, MergedEvent
from src.briefing.semantic import (
    EventDocument,
    deterministic_relationship,
    evidence_priority,
)
from src.briefing.semantic_reviewer import SemanticReview


class SemanticReviewerProtocol(Protocol):
    diagnostics: Mapping[str, int]

    def review(self, left: EventDocument, right: EventDocument) -> SemanticReview: ...


@dataclass(frozen=True, slots=True)
class DeduplicationOutcome:
    accept_candidate: bool
    removed_event_keys: tuple[str, ...] = ()
    duplicate_of: str | None = None
    reason_code: str | None = None
    comparison_mode: str = "rules"


class AcceptedItemDeduplicator:
    """Compare validated items and retain only the strongest event report."""

    def __init__(
        self,
        config: BriefingConfig,
        *,
        reviewer: SemanticReviewerProtocol | None = None,
    ) -> None:
        self.config = config
        self.reviewer = reviewer
        self._reviewer_diagnostics_before = Counter(
            getattr(self.reviewer, "diagnostics", {})
        )
        self.diagnostics: Counter[str] = Counter(
            semantic_comparison_count=0,
            semantic_duplicate_merged_count=0,
            semantic_duplicate_removed_count=0,
            semantic_duplicate_unresolved_count=0,
        )

    def evaluate(
        self,
        candidate: BriefItem,
        accepted: Sequence[BriefItem],
    ) -> DeduplicationOutcome:
        candidate_document = EventDocument.from_brief(candidate)
        removed: list[str] = []
        comparison_mode = "rules"
        removal_reason: str | None = None
        for current in accepted:
            current_document = EventDocument.from_brief(current)
            self.diagnostics["semantic_comparison_count"] += 1
            relationship = deterministic_relationship(
                candidate_document,
                current_document,
                window_hours=self.config.semantic_dedup_window_hours,
            )
            if relationship == "review":
                if self.reviewer is None:
                    review = SemanticReview(
                        "uncertain", "rules", "semantic_llm_unavailable"
                    )
                else:
                    review = self.reviewer.review(candidate_document, current_document)
                comparison_mode = review.comparison_mode
                relationship = review.relationship
                if relationship == "uncertain":
                    self.diagnostics["semantic_duplicate_unresolved_count"] += 1
                    if self._priority(candidate) < self._priority(current):
                        removed.append(current.event_key)
                        removal_reason = "semantic_duplicate_unresolved"
                        self.diagnostics["semantic_duplicate_removed_count"] += 1
                        continue
                    self.diagnostics["semantic_duplicate_removed_count"] += 1
                    outcome = DeduplicationOutcome(
                        False,
                        tuple(removed),
                        current.event_key,
                        "semantic_duplicate_unresolved",
                        comparison_mode,
                    )
                    self._sync_reviewer_diagnostics()
                    return outcome
            if relationship != "same_event":
                continue
            if self._priority(candidate) < self._priority(current):
                removed.append(current.event_key)
                removal_reason = removal_reason or "semantic_duplicate"
                self.diagnostics["semantic_duplicate_merged_count"] += 1
                self.diagnostics["semantic_duplicate_removed_count"] += 1
                continue
            self.diagnostics["semantic_duplicate_removed_count"] += 1
            outcome = DeduplicationOutcome(
                False,
                tuple(removed),
                current.event_key,
                "semantic_duplicate",
                comparison_mode,
            )
            self._sync_reviewer_diagnostics()
            return outcome
        outcome = DeduplicationOutcome(
            True,
            tuple(removed),
            None,
            removal_reason,
            comparison_mode,
        )
        self._sync_reviewer_diagnostics()
        return outcome

    def can_replace_any(
        self,
        candidate: MergedEvent,
        accepted: Sequence[BriefItem],
    ) -> bool:
        """Return whether a stronger queued source could replace an accepted item."""
        candidate_document = EventDocument.from_evidence(candidate.canonical_evidence)
        candidate_priority = evidence_priority(candidate.canonical_evidence)
        for current in accepted:
            if candidate_priority >= self._priority(current):
                continue
            relationship = deterministic_relationship(
                candidate_document,
                EventDocument.from_brief(current),
                window_hours=self.config.semantic_dedup_window_hours,
            )
            if relationship != "distinct":
                return True
        return False

    @staticmethod
    def _priority(item: BriefItem) -> tuple[object, ...]:
        return evidence_priority(item.canonical_source)

    def _sync_reviewer_diagnostics(self) -> None:
        for name, count in getattr(self.reviewer, "diagnostics", {}).items():
            if isinstance(count, int) and not isinstance(count, bool):
                self.diagnostics[str(name)] = (
                    count - self._reviewer_diagnostics_before.get(str(name), 0)
                )
