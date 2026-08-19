"""The single final draft decision and execution-result constructors."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from html import unescape
import re
from typing import Iterable, Mapping
from urllib.parse import urlparse

from src.briefing.config import BriefingConfig
from src.briefing.models import BriefItem, DraftDecision, DraftExecution


def decide_draft(
    items: Iterable[BriefItem],
    config: BriefingConfig,
    *,
    quarantined_keys: Iterable[str] = (),
    excluded_counts: Mapping[str, int] | None = None,
    semantic_dedup_complete: bool = True,
    semantic_conflict_keys: Iterable[str] = (),
) -> DraftDecision:
    """Return the sole create/block decision from validated immutable items."""
    values = list(items)
    reasons: list[str] = []
    invalid = any(not _valid_final_item(item) for item in values)
    valid_items = [item for item in values if isinstance(item, BriefItem)]
    event_keys = [item.event_key for item in valid_items]
    quarantined = {str(key) for key in quarantined_keys}
    semantic_conflicts = {str(key) for key in semantic_conflict_keys}
    source_counts = Counter(
        item.canonical_source.publisher_id for item in valid_items
    )
    x_count = sum(item.canonical_source.channel == "x" for item in valid_items)
    fact_count = sum(item.content_type == "fact_event" for item in valid_items)
    opinion_items = [
        item for item in valid_items if item.content_type == "attributed_opinion"
    ]
    opinion_count = len(opinion_items)
    opinion_authors = [item.opinion_author.strip().lower() for item in opinion_items]

    if len(values) < config.min_items:
        reasons.append("insufficient_items")
    if fact_count < config.min_fact_items:
        reasons.append("insufficient_fact_items")
    if opinion_count > config.max_opinion_items:
        reasons.append("opinion_limit")
    if not all(opinion_authors) or len(opinion_authors) != len(set(opinion_authors)):
        reasons.append("opinion_author_limit")
    if len(values) > config.max_items or invalid or x_count > config.max_x_items:
        reasons.append("invalid_final_item")
    if len(event_keys) != len(set(event_keys)) or any(
        key in quarantined for key in event_keys
    ) or not semantic_dedup_complete or semantic_conflicts:
        reasons.append("duplicate_event_remaining")

    return DraftDecision(
        action="block" if reasons else "create",
        selected_count=len(values),
        min_items=config.min_items,
        max_items=config.max_items,
        x_count=x_count,
        max_x_items=config.max_x_items,
        fact_count=fact_count,
        min_fact_items=config.min_fact_items,
        opinion_count=opinion_count,
        max_opinion_items=config.max_opinion_items,
        reasons=tuple(reasons),
        excluded_counts=excluded_counts,
        source_counts=source_counts,
    )


def _valid_final_item(value: object) -> bool:
    if not isinstance(value, BriefItem):
        return False
    source = value.canonical_source
    parsed_url = urlparse(source.url)
    try:
        published_at = datetime.fromisoformat(value.published_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        return False
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[。！？!?])\s*", value.brief.strip())
        if part.strip()
    ]
    valid_brief_shape = (
        value.brief_mode == "title_only" and not sentences
    ) or (
        value.brief_mode == "expanded"
        and 1 <= len(sentences) <= 2
        and all(_contains_chinese(sentence) for sentence in sentences)
    )
    if (
        not value.event_key.strip()
        or not value.chinese_title.strip()
        or not _contains_chinese(value.chinese_title)
        or not valid_brief_shape
        or not source.source_title.strip()
        or not source.evidence_text.strip()
        or parsed_url.scheme.lower() not in {"http", "https"}
        or not parsed_url.netloc
        or value.published_at != source.published_at
        or not value.evidence_bindings
    ):
        return False
    evidence = _quote_text(source.evidence_text)
    display_claims = [value.chinese_title, *sentences]
    display_claim_keys = {_claim_text(display_claim) for display_claim in display_claims}
    bindings_cover_display = all(
        any(
            _claim_text(binding.claim) == _claim_text(display_claim)
            for binding in value.evidence_bindings
        )
        for display_claim in display_claims
    )
    bindings_only_cover_display = all(
        _claim_text(binding.claim) in display_claim_keys
        for binding in value.evidence_bindings
    )
    return bindings_cover_display and bindings_only_cover_display and all(
        binding.claim.strip()
        and binding.source_quote.strip()
        and binding.source_url == source.url
        and _quote_text(binding.source_quote) in evidence
        for binding in value.evidence_bindings
    )


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)


def _quote_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value).replace("\xa0", " ")).strip()


def _claim_text(value: str) -> str:
    return re.sub(
        r"[\s，,。.!！?？:：;；、\"'“”‘’（）()【】\[\]]+",
        "",
        _quote_text(value).lower(),
    )


def _timestamp(now: datetime | None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("execution timestamps must include a timezone")
    return value.isoformat()


def blocked_execution(reason: str, *, now: datetime | None = None) -> DraftExecution:
    timestamp = _timestamp(now)
    return DraftExecution("blocked", reason, timestamp, timestamp)


def dry_run_execution(*, now: datetime | None = None) -> DraftExecution:
    timestamp = _timestamp(now)
    return DraftExecution("dry_run", None, timestamp, timestamp)


def draft_created_execution(
    media_id: str,
    *,
    now: datetime | None = None,
) -> DraftExecution:
    timestamp = _timestamp(now)
    return DraftExecution("draft_created", None, timestamp, timestamp, media_id)


def failed_execution(reason: str, *, now: datetime | None = None) -> DraftExecution:
    timestamp = _timestamp(now)
    return DraftExecution("failed", reason, timestamp, timestamp)
