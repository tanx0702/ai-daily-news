"""Evidence-based editorial signals used before daily-news selection."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlparse


_ENTITY_ALIASES = {
    "tencent": ("腾讯", "tencent"),
    "openai": ("openai",),
    "google": ("google", "deepmind", "谷歌"),
    "microsoft": ("microsoft", "微软"),
    "apple": ("apple", "苹果"),
    "meta": ("meta",),
    "anthropic": ("anthropic",),
    "nvidia": ("nvidia", "英伟达"),
}

_EVENT_ALIASES = {
    "waic": ("waic", "世界人工智能大会"),
    "lawsuit": ("lawsuit", "sues", "sued", "起诉", "诉讼"),
    "funding": ("funding", "融资", "估值"),
    "conference": ("conference", "大会", "峰会"),
}

_TIER_SCORE = {
    "primary": 2.5,
    "research": 2.2,
    "media": 1.8,
    "community": 1.0,
}


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _has_http_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _combined_source_text(item: dict) -> str:
    return " ".join(
        str(item.get(field) or "")
        for field in ("source_title", "title", "source_summary", "summary")
    ).lower()


def _find_alias(text: str, aliases: dict[str, tuple[str, ...]]) -> str:
    for canonical, values in aliases.items():
        if any(value in text for value in values):
            return canonical
    return ""


def _fallback_event_key(item: dict) -> str:
    topic = str(item.get("topic_key") or item.get("_topic_key") or item.get("source_title") or item.get("title") or "")
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", topic.lower()).strip("-")
    return f"topic:{normalized[:80]}" if normalized else "topic:unknown"


def _event_identity(item: dict) -> tuple[str, str]:
    """Return a stable event type and event key without inferring new facts."""
    text = _combined_source_text(item)
    source_type = str(item.get("source_type") or "")
    metrics = item.get("metrics") or {}

    if source_type == "github":
        activity_type = str(metrics.get("github_activity_type") or "")
        repository = str(item.get("source_title") or item.get("title") or "").split(":", 1)[0].strip().lower()
        repository = re.sub(r"[^a-z0-9._/-]+", "-", repository).strip("-")
        if activity_type == "github_release" or metrics.get("github_release_url"):
            return "github_release", f"github-release:{repository or 'unknown'}"
        if activity_type == "new_repository":
            return "github_new_repository", f"github-new:{repository or 'unknown'}"
        return "github_activity", f"github-activity:{repository or 'unknown'}"

    entity = _find_alias(text, _ENTITY_ALIASES)
    event = _find_alias(text, _EVENT_ALIASES)
    if entity and event:
        return event, f"event:{entity}:{event}"
    if event:
        return event, f"event:{event}:{_fallback_event_key(item)[6:]}"
    return "news", _fallback_event_key(item)


def _freshness_score(published_at: object, now: datetime) -> float:
    timestamp = _as_datetime(published_at)
    if timestamp is None:
        return 0.0
    age_hours = max(0.0, (now - timestamp).total_seconds() / 3600)
    if age_hours <= 6:
        return 2.0
    if age_hours <= 24:
        return 1.8
    if age_hours <= 36:
        return 1.5
    if age_hours <= 72:
        return 0.8
    return 0.2


def _evidence_complete(item: dict) -> bool:
    return bool(
        str(item.get("source_title") or item.get("title") or "").strip()
        and str(item.get("source_summary") or "").strip()
        and _has_http_url(item.get("source_url") or item.get("url"))
        and _as_datetime(item.get("published_at"))
    )


def annotate_editorial_candidates(
    items: Iterable[dict],
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Attach deterministic editorial evidence, event, and quality signals.

    The score is a ranking aid for a human-reviewed draft, not a publishing
    gate. Missing evidence and unverified GitHub activity are deliberately
    capped so they cannot displace a well-sourced news event.
    """
    current = now or datetime.now(timezone.utc)
    annotated = list(items)

    for item in annotated:
        event_type, event_key = _event_identity(item)
        evidence_complete = _evidence_complete(item)
        tier = str(item.get("source_tier") or "media")
        reasons: list[str] = []

        evidence_score = 3.0 if evidence_complete else 0.8
        if not evidence_complete:
            reasons.append("incomplete_source_evidence")

        freshness_score = _freshness_score(item.get("published_at"), current)
        if freshness_score < 1.5:
            reasons.append("stale_or_missing_timestamp")

        tier_score = _TIER_SCORE.get(tier, 1.0)
        event_score = 2.0 if event_type in {"news", "lawsuit", "funding", "waic", "conference"} else 1.2
        score = evidence_score + freshness_score + tier_score + event_score

        if event_type == "github_activity":
            score = min(score, 7.4)
            reasons.append("github_activity_not_release")
        elif event_type == "github_new_repository":
            score = min(score, 8.4)
            reasons.append("github_new_repository")
        elif event_type == "github_release":
            reasons.append("github_release_evidence")

        item["_editorial"] = {
            "score": round(min(score, 10.0), 1),
            "event_key": event_key,
            "event_type": event_type,
            "evidence_complete": evidence_complete,
            "reasons": reasons,
        }

    return annotated


