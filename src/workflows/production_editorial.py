"""Safely apply existing v2 editorial decisions to one v1 production run.

This adapter deliberately has no collection, summarization, rendering, storage,
or publishing side effects.  It only reorders candidates that the current v1
pipeline has already collected and annotated.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

from src.agents.collector_agent import CollectorAgent
from src.agents.editorial_agent import EditorialAgent
from src.agents.news_analyst_agent import NewsAnalystAgent
from src.domain.models import EditorialAction, NewsAnalysis, NewsCandidate


@dataclass(frozen=True, slots=True)
class ProductionEditorialResult:
    """The final production lists plus a private diagnostic record."""

    selected: list[dict]
    reserves: list[dict]
    report: Mapping[str, Any]


def run_production_editorial(
    *,
    mode: str,
    all_candidates: list[dict],
    v1_selected: list[dict],
    v1_reserves: list[dict],
    target_count: int,
    max_items_per_source: int = 2,
    max_items_per_topic: int = 2,
    min_primary_or_research: int = 2,
    collector: Any | None = None,
    analyst: Any | None = None,
    editorial: Any | None = None,
) -> ProductionEditorialResult:
    """Return a v2-assisted selection or the original v1 lists on any risk."""
    report = _base_report(mode, v1_selected, v1_reserves)
    if mode != "v2_assist":
        report["mode"] = "v1"
        report["status"] = "v1"
        report["selected_ids"] = _item_ids(v1_selected)
        report["reserve_ids"] = _item_ids(v1_reserves)
        return ProductionEditorialResult(v1_selected, v1_reserves, report)

    try:
        adapter = collector or CollectorAgent()
        typed_candidates = adapter.adapt_existing(all_candidates)
        analysis_agent = analyst or NewsAnalystAgent()
        analyses = analysis_agent.analyze(typed_candidates)
        report["analysis"] = _analysis_diagnostics(typed_candidates, analyses)

        editorial_agent = editorial or EditorialAgent()
        plan = editorial_agent.select(
            typed_candidates,
            analyses,
            target_count=target_count,
            max_items_per_source=max_items_per_source,
            max_items_per_topic=max_items_per_topic,
            min_primary_or_research=min_primary_or_research,
        )
        candidate_by_id = _candidate_mapping(typed_candidates, all_candidates)
        decisions = tuple(plan.decisions)
        write_ids = _decision_ids(decisions, EditorialAction.WRITE)
        reserve_ids = _decision_ids(decisions, EditorialAction.RESERVE)
        report["v2_write_ids"] = write_ids
        report["v2_reserve_ids"] = reserve_ids

        selected_ids = [
            candidate_id
            for candidate_id in write_ids
            if candidate_by_id[candidate_id]["candidate"].evidence.content_quality == "ready"
        ]
        if len(selected_ids) < target_count:
            return _fallback(
                report,
                v1_selected,
                v1_reserves,
                "insufficient_ready_write_candidates",
            )

        selected = [candidate_by_id[candidate_id]["item"] for candidate_id in selected_ids]
        reserves = [
            candidate_by_id[candidate_id]["item"]
            for candidate_id in reserve_ids
            if candidate_id not in selected_ids
        ]
        report.update(
            {
                "status": "applied",
                "selected_ids": selected_ids,
                "reserve_ids": _item_ids(reserves),
                "dropped_v1_selected_ids": _ordered_difference(_item_ids(v1_selected), selected_ids),
                "added_v2_selected_ids": _ordered_difference(selected_ids, _item_ids(v1_selected)),
                "selection_report": dict(plan.selection_report),
            }
        )
        return ProductionEditorialResult(selected, reserves, report)
    except Exception as exc:  # Production assist must never block the established v1 edition.
        return _fallback(
            report,
            v1_selected,
            v1_reserves,
            f"agent_error:{type(exc).__name__}",
        )


def _base_report(mode: str, v1_selected: list[dict], v1_reserves: list[dict]) -> dict[str, Any]:
    return {
        "mode": mode,
        "status": "pending",
        "fallback_reason": "",
        "v1_selected_ids": _item_ids(v1_selected),
        "v1_reserve_ids": _item_ids(v1_reserves),
        "v2_write_ids": [],
        "v2_reserve_ids": [],
        "selected_ids": [],
        "reserve_ids": [],
        "dropped_v1_selected_ids": [],
        "added_v2_selected_ids": [],
        "analysis": {
            "risk_level_distribution": {},
            "content_quality_distribution": {},
        },
    }


def _fallback(
    report: dict[str, Any],
    v1_selected: list[dict],
    v1_reserves: list[dict],
    reason: str,
) -> ProductionEditorialResult:
    report.update(
        {
            "status": "fallback",
            "fallback_reason": reason,
            "selected_ids": _item_ids(v1_selected),
            "reserve_ids": _item_ids(v1_reserves),
        }
    )
    return ProductionEditorialResult(v1_selected, v1_reserves, report)


def _candidate_mapping(
    candidates: tuple[NewsCandidate, ...],
    items: list[dict],
) -> dict[str, dict[str, Any]]:
    item_by_id = {
        _item_id(item, index): item
        for index, item in enumerate(items, start=1)
    }
    mapping: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        item = item_by_id.get(candidate.candidate_id)
        if item is None:
            raise KeyError(f"candidate {candidate.candidate_id} does not map to a production item")
        if candidate.candidate_id in mapping:
            raise ValueError(f"duplicate candidate id: {candidate.candidate_id}")
        mapping[candidate.candidate_id] = {"candidate": candidate, "item": item}
    return mapping


def _decision_ids(decisions: tuple[Any, ...], action: EditorialAction) -> list[str]:
    ranked = [decision for decision in decisions if decision.action is action]
    ranked.sort(key=lambda decision: (decision.rank is None, decision.rank or 0))
    return [decision.candidate_id for decision in ranked]


def _analysis_diagnostics(
    candidates: tuple[NewsCandidate, ...],
    analyses: tuple[NewsAnalysis, ...],
) -> dict[str, dict[str, int]]:
    risk_counts = Counter(str(analysis.risk_level) for analysis in analyses)
    content_counts = Counter(candidate.evidence.content_quality for candidate in candidates)
    return {
        "risk_level_distribution": dict(sorted(risk_counts.items())),
        "content_quality_distribution": dict(sorted(content_counts.items())),
    }


def _item_ids(items: list[dict]) -> list[str]:
    return [_item_id(item, index) for index, item in enumerate(items, start=1)]


def _item_id(item: dict, index: int) -> str:
    return str(item.get("id") or item.get("url") or f"candidate-{index}")


def _ordered_difference(items: list[str], excluded: list[str]) -> list[str]:
    excluded_ids = set(excluded)
    return [item for item in items if item not in excluded_ids]


__all__ = ["ProductionEditorialResult", "run_production_editorial"]
