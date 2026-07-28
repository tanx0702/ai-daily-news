"""Immutable source evidence helpers for the editorial pipeline."""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from types import MappingProxyType
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import requests


logger = logging.getLogger(__name__)

_MIN_READABLE_EVIDENCE_LENGTH = 40
_MAX_PROJECT_PURPOSE_LENGTH = 420
_MAX_RELEASE_CHANGES_LENGTH = 640
_MAX_HN_SUMMARY_LENGTH = 900
_HN_EXTERNAL_TIMEOUT = 5
_HN_EXTERNAL_MAX_BYTES = 120_000


@dataclass(frozen=True, slots=True)
class NormalizedEvidence:
    """Clean, source-bound evidence used only by the shadow workflow."""

    title: str
    summary: str
    url: str
    content_quality: str
    content_quality_reason: str
    details: Mapping[str, str]


class _TextExtractor(HTMLParser):
    """Extract visible text without introducing an HTML parsing dependency."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "svg", "noscript", "template"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "svg", "noscript", "template"}:
            self._hidden_depth = max(0, self._hidden_depth - 1)

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)


def normalize_shadow_evidence(
    item: Mapping[str, object],
    *,
    fetch_article_text: Callable[[str], str] | None = None,
) -> NormalizedEvidence:
    """Return readable evidence for v2 shadow review without mutating v1 data."""
    title = _clean_text(_source_value(item, "source_title", "title"), limit=240)
    url = _safe_http_url(_source_value(item, "source_url", "url"))
    summary = _source_value(item, "source_summary", "summary")
    source = _clean_text(item.get("source"), limit=120).lower()
    source_type = _clean_text(item.get("source_type"), limit=40).lower()

    if source_type == "github":
        return _normalize_github_evidence(item, title=title, url=url)
    if _is_hn_rss_metadata(source, summary):
        return _normalize_hn_rss_metadata(title=title, summary=summary, fallback_url=url)
    if source_type == "hn":
        return _normalize_hn_evidence(
            title=title,
            summary=summary,
            url=url,
            fetch_article_text=fetch_article_text or _fetch_external_article_text,
        )

    return NormalizedEvidence(
        title=title,
        summary=_clean_text(summary, limit=_MAX_HN_SUMMARY_LENGTH),
        url=url,
        content_quality="ready",
        content_quality_reason="该来源沿用既有影子证据，不适用 GitHub/HN 专项校验。",
        details=MappingProxyType({}),
    )


def _normalize_github_evidence(
    item: Mapping[str, object],
    *,
    title: str,
    url: str,
) -> NormalizedEvidence:
    raw_evidence = item.get("github_evidence")
    evidence = raw_evidence if isinstance(raw_evidence, Mapping) else {}
    project_purpose = _clean_text(
        evidence.get("project_description"),
        limit=_MAX_PROJECT_PURPOSE_LENGTH,
    )
    release_changes = _clean_text(
        evidence.get("release_notes"),
        limit=_MAX_RELEASE_CHANGES_LENGTH,
    )
    if _is_changelog_pointer(release_changes):
        release_changes = ""
    release_tag = _clean_text(evidence.get("release_tag"), limit=80)
    details = MappingProxyType(
        {
            "project_purpose": project_purpose,
            "release_changes": release_changes,
            "release_tag": release_tag,
        }
    )

    if not project_purpose:
        return NormalizedEvidence(
            title=title,
            summary="",
            url=url,
            content_quality="missing",
            content_quality_reason="GitHub 项目用途缺少可读的源证据。",
            details=details,
        )
    if len(release_changes) < _MIN_READABLE_EVIDENCE_LENGTH:
        return NormalizedEvidence(
            title=title,
            summary=f"项目用途：{project_purpose}",
            url=url,
            content_quality="missing",
            content_quality_reason="GitHub Release 未提供可验证的实际变更说明。",
            details=details,
        )

    release_label = f"版本 {release_tag} 变更" if release_tag else "本次版本变更"
    return NormalizedEvidence(
        title=title,
        summary=f"项目用途：{project_purpose}\n{release_label}：{release_changes}",
        url=url,
        content_quality="ready",
        content_quality_reason="GitHub 项目用途与版本变更均有可读证据。",
        details=details,
    )


def _normalize_hn_rss_metadata(
    *,
    title: str,
    summary: str,
    fallback_url: str,
) -> NormalizedEvidence:
    article_url = _hn_article_url(summary) or fallback_url
    return NormalizedEvidence(
        title=title,
        summary="",
        url=article_url,
        content_quality="metadata_only",
        content_quality_reason="HN RSS 仅提供文章链接、评论链接和积分，未提供新闻正文。",
        details=MappingProxyType({"article_url": article_url}),
    )


def _normalize_hn_evidence(
    *,
    title: str,
    summary: str,
    url: str,
    fetch_article_text: Callable[[str], str],
) -> NormalizedEvidence:
    clean_summary = _clean_text(summary, limit=_MAX_HN_SUMMARY_LENGTH)
    if len(clean_summary) >= _MIN_READABLE_EVIDENCE_LENGTH:
        return NormalizedEvidence(
            title=title,
            summary=clean_summary,
            url=url,
            content_quality="ready",
            content_quality_reason="HN 帖子提供了可读的正文说明。",
            details=MappingProxyType({"evidence_origin": "hn_text"}),
        )

    article_text = ""
    if _is_external_article_url(url):
        try:
            article_text = _clean_text(fetch_article_text(url), limit=_MAX_HN_SUMMARY_LENGTH)
        except Exception as exc:
            logger.info("Shadow HN article evidence unavailable for %s: %s", url, exc)
    if len(article_text) >= _MIN_READABLE_EVIDENCE_LENGTH:
        return NormalizedEvidence(
            title=title,
            summary=article_text,
            url=url,
            content_quality="ready",
            content_quality_reason="HN 外链文章提供了可读的正文证据。",
            details=MappingProxyType({"evidence_origin": "external_article"}),
        )

    return NormalizedEvidence(
        title=title,
        summary=clean_summary,
        url=url,
        content_quality="missing" if not clean_summary else "too_short",
        content_quality_reason="HN 帖子未提供足够正文，外链文章也无法取得可读证据。",
        details=MappingProxyType({}),
    )


def _source_value(item: Mapping[str, object], primary: str, fallback: str) -> object:
    return item.get(primary) or item.get(fallback) or ""


def _clean_text(value: object, *, limit: int) -> str:
    text = unescape(str(value or ""))
    extractor = _TextExtractor()
    try:
        extractor.feed(text)
        extractor.close()
        text = " ".join(extractor.parts)
    except Exception:
        pass
    text = re.sub(r"!\[[^]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<https?://[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].strip()


def _is_changelog_pointer(text: str) -> bool:
    if not text:
        return True
    lowered = text.lower()
    markers = ("see changelog", "changelog.md", "full changelog", "compare changes")
    return any(marker in lowered for marker in markers) and len(text) < 120


def _is_hn_rss_metadata(source: str, summary: object) -> bool:
    if "hacker news" not in source:
        return False
    return bool(
        re.search(r"(?im)^\s*article url:\s*https?://\S+\s*$", str(summary or ""))
        and re.search(r"(?im)^\s*comments url:\s*https?://\S+\s*$", str(summary or ""))
    )


def _hn_article_url(summary: object) -> str:
    match = re.search(r"(?im)^\s*article url:\s*(https?://\S+)\s*$", str(summary or ""))
    return _safe_http_url(match.group(1)) if match else ""


def _safe_http_url(value: object) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        return url
    return "" if address.is_private or address.is_loopback or address.is_link_local else url


def _is_external_article_url(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    return bool(url and hostname and hostname not in {"news.ycombinator.com", "ycombinator.com"})


def _fetch_external_article_text(url: str) -> str:
    """Fetch only a bounded public HTML response for shadow evidence enrichment."""
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "AI-Daily-News-Shadow/1.0"},
            timeout=_HN_EXTERNAL_TIMEOUT,
            stream=True,
            allow_redirects=False,
        )
        response.raise_for_status()
        if "html" not in response.headers.get("Content-Type", "").lower():
            return ""
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            total += len(chunk)
            if total > _HN_EXTERNAL_MAX_BYTES:
                return ""
            chunks.append(chunk)
        return b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
    except requests.RequestException as exc:
        logger.info("Shadow HN article fetch failed for %s: %s", url, exc)
        return ""
    finally:
        if "response" in locals():
            response.close()


def preserve_source_evidence(item: dict) -> dict:
    """Capture collected source fields once before generated fields can replace them."""
    evidence_fields = {
        "source_title": item.get("title", ""),
        "source_summary": item.get("summary", ""),
        "source_url": item.get("url", ""),
        "source_name": item.get("source", ""),
        "source_published_at": item.get("published_at"),
    }
    for field, value in evidence_fields.items():
        item.setdefault(field, value)
    return item


def source_evidence_text(item: dict) -> str:
    """Return the source material available for a generated-text review."""
    fields = ("source_title", "source_summary", "source_excerpt")
    return "\n".join(
        str(item.get(field, "")).strip()
        for field in fields
        if str(item.get(field, "")).strip()
    )