def assess_daily_edition(news_list: Iterable[dict], quality_report: dict | None) -> dict:
    """Build an explainable 0-10 editorial score for a human-reviewed draft."""
    items = list(news_list)
    quality_report = quality_report or {}
    if not items:
        return {
            "score": 0.0,
            "target": 9.0,
            "meets_target": False,
            "components": {},
            "reasons": ["no_publishable_items"],
        }

    editorials = [item.get("_editorial") or {} for item in items]
    item_average = sum(float(editorial.get("score", 0) or 0) for editorial in editorials) / len(items)
    item_component = round(item_average * 0.45, 2)

    evidence_ratio = sum(bool(editorial.get("evidence_complete")) for editorial in editorials) / len(items)
    evidence_component = round(evidence_ratio * 1.5, 2)

    event_keys = {
        str(editorial.get("event_key") or "")
        for editorial in editorials
        if editorial.get("event_key")
    }
    event_component = round((len(event_keys) / len(items)) * 1.0, 2)

    authoritative_count = sum(
        item.get("source_tier") in {"primary", "research"}
        for item in items
    )
    authority_target = max(2, len(items) / 3)
    primary_authority = min(1.5, authoritative_count / authority_target * 1.5)
    media_sources = {
        str(item.get("source") or "")
        for item in items
        if item.get("source_tier") == "media" and str(item.get("source") or "")
    }
    media_authority = min(1.5, len(media_sources) / 3 * 1.5)
    authority_component = round(max(primary_authority, media_authority), 2)

    issues = quality_report.get("issues", [])
    if not isinstance(issues, list):
        issues = []
    review_status = quality_report.get("llm_review_status", "skipped")
    risk_level = quality_report.get("risk_level", "medium")
    if quality_report.get("pass") and review_status == "passed" and risk_level == "low" and not issues:
        factual_component = 1.5
    elif quality_report.get("pass") and review_status == "passed" and risk_level != "high":
        factual_component = 0.7
    else:
        factual_component = 0.0

    score = round(
        min(
            10.0,
            item_component + evidence_component + event_component + authority_component + factual_component,
        ),
        1,
    )

    reasons: list[str] = []
    editorial_reasons = {
        reason
        for editorial in editorials
        for reason in editorial.get("reasons", [])
        if isinstance(reason, str)
    }
    if "github_activity_not_release" in editorial_reasons:
        reasons.append("github_activity_not_release")
    if evidence_ratio < 1:
        reasons.append("incomplete_source_evidence")
    if len(event_keys) < len(items):
        reasons.append("duplicate_event")
    if authority_component < 1.5:
        reasons.append("insufficient_authoritative_source_diversity")
    if issues:
        reasons.append("quality_gate_issues")
    if review_status != "passed":
        reasons.append("quality_review_not_passed")
    if score < 9:
        reasons.append("below_editorial_target")

    return {
        "score": score,
        "target": 9.0,
        "meets_target": score >= 9,
        "components": {
            "item_quality": item_component,
            "evidence": evidence_component,
            "event_diversity": event_component,
            "source_authority": authority_component,
            "factual_quality": factual_component,
        },
        "reasons": reasons,
    }
