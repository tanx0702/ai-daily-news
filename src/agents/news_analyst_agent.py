"""Deterministic MVP analysis built from existing v1 editorial signals."""

from __future__ import annotations

from collections.abc import Mapping

from src.domain.models import NewsAnalysis, NewsCandidate


class NewsAnalystAgent:
    """Explain the value and evidence quality of candidates without new LLM calls."""

    def analyze(self, candidates: tuple[NewsCandidate, ...]) -> tuple[NewsAnalysis, ...]:
        return tuple(self._analyze_candidate(candidate) for candidate in candidates)

    @staticmethod
    def _analyze_candidate(candidate: NewsCandidate) -> NewsAnalysis:
        payload = candidate.legacy_payload
        editorial = _mapping(payload.get("_editorial"))
        metrics = _mapping(payload.get("metrics"))
        editorial_score = _score(editorial.get("score"), fallback=_score(payload.get("_score")) / 10)
        importance_score = _clamp(editorial_score)

        evidence_score = 8.0 if editorial.get("evidence_complete") else 4.0
        if candidate.source_tier in {"primary", "research"}:
            evidence_score += 0.5
        cross_source_count = _number(metrics.get("cross_source_count"))
        evidence_score += min(cross_source_count * 0.5, 1.0)
        evidence_score = _clamp(evidence_score)

        risk_level = _risk_level(payload, evidence_score)
        impact_score = _clamp(importance_score * 0.7 + evidence_score * 0.3)
        return NewsAnalysis(
            candidate_id=candidate.candidate_id,
            importance_score=importance_score,
            evidence_score=evidence_score,
            impact_score=impact_score,
            risk_level=risk_level,
            importance_reason=_importance_reason(importance_score),
            verifiability_reason=_verifiability_reason(evidence_score, cross_source_count),
            impact_analysis=_impact_analysis(impact_score, risk_level),
        )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _score(value: object, *, fallback: float = 0.0) -> float:
    return _number(value) if isinstance(value, (int, float)) else fallback


def _clamp(value: float) -> float:
    return round(max(0.0, min(value, 10.0)), 1)


def _risk_level(payload: Mapping[str, object], evidence_score: float) -> str:
    quality_gate = _mapping(payload.get("_quality_gate"))
    if payload.get("_publish_risk") or quality_gate.get("risk_level") == "high":
        return "high"
    if evidence_score < 6.0 or payload.get("_confidence_level") == "low":
        return "medium"
    return "low"


def _importance_reason(score: float) -> str:
    if score >= 8.0:
        return "既有编辑信号显示该事件具备高日报价值。"
    if score >= 5.0:
        return "既有编辑信号显示该事件值得作为备选关注。"
    return "既有编辑信号不足以支持优先报道。"


def _verifiability_reason(score: float, cross_source_count: float) -> str:
    if score >= 8.0:
        return f"来源证据完整，且有 {int(cross_source_count)} 个跨源佐证信号。"
    return "来源证据或跨源佐证不足，应保持保守表述。"


def _impact_analysis(score: float, risk_level: str) -> str:
    if risk_level == "high":
        return "该事件可能有读者价值，但证据或发布风险较高，需谨慎处理。"
    if score >= 8.0:
        return "高影响：适合优先面向关注 AI 产业与产品变化的读者报道。"
    return "中低影响：适合作为补充线索，不应压过证据更强的事件。"
