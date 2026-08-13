"""Deterministic event clustering and ambiguous-duplicate quarantine."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import re
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence
from urllib.parse import urlparse, urlunparse

from src.briefing.config import BriefingConfig
from src.briefing.models import MergedEvent, QuarantinedEvent, SourceEvidence
from src.briefing.semantic import (
    EventDocument,
    deterministic_relationship,
    evidence_priority,
)
from src.briefing.semantic_reviewer import SemanticReview


_STOP_WORDS = {
    "a", "an", "the", "to", "for", "of", "and", "with", "its", "new",
    "begin", "begins", "makes", "make", "selected",
}


@dataclass(frozen=True, slots=True)
class ClusteredDuplicate:
    evidence: SourceEvidence
    duplicate_of: str
    relationship: str
    comparison_mode: str
    reason_code: str

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence": self.evidence.to_dict(),
            "duplicate_of": self.duplicate_of,
            "relationship": self.relationship,
            "comparison_mode": self.comparison_mode,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class ClusterResult:
    events: tuple[MergedEvent, ...]
    quarantined: tuple[QuarantinedEvent, ...]
    diagnostics: Mapping[str, int]
    merged_duplicates: tuple[ClusteredDuplicate, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "quarantined", tuple(self.quarantined))
        object.__setattr__(self, "merged_duplicates", tuple(self.merged_duplicates))
        object.__setattr__(
            self,
            "diagnostics",
            MappingProxyType({str(key): int(value) for key, value in self.diagnostics.items()}),
        )


class SemanticReviewerProtocol(Protocol):
    diagnostics: Mapping[str, int]

    def review(
        self,
        left: EventDocument,
        right: EventDocument,
    ) -> SemanticReview: ...


def _normalized_url(value: str) -> str:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not host:
        return ""
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    path = re.sub(r"/+", "/", parsed.path).rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), netloc, path, "", "", ""))


def _tokens(value: str) -> tuple[str, ...]:
    words = tuple(
        token
        for token in re.findall(r"[a-z0-9][a-z0-9.+-]*|[\u4e00-\u9fff]{2,}", value.lower())
        if token not in _STOP_WORDS
    )
    return words


def _event_key(evidence: SourceEvidence) -> str:
    signature = " ".join(_tokens(evidence.source_title))
    if not signature:
        signature = _normalized_url(evidence.url) or evidence.publisher_id
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    return f"event-{digest}"


class EventClusterer:
    """Cluster only confirmed duplicates and quarantine unresolved overlap."""

    def __init__(
        self,
        config: BriefingConfig | None = None,
        *,
        reviewer: SemanticReviewerProtocol | None = None,
    ) -> None:
        self.config = config or BriefingConfig()
        self.reviewer = reviewer

    def cluster(
        self,
        evidence: Sequence[SourceEvidence],
        editorial_scores: Mapping[str, float] | None = None,
    ) -> ClusterResult:
        reviewer_before = Counter(getattr(self.reviewer, "diagnostics", {}))
        scores = dict(editorial_scores or {})
        ordered = sorted(
            evidence,
            key=lambda value: evidence_priority(
                value,
                scores.get(value.url, 0.0),
            ),
        )
        event_rows: list[dict[str, object]] = []
        quarantined: list[QuarantinedEvent] = []
        merged_duplicates: list[ClusteredDuplicate] = []
        merged_count = 0
        semantic_comparison_count = 0
        semantic_duplicate_merged_count = 0
        semantic_duplicate_unresolved_count = 0

        for candidate in ordered:
            confirmed_matches: list[tuple[dict[str, object], str]] = []
            uncertain_matches: list[tuple[dict[str, object], str, str]] = []
            for row in event_rows:
                canonical = row["canonical"]
                assert isinstance(canonical, SourceEvidence)
                related = row["related"]
                assert isinstance(related, list)
                confirmed_mode: str | None = None
                uncertain_match: tuple[str, str] | None = None
                for existing in (canonical, *related):
                    semantic_comparison_count += 1
                    pair_relationship, comparison_mode = self._relationship(
                        candidate,
                        existing,
                    )
                    if pair_relationship == "confirmed":
                        confirmed_mode = comparison_mode
                    elif (
                        pair_relationship in {"ambiguous", "unresolved"}
                        and uncertain_match is None
                    ):
                        uncertain_match = (pair_relationship, comparison_mode)
                if confirmed_mode is not None:
                    confirmed_matches.append((row, confirmed_mode))
                elif uncertain_match is not None:
                    uncertain_matches.append((row, *uncertain_match))

            if uncertain_matches:
                matched_rows: list[dict[str, object]] = []
                for row in event_rows:
                    if any(value[0] is row for value in (*confirmed_matches, *uncertain_matches)):
                        matched_rows.append(row)
                keeper = matched_rows[0]
                keeper_key = str(keeper["event_key"])
                unresolved = any(
                    relationship == "unresolved"
                    for _row, relationship, _mode in uncertain_matches
                )
                reason_code = (
                    "semantic_duplicate_unresolved"
                    if unresolved
                    else "ambiguous_duplicate"
                )
                comparison_mode = uncertain_matches[0][2]
                for row in matched_rows[1:]:
                    canonical = row["canonical"]
                    related = row["related"]
                    assert isinstance(canonical, SourceEvidence)
                    assert isinstance(related, list)
                    row_evidence = (canonical, *related)
                    quarantined.extend(
                        QuarantinedEvent(
                            evidence=value,
                            duplicate_of=keeper_key,
                            reason_code=reason_code,
                            relationship="uncertain",
                            comparison_mode=comparison_mode,
                        )
                        for value in row_evidence
                    )
                    merged_count -= len(related)
                    semantic_duplicate_merged_count -= len(related)
                    merged_duplicates = [
                        value
                        for value in merged_duplicates
                        if value.evidence not in row_evidence
                    ]
                    event_rows.remove(row)
                quarantined.append(
                    QuarantinedEvent(
                        evidence=candidate,
                        duplicate_of=keeper_key,
                        reason_code=reason_code,
                        relationship="uncertain",
                        comparison_mode=comparison_mode,
                    )
                )
                if unresolved:
                    semantic_duplicate_unresolved_count += 1
                continue

            if not confirmed_matches:
                event_rows.append(
                    {
                        "event_key": _event_key(candidate),
                        "canonical": candidate,
                        "related": [],
                    }
                )
                continue

            matched_row, comparison_mode = confirmed_matches[0]
            event_key = str(matched_row["event_key"])
            related = matched_row["related"]
            assert isinstance(related, list)
            for other_row, other_mode in confirmed_matches[1:]:
                other_key = str(other_row["event_key"])
                other_canonical = other_row["canonical"]
                other_related = other_row["related"]
                assert isinstance(other_canonical, SourceEvidence)
                assert isinstance(other_related, list)
                related.extend((other_canonical, *other_related))
                merged_count += 1
                semantic_duplicate_merged_count += 1
                merged_duplicates.append(
                    ClusteredDuplicate(
                        evidence=other_canonical,
                        duplicate_of=event_key,
                        relationship="same_event",
                        comparison_mode=other_mode,
                        reason_code="semantic_duplicate",
                    )
                )
                merged_duplicates = [
                    ClusteredDuplicate(
                        evidence=value.evidence,
                        duplicate_of=event_key if value.duplicate_of == other_key else value.duplicate_of,
                        relationship=value.relationship,
                        comparison_mode=value.comparison_mode,
                        reason_code=value.reason_code,
                    )
                    for value in merged_duplicates
                ]
                event_rows.remove(other_row)
            related.append(candidate)
            merged_count += 1
            semantic_duplicate_merged_count += 1
            merged_duplicates.append(
                ClusteredDuplicate(
                    evidence=candidate,
                    duplicate_of=event_key,
                    relationship="same_event",
                    comparison_mode=comparison_mode,
                    reason_code="semantic_duplicate",
                )
            )

        events = tuple(
            MergedEvent(
                event_key=str(row["event_key"]),
                canonical_evidence=row["canonical"],
                related_evidence=tuple(row["related"]),
                editorial_score=float(
                    scores.get(
                        str(row["event_key"]),
                        scores.get(row["canonical"].url, 0.0),
                    )
                ),
                rank_reasons=("canonical_authority",),
            )
            for row in event_rows
        )
        diagnostics = {
            "candidate_count": len(evidence),
            "event_count": len(events),
            "merged_count": merged_count,
            "quarantined_count": len(quarantined),
            "semantic_comparison_count": semantic_comparison_count,
            "semantic_duplicate_merged_count": semantic_duplicate_merged_count,
            "semantic_duplicate_unresolved_count": (
                semantic_duplicate_unresolved_count
            ),
        }
        for name, count in getattr(self.reviewer, "diagnostics", {}).items():
            if isinstance(count, int) and not isinstance(count, bool):
                diagnostics[str(name)] = count - reviewer_before.get(str(name), 0)
        return ClusterResult(
            events=events,
            quarantined=tuple(quarantined),
            diagnostics=diagnostics,
            merged_duplicates=tuple(merged_duplicates),
        )

    def _relationship(
        self,
        left: SourceEvidence,
        right: SourceEvidence,
    ) -> tuple[str, str]:
        left_document = EventDocument.from_evidence(left)
        right_document = EventDocument.from_evidence(right)
        semantic = deterministic_relationship(
            left_document,
            right_document,
            window_hours=self.config.semantic_dedup_window_hours,
        )
        if semantic == "same_event":
            return "confirmed", "rules"
        if semantic == "review":
            if self.reviewer is None:
                return "unresolved", "rules"
            review = self.reviewer.review(left_document, right_document)
            if review.relationship == "same_event":
                return "confirmed", review.comparison_mode
            if review.relationship == "uncertain":
                return "unresolved", review.comparison_mode
            return "distinct", review.comparison_mode
        return "distinct", "rules"
