"""Adapters from immutable briefing contracts to deterministic display data."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from src.briefing.models import BriefItem


def brief_item_to_display_dict(item: BriefItem) -> dict[str, str]:
    """Project a validated item into the stable fields public renderers consume."""
    return {
        "id": item.event_key,
        "event_key": item.event_key,
        "title": item.chinese_title,
        "chinese_title": item.chinese_title,
        "brief": item.brief,
        "summary": item.brief,
        "url": item.canonical_source.url,
        "source_url": item.canonical_source.url,
        "source": item.canonical_source.publisher_name,
        "source_type": item.canonical_source.channel,
        "published_at": item.published_at,
    }


def content_fingerprint(items: Sequence[BriefItem | Mapping[str, Any]]) -> str:
    """Return a stable digest for the ordered public text and canonical links."""
    projection = [_fingerprint_projection(item) for item in items]
    encoded = json.dumps(
        projection,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fingerprint_projection(item: BriefItem | Mapping[str, Any]) -> dict[str, str]:
    if isinstance(item, BriefItem):
        display = brief_item_to_display_dict(item)
    elif isinstance(item, Mapping):
        display = item
    else:
        raise TypeError("content fingerprint items must be BriefItem or mappings")

    title = display.get("title", display.get("chinese_title"))
    brief = display.get("brief", display.get("summary"))
    source_url = display.get("source_url", display.get("url"))
    if not all(isinstance(value, str) for value in (title, brief, source_url)):
        raise ValueError("content fingerprint requires title, brief, and source URL")
    return {"title": title, "brief": brief, "source_url": source_url}
