"""Build JSON-safe metrics reports from v2 shadow workflow results."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from src.domain.models import WorkflowResult


_SCORE_BUCKETS = ("0-3.9", "4-6.9", "7-8.4", "8.5-10")
_RISK_LEVELS = ("low", "medium", "high")


def build_editorial_report(
    result: WorkflowResult,
    *,
    run_id: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Summarize collection, analysis, and editorial decisions for one run."""
    generated_at = generated_at or datetime.now(timezone.utc)
    candidates = {candidate.candidate_id: candidate for candidate in result.candidates}
    analyses = [_analysis_payload(analysis) for analysis in result.analyses]
    plan = result.editorial_plan
    decisions = [
        _decision_payload(decision, candidates.get(decision.candidate_id))
        for decision in (plan.decisions if plan else ())
    ]
    actions = Counter(decision["action"] for decision in decisions)
    reject_counts = Counter(
        decision["reason"] for decision in decisions if decision["action"] == "reject"
    )
    metrics = result.collection_diagnostics

    return {
        "schema_version": "shadow-run-v1",
        "run_id": run_id,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "workflow": {
            "state": result.state.value,
            "state_history": [state.value for state in result.state_history],
            "error": result.error,
        },
        "collection": {
            "fetched_total": metrics.fetched_total,
            "source_merge_removed": metrics.source_merge_removed,
            "filtered_total": metrics.filtered_total,
            "topic_cluster_removed": metrics.topic_cluster_removed,
            "final_editorial_dedup_removed": metrics.final_editorial_dedup_removed,
            "dedup_removed_total": metrics.dedup_removed_total,
            "returned_candidate_count": metrics.returned_candidate_count,
            "content_quality_distribution": _content_quality_distribution(result),
        },
        "candidates": [_candidate_payload(candidate) for candidate in result.candidates],
        "analysis": {
            "importance_score_distribution": _score_distribution(result, "importance_score"),
            "evidence_score_distribution": _score_distribution(result, "evidence_score"),
            "risk_level_distribution": _risk_distribution(result),
            "items": analyses,
        },
        "editorial": {
            "write_count": actions["write"],
            "reserve_count": actions["reserve"],
            "reject_count": actions["reject"],
            "selection_report": dict(plan.selection_report) if plan else {},
            "decisions": decisions,
            "reject_reasons": [
                {"reason": reason, "count": count}
                for reason, count in sorted(reject_counts.items())
            ],
        },
    }


def _score_distribution(result: WorkflowResult, field: str) -> dict[str, int]:
    counts = Counter(_score_bucket(float(getattr(analysis, field))) for analysis in result.analyses)
    return {bucket: counts[bucket] for bucket in _SCORE_BUCKETS}


def _risk_distribution(result: WorkflowResult) -> dict[str, int]:
    counts = Counter(str(analysis.risk_level) for analysis in result.analyses)
    return {risk: counts[risk] for risk in _RISK_LEVELS}


def _content_quality_distribution(result: WorkflowResult) -> dict[str, int]:
    counts = Counter(candidate.evidence.content_quality for candidate in result.candidates)
    return dict(sorted(counts.items()))


def _score_bucket(score: float) -> str:
    if score < 4.0:
        return "0-3.9"
    if score < 7.0:
        return "4-6.9"
    if score < 8.5:
        return "7-8.4"
    return "8.5-10"


def _analysis_payload(analysis: Any) -> dict[str, Any]:
    return {
        "candidate_id": analysis.candidate_id,
        "importance_score": analysis.importance_score,
        "evidence_score": analysis.evidence_score,
        "impact_score": analysis.impact_score,
        "risk_level": analysis.risk_level,
        "importance_reason": analysis.importance_reason,
        "verifiability_reason": analysis.verifiability_reason,
        "impact_analysis": analysis.impact_analysis,
    }


def _candidate_payload(candidate: Any) -> dict[str, Any]:
    evidence = candidate.evidence
    return {
        "candidate_id": candidate.candidate_id,
        "source_title": evidence.title,
        "source_summary": evidence.summary,
        "source_url": evidence.url,
        "source": evidence.source,
        "source_type": evidence.source_type,
        "source_tier": candidate.source_tier,
        "published_at": evidence.published_at.isoformat() if evidence.published_at else "",
        "content_quality": evidence.content_quality,
        "content_quality_reason": evidence.content_quality_reason,
        "evidence_details": dict(evidence.details),
    }


def _decision_payload(decision: Any, candidate: Any) -> dict[str, Any]:
    return {
        "candidate_id": decision.candidate_id,
        "title": candidate.evidence.title if candidate else "",
        "source": candidate.evidence.source if candidate else "",
        "action": decision.action.value,
        "rank": decision.rank,
        "reason": decision.reason,
        "audience": decision.brief.audience,
        "angle": decision.brief.angle,
        "title_direction": decision.brief.title_direction,
    }
