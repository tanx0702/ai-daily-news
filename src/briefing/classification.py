"""Deterministic content-type classification before content generation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re

from src.briefing.models import SourceEvidence
from src.briefing.publishability import (
    validate_content_source_publishability,
    validate_source_publishability,
    validate_update_source_publishability,
)


_FORMAL_EVENT_TYPES = {
    "release",
    "research",
    "funding",
    "acquisition",
    "partnership",
    "appointment",
    "departure",
    "organizational_change",
    "joining",
    "layoff",
    "policy",
    "infrastructure",
    "security",
    "open_source",
}
_UNVERIFIED_RUMOR = re.compile(
    r"\b(?:rumou?r|reportedly|sources? say|unconfirmed)\b|"
    r"据传|传闻|未经证实|消息人士称|爆料",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ContentClassification:
    content_type: str | None
    reason_codes: tuple[str, ...]
    subject_anchors: tuple[str, ...] = ()
    detail_anchors: tuple[str, ...] = ()


def classify_source_content(source: SourceEvidence) -> ContentClassification:
    """Freeze one source as a fact, update, opinion, or deterministic rejection."""
    combined = f"{source.source_title} {source.evidence_text}"
    if _UNVERIFIED_RUMOR.search(combined):
        return ContentClassification(None, ("unverified_rumor",))

    fact = validate_source_publishability(replace(source, content_type="fact_event"))
    if fact.accepted and fact.event_type in _FORMAL_EVENT_TYPES:
        return ContentClassification(
            "fact_event",
            ("classified_fact_event",),
            fact.subject_anchors,
        )

    if source.content_type == "attributed_opinion":
        opinion = validate_content_source_publishability(source)
        if opinion.accepted:
            return ContentClassification(
                "attributed_opinion",
                ("classified_attributed_opinion",),
            )

    update = validate_update_source_publishability(
        replace(source, content_type="ai_update")
    )
    if update.accepted:
        return ContentClassification(
            "ai_update",
            ("classified_ai_update",),
            update.subject_anchors,
            update.detail_anchors,
        )

    if fact.accepted:
        return ContentClassification(
            "fact_event",
            ("classified_fact_event_fallback",),
            fact.subject_anchors,
        )

    return ContentClassification(
        None,
        (
            fact.reason_codes
            if "non_news_content" in fact.reason_codes
            else update.reason_codes or fact.reason_codes or ("non_news_content",)
        ),
    )
