"""Normalize collector candidates into source-bound production evidence."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Mapping
from urllib.parse import urlparse

from src.briefing.models import SourceEvidence
from src.source_normalization import (
    normalize_candidate_source,
    publisher_trust_from_url,
)


_CHANNEL_MAP = {
    "rss": "rss",
    "x": "x",
    "github": "github",
    "huggingface": "huggingface",
    "arxiv": "arxiv",
    "hn": "hacker_news",
    "hacker_news": "hacker_news",
}


def _published_iso(value: object) -> str | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _normalized_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.")


def _slug(value: str) -> str:
    value = value.strip().lower().lstrip("@")
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value)
    return value.strip("-") or "unknown"


def source_evidence_from_candidate(
    candidate: Mapping[str, object],
    *,
    publisher_aliases: Mapping[str, str] | None = None,
    official_x_accounts: Mapping[str, str] | None = None,
    trusted_x_collector: bool = False,
) -> SourceEvidence | None:
    """Create immutable evidence without trusting generated display fields."""
    published_at = _published_iso(
        candidate.get("source_published_at") or candidate.get("published_at")
    )
    if published_at is None:
        return None

    normalized = normalize_candidate_source(candidate)
    if not normalized.canonical_url or not normalized.source_title:
        return None

    source_type = str(candidate.get("source_type") or "rss").strip().lower()
    channel = _CHANNEL_MAP.get(source_type, "rss")
    url = normalized.canonical_url
    source_name = normalized.publisher_name
    source_title = normalized.source_title
    evidence_text = normalized.evidence_text

    aliases = {
        str(key).strip().lower().lstrip("@"): str(value).strip()
        for key, value in dict(publisher_aliases or {}).items()
        if str(value).strip()
    }
    source_tier = str(candidate.get("source_tier") or "").strip().lower()
    official_identity_source = ""
    is_official = False

    if channel == "x":
        handle = str(candidate.get("x_handle") or "").strip().lstrip("@")
        controlled_accounts = {
            str(key).strip().lower().lstrip("@"): str(value).strip()
            for key, value in dict(official_x_accounts or {}).items()
            if str(value).strip()
        }
        controlled_source = controlled_accounts.get(handle.lower(), "")
        is_official = bool(controlled_source) or (
            trusted_x_collector and bool(candidate.get("x_official"))
        )
        official_identity_source = (
            controlled_source if controlled_source
            else "trusted_x_collector" if is_official
            else ""
        )
        authority = (
            "official" if is_official
            else "research" if source_tier == "research"
            else "professional_media" if source_tier == "media"
            else "community"
        )
        alias_key = handle.lower()
        publisher_id = aliases.get(alias_key) or _slug(handle or source_name)
    else:
        host = _normalized_host(url)
        registered_authority, registered_official, registered_identity = (
            publisher_trust_from_url(url)
        )
        if normalized.discovered_via == "hacker_news":
            authority = registered_authority
            is_official = registered_official
            official_identity_source = registered_identity
        elif channel == "arxiv" or source_tier == "research":
            authority = "research"
        elif source_tier == "primary":
            authority = "official"
            is_official = True
            official_identity_source = "rss_source_config"
        elif source_tier in {"media", "professional_media"}:
            authority = "professional_media"
        elif registered_authority != "community":
            authority = registered_authority
            is_official = registered_official
            official_identity_source = registered_identity
        else:
            authority = "community"
        publisher_id = aliases.get(host) or _slug(host or source_name)

    thread_values = {
        "source_item_id": "",
        "thread_id": "",
        "reply_to_item_id": "",
        "quoted_item_id": "",
        "content_type": "fact_event",
        "opinion_author": "",
        "opinion_eligible": False,
        "original_post": False,
        "context_complete": False,
        "stance_type": "",
        "affiliation_disclosure": False,
    }
    if channel == "x":
        thread_values = {
            "source_item_id": str(candidate.get("x_tweet_id") or ""),
            "thread_id": str(
                candidate.get("x_thread_id") or candidate.get("x_tweet_id") or ""
            ),
            "reply_to_item_id": str(candidate.get("x_reply_to_id") or ""),
            "quoted_item_id": str(candidate.get("x_quoted_id") or ""),
            "content_type": str(candidate.get("content_type") or "fact_event"),
            "opinion_author": str(candidate.get("opinion_author") or ""),
            "opinion_eligible": bool(candidate.get("opinion_eligible", False)),
            "original_post": bool(candidate.get("opinion_original_post", False)),
            "context_complete": bool(candidate.get("opinion_context_complete", False)),
            "stance_type": str(candidate.get("opinion_stance_type") or ""),
            "affiliation_disclosure": bool(candidate.get("affiliation_disclosure", False)),
        }

    return SourceEvidence(
        publisher_id=publisher_id,
        publisher_name=source_name,
        channel=channel,
        authority=authority,
        is_official=is_official,
        official_identity_source=official_identity_source,
        source_title=source_title,
        evidence_text=evidence_text,
        url=url,
        published_at=published_at,
        discovered_via=normalized.discovered_via,
        evidence_quality=normalized.evidence_quality,
        **thread_values,
    )
