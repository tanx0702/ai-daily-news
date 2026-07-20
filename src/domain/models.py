"""Typed contracts shared by v2 agents without changing v1 payloads."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from src.domain.states import WorkflowState


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    """Source-bound facts that downstream agents may use."""

    title: str
    summary: str
    url: str
    source: str
    source_type: str
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class NewsCandidate:
    """A v2 view over an existing v1 candidate and its evidence."""

    candidate_id: str
    evidence: SourceEvidence
    source_tier: str
    legacy_payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CollectionDiagnostics:
    """Collection and deduplication counts captured for one shadow run."""

    fetched_total: int = 0
    source_merge_removed: int = 0
    filtered_total: int = 0
    topic_cluster_removed: int = 0
    final_editorial_dedup_removed: int = 0
    returned_candidate_count: int = 0

    @property
    def dedup_removed_total(self) -> int:
        return (
            self.source_merge_removed
            + self.topic_cluster_removed
            + self.final_editorial_dedup_removed
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CollectionDiagnostics":
        values = {
            field: data.get(field, 0)
            for field in (
                "fetched_total",
                "source_merge_removed",
                "filtered_total",
                "topic_cluster_removed",
                "final_editorial_dedup_removed",
                "returned_candidate_count",
            )
        }
        return cls(**{name: int(value) if isinstance(value, (int, float)) else 0 for name, value in values.items()})


@dataclass(frozen=True, slots=True)
class NewsAnalysis:
    """Explainable importance, evidence, and impact signals for one candidate."""

    candidate_id: str
    importance_score: float
    evidence_score: float
    impact_score: float
    risk_level: str
    importance_reason: str
    verifiability_reason: str
    impact_analysis: str


class EditorialAction(str, Enum):
    """The only editorial outcomes available in the MVP."""

    WRITE = "write"
    RESERVE = "reserve"
    REJECT = "reject"


class FeedbackLabel(str, Enum):
    """Human validation labels accepted by the MVP CLI."""

    GOOD_TOPIC = "good_topic"
    BAD_TOPIC = "bad_topic"
    DUPLICATE = "duplicate"
    NOT_INTERESTING = "not_interesting"


@dataclass(frozen=True, slots=True)
class EditorialBrief:
    """The writing direction selected before any article is generated."""

    audience: str
    angle: str
    title_direction: str


@dataclass(frozen=True, slots=True)
class EditorialDecision:
    """A transparent selection outcome for one candidate."""

    candidate_id: str
    action: EditorialAction
    rank: int | None
    brief: EditorialBrief
    reason: str


@dataclass(frozen=True, slots=True)
class EditorialPlan:
    """The final selection output for one shadow edition."""

    decisions: tuple[EditorialDecision, ...]
    selection_report: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    """Inspectable outcome of a side-effect-free MVP editorial run."""

    state: WorkflowState
    state_history: tuple[WorkflowState, ...]
    candidates: tuple[NewsCandidate, ...]
    analyses: tuple[NewsAnalysis, ...]
    editorial_plan: EditorialPlan | None
    collection_diagnostics: CollectionDiagnostics = CollectionDiagnostics()
    error: str = ""
