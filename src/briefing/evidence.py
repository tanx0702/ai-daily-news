"""Normalize collector candidates into source-bound production evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import re
from typing import Mapping
from urllib.parse import urlparse

from src.briefing.models import SourceEvidence


_CHANNEL_MAP = {
    "rss": "rss",
    "x": "x",
    "github": "github",
    "huggingface": "huggingface",
    "arxiv": "arxiv",
    "hn": "hacker_news",
    "hacker_news": "hacker_news",
}


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _clean_text(value: object) -> str:
    raw = unescape(str(value or ""))
    parser = _VisibleText()
    try:
        parser.feed(raw)
        parser.close()
        raw = " ".join(parser.parts)
    except Exception:
        pass
    return re.sub(r"\s+", " ", raw).strip()


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

    source_type = str(candidate.get("source_type") or "rss").strip().lower()
    channel = _CHANNEL_MAP.get(source_type, "rss")
    url = str(candidate.get("source_url") or candidate.get("url") or "").strip()
    source_name = _clean_text(candidate.get("source_name") or candidate.get("source"))
    source_title = _clean_text(candidate.get("source_title") or candidate.get("title"))
    evidence_parts = [
        source_title,
        _clean_text(candidate.get("source_summary")),
        _clean_text(candidate.get("source_excerpt")),
        _clean_text(candidate.get("source_body")),
    ]
    evidence_text = "\n".join(dict.fromkeys(part for part in evidence_parts if part))

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
        if channel == "arxiv" or source_tier == "research":
            authority = "research"
        elif source_tier == "primary":
            authority = "official"
            is_official = True
            official_identity_source = "rss_source_config"
        elif source_tier in {"media", "professional_media"}:
            authority = "professional_media"
        else:
            authority = "community"
        publisher_id = aliases.get(host) or _slug(host or source_name)

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
    )
