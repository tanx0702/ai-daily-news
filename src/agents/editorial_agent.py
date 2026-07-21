"""Turn analyzed candidates into explainable editorial decisions."""

from __future__ import annotations

from collections.abc import Callable

from src.domain.models import (
    EditorialAction,
    EditorialBrief,
    EditorialDecision,
    EditorialPlan,
    NewsAnalysis,
    NewsCandidate,
)


class EditorialAgent:
    """Reuse the v1 selection policy while publishing typed writing briefs."""

    def __init__(self, *, selector: Callable[..., tuple[list[dict], list[dict], dict]] | None = None) -> None:
        if selector is None:
            from src.editorial_selection import select_editorial_candidates

            selector = select_editorial_candidates
        self._selector = selector

    def select(
        self,
        candidates: tuple[NewsCandidate, ...],
        analyses: tuple[NewsAnalysis, ...],
        *,
        target_count: int = 10,
        max_items_per_source: int = 2,
        max_items_per_topic: int = 2,
        min_primary_or_research: int = 2,
    ) -> EditorialPlan:
        """Produce write, reserve, and reject decisions without writing content."""
        analysis_by_id = {analysis.candidate_id: analysis for analysis in analyses}
        eligible = [
            candidate
            for candidate in candidates
            if analysis_by_id.get(candidate.candidate_id, _unknown_analysis(candidate)).risk_level != "high"
        ]
        selected, reserves, report = self._selector(
            [candidate.legacy_payload for candidate in eligible],
            target_count=target_count,
            max_items_per_source=max_items_per_source,
            max_items_per_topic=max_items_per_topic,
            min_primary_or_research=min_primary_or_research,
        )
        selected_payload_ids = {id(item) for item in selected}
        reserve_payload_ids = {id(item) for item in reserves}
        selected_rank = {id(item): index for index, item in enumerate(selected, start=1)}

        decisions: list[EditorialDecision] = []
        for candidate in candidates:
            analysis = analysis_by_id.get(candidate.candidate_id, _unknown_analysis(candidate))
            payload_id = id(candidate.legacy_payload)
            if analysis.risk_level == "high":
                action = EditorialAction.REJECT
                rank = None
                reason = "分析阶段标记为高风险，不能进入自动写作。"
            elif payload_id in selected_payload_ids:
                action = EditorialAction.WRITE
                rank = selected_rank[payload_id]
                reason = analysis.importance_reason
            elif payload_id in reserve_payload_ids:
                action = EditorialAction.RESERVE
                rank = None
                reason = "符合候选条件，但未进入当期正选。"
            else:
                action = EditorialAction.REJECT
                rank = None
                reason = "未通过当前编辑配额与选题规则。"
            decisions.append(
                EditorialDecision(
                    candidate_id=candidate.candidate_id,
                    action=action,
                    rank=rank,
                    brief=_brief_for(candidate),
                    reason=reason,
                )
            )
        return EditorialPlan(decisions=tuple(decisions), selection_report=report)


def _unknown_analysis(candidate: NewsCandidate) -> NewsAnalysis:
    return NewsAnalysis(
        candidate_id=candidate.candidate_id,
        importance_score=0.0,
        evidence_score=0.0,
        impact_score=0.0,
        risk_level="high",
        importance_reason="缺少分析结果。",
        verifiability_reason="缺少分析结果。",
        impact_analysis="缺少分析结果。",
    )


def _brief_for(candidate: NewsCandidate) -> EditorialBrief:
    if candidate.evidence.source_type == "arxiv":
        return EditorialBrief(
            audience="关注 AI 技术进展的读者",
            angle="说明论文提出的问题、方法边界与潜在应用，不夸大研究结论。",
            title_direction="研究解读型，突出已知问题与方法，不写成产品发布。",
        )
    if candidate.evidence.source_type in {"github", "huggingface"}:
        return EditorialBrief(
            audience="关注 AI 工具和开源生态的读者",
            angle="先解释项目用途，再说明可确认的版本或社区信号。",
            title_direction="工具价值型，避免将社区活跃表述为公司官宣。",
        )
    return EditorialBrief(
        audience="关注 AI 产业与产品变化的读者",
        angle="先交代可确认的事实，再解释对读者的实际影响或不确定性。",
        title_direction="事实型标题，突出事件主体和已确认动作，避免夸张措辞。",
    )


__all__ = ["EditorialAction", "EditorialAgent"]
