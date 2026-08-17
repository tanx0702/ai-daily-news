"""Normalize untrusted collector source metadata before evidence construction."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from html import unescape
from html.parser import HTMLParser
import ipaddress
import re
from typing import Mapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


_PUBLISHER_REGISTRY = {
    "hnrss.org": ("Hacker News", "community", False, ""),
    "news.ycombinator.com": ("Hacker News", "community", False, ""),
    "openai.com": ("OpenAI", "official", True, "canonical_domain_allowlist"),
    "anthropic.com": ("Anthropic", "official", True, "canonical_domain_allowlist"),
    "deepmind.google": ("Google DeepMind", "official", True, "canonical_domain_allowlist"),
    "blog.google": ("Google AI", "official", True, "canonical_domain_allowlist"),
    "research.google": ("Google Research", "official", True, "canonical_domain_allowlist"),
    "ai.meta.com": ("Meta AI", "official", True, "canonical_domain_allowlist"),
    "nvidia.com": ("NVIDIA", "official", True, "canonical_domain_allowlist"),
    "microsoft.com": ("Microsoft", "official", True, "canonical_domain_allowlist"),
    "huggingface.co": ("Hugging Face", "community", False, ""),
    "mistral.ai": ("Mistral AI", "official", True, "canonical_domain_allowlist"),
    "x.ai": ("xAI", "official", True, "canonical_domain_allowlist"),
    "cohere.com": ("Cohere", "official", True, "canonical_domain_allowlist"),
    "cerebras.ai": ("Cerebras", "official", True, "canonical_domain_allowlist"),
    "arxiv.org": ("arXiv", "research", False, ""),
    "techcrunch.com": ("TechCrunch", "professional_media", False, ""),
    "theverge.com": ("The Verge", "professional_media", False, ""),
    "venturebeat.com": ("VentureBeat", "professional_media", False, ""),
    "technologyreview.com": ("MIT Technology Review", "professional_media", False, ""),
    "arstechnica.com": ("Ars Technica", "professional_media", False, ""),
    "spectrum.ieee.org": ("IEEE Spectrum", "professional_media", False, ""),
    "36kr.com": ("36Kr", "professional_media", False, ""),
    "jiqizhixin.com": ("机器之心", "professional_media", False, ""),
    "qbitai.com": ("量子位", "professional_media", False, ""),
    "bbc.co.uk": ("BBC", "professional_media", False, ""),
}
_COMMON_SECOND_LEVEL_SUFFIXES = {
    "co.uk", "com.au", "co.jp", "com.cn", "com.sg", "com.br"
}
_PUBLIC_QUERY_KEYS_BY_HOST = {
    "news.ycombinator.com": frozenset({"id"}),
}
_TRACKING_QUERY_KEYS = frozenset({"fbclid", "gclid", "ref", "source"})
_HIDDEN_TAGS = {"script", "style", "svg", "noscript", "template"}
_HN_ARTICLE_URL = re.compile(r"Article URL:\s*(https?://\S+)", re.I)
_HN_COMMENTS_URL = re.compile(r"Comments URL:\s*(https?://\S+)", re.I)
_HN_METADATA = re.compile(
    r"(?:Points:\s*\d+|#\s*Comments:\s*\d+|Comments:\s*\d+)", re.I
)


@dataclass(frozen=True, slots=True)
class NormalizedSourceContent:
    source_title: str
    evidence_text: str
    canonical_url: str
    publisher_name: str
    discovered_via: str
    evidence_quality: str
    request_url: str = field(default="", repr=False, compare=False)


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag, attrs) -> None:
        if tag.casefold() in _HIDDEN_TAGS:
            self.hidden_depth += 1

    def handle_endtag(self, tag) -> None:
        if tag.casefold() in _HIDDEN_TAGS:
            self.hidden_depth = max(0, self.hidden_depth - 1)

    def handle_data(self, data) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


class _HNEnvelopeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible_parts: list[str] = []
        self.article_links: list[tuple[str, str]] = []
        self.comments_links: list[tuple[str, str]] = []
        self.pending_label = ""
        self.active_label = ""
        self.active_href = ""
        self.active_parts: list[str] = []
        self.hidden_depth = 0

    def _append(self, label: str, href: str, visible: str = "") -> None:
        target = self.article_links if label == "article" else self.comments_links
        target.append((href, visible))

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.casefold()
        if tag in _HIDDEN_TAGS:
            self.hidden_depth += 1
            return
        if self.hidden_depth:
            return
        if tag == "a":
            self.active_label = self.pending_label
            self.active_href = dict(attrs).get("href") or ""
            self.active_parts.clear()
            self.pending_label = ""

    def handle_endtag(self, tag) -> None:
        tag = tag.casefold()
        if tag in _HIDDEN_TAGS:
            self.hidden_depth = max(0, self.hidden_depth - 1)
            return
        if self.hidden_depth:
            return
        if tag == "a" and self.active_label:
            visible = " ".join(self.active_parts).strip()
            urls = re.findall(r"https?://[^\s<>]+", visible, re.I)
            self._append(
                self.active_label,
                self.active_href,
                urls[0] if len(urls) == 1 else visible,
            )
            self.active_label = ""
            self.active_href = ""
            self.active_parts.clear()

    def handle_data(self, data) -> None:
        if self.hidden_depth:
            return
        self.visible_parts.append(data)
        if self.active_label:
            self.active_parts.append(data)
        lowered = data.casefold()
        article_index = lowered.rfind("article url:")
        comments_index = lowered.rfind("comments url:")
        if article_index >= 0 or comments_index >= 0:
            self.pending_label = (
                "article" if article_index > comments_index else "comments"
            )
        for label, raw_url in re.findall(
            r"(Article URL|Comments URL):\s*(https?://[^\s<>]+)", data, re.I
        ):
            self._append(
                "article" if label.casefold().startswith("article") else "comments",
                raw_url,
                raw_url,
            )
            self.pending_label = ""


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


def _looks_like_ip_literal(hostname: str) -> bool:
    host = hostname.strip("[]")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return True
    return bool(re.fullmatch(
        r"(?:0x[0-9a-f]+|[0-9]+|(?:0x[0-9a-f]+|0[0-7]+|[0-9]+)"
        r"(?:\.(?:0x[0-9a-f]+|0[0-7]+|[0-9]+)){1,3})",
        host,
        re.I,
    ))


def _validated_request_url(value: object) -> str:
    url = str(value or "").strip()
    if re.search(r"[\x00-\x20\x7f]", url):
        return ""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return ""
    scheme = parsed.scheme.casefold()
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (scheme == "http" and port not in {None, 80})
        or (scheme == "https" and port not in {None, 443})
        or hostname.casefold().rstrip(".") == "localhost"
        or _looks_like_ip_literal(hostname)
    ):
        return ""
    try:
        host = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return ""
    return urlunparse((scheme, host, parsed.path or "", "", parsed.query, ""))


def public_source_url(value: object) -> str:
    request_url = _validated_request_url(value)
    if not request_url:
        return ""
    parsed = urlparse(request_url)
    allowed = _PUBLIC_QUERY_KEYS_BY_HOST.get(parsed.hostname or "", frozenset())
    public_pairs: list[tuple[str, str]] = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.casefold()
        if lowered in allowed:
            if (
                parsed.hostname == "news.ycombinator.com"
                and (parsed.path != "/item" or not re.fullmatch(r"[0-9]{1,20}", item))
            ):
                return ""
            public_pairs.append((key, item))
        elif lowered in _TRACKING_QUERY_KEYS or lowered.startswith("utm_"):
            continue
        else:
            return ""
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        "",
        urlencode(tuple(public_pairs)),
        "",
    ))


def sanitize_source_url_for_audit(value: object) -> str:
    safe = public_source_url(value)
    if safe:
        parsed = urlparse(safe)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    digest = hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]
    return f"invalid-url:sha256:{digest}"


def _registry_entry(url: str):
    request_url = _validated_request_url(url)
    if not request_url:
        return None
    host = (urlparse(request_url).hostname or "").removeprefix("www.")
    for domain, values in _PUBLISHER_REGISTRY.items():
        if host == domain or host.endswith(f".{domain}"):
            return values
    return None


def publisher_name_from_url(url: str) -> str:
    entry = _registry_entry(url)
    if entry:
        return entry[0]
    request_url = _validated_request_url(url)
    if not request_url:
        return ""
    host = (urlparse(request_url).hostname or "").removeprefix("www.")
    labels = host.split(".")
    index = (
        -3
        if len(labels) >= 3 and ".".join(labels[-2:]) in _COMMON_SECOND_LEVEL_SUFFIXES
        else -2
    )
    candidate = labels[index] if len(labels) >= 2 else host
    return candidate.replace("-", " ").title()


def publisher_trust_from_url(url: str) -> tuple[str, bool, str]:
    entry = _registry_entry(url)
    return entry[1:] if entry else ("community", False, "")


def _parse_hn_envelope(value: object):
    parser = _HNEnvelopeParser()
    raw = unescape(str(value or ""))
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        pass
    visible = _clean_text(" ".join(parser.visible_parts))
    for label, raw_url in re.findall(
        r"(Article URL|Comments URL):\s*(https?://\S+)", visible, re.I
    ):
        parser._append(
            "article" if label.casefold().startswith("article") else "comments",
            raw_url,
            raw_url,
        )
    return visible, tuple(parser.article_links), tuple(parser.comments_links)


def _select_labelled_request_url(links: tuple[tuple[str, str], ...]):
    candidates: list[str] = []
    for href, visible in links:
        safe_href = _validated_request_url(href)
        safe_visible = _validated_request_url(visible)
        if safe_href and safe_visible and safe_href != safe_visible:
            return "", True
        selected = safe_href or safe_visible
        if selected:
            candidates.append(selected)
    unique = tuple(dict.fromkeys(candidates))
    return (unique[0], False) if len(unique) == 1 else ("", bool(unique))


def normalize_candidate_source(
    candidate: Mapping[str, object],
) -> NormalizedSourceContent:
    title = _clean_text(candidate.get("source_title") or candidate.get("title"))
    request_url = _validated_request_url(
        candidate.get("source_url") or candidate.get("url")
    )
    canonical_url = public_source_url(request_url)
    source = _clean_text(candidate.get("source_name") or candidate.get("source"))
    source_type = str(candidate.get("source_type") or "rss").strip().casefold()
    is_hn = source_type in {"hn", "hacker_news"} or "hacker news" in source.casefold()

    if is_hn:
        raw_summary = candidate.get("source_summary") or candidate.get("summary") or ""
        visible, article_links, comment_links = _parse_hn_envelope(raw_summary)
        article_request_url, ambiguous = _select_labelled_request_url(article_links)
        if not ambiguous and article_request_url:
            request_url = article_request_url
            canonical_url = public_source_url(request_url)
        removable = [
            raw
            for pair in article_links + comment_links
            for raw in pair
            if raw
        ]
        cleaned = _HN_ARTICLE_URL.sub("", visible)
        cleaned = _HN_COMMENTS_URL.sub("", cleaned)
        cleaned = _HN_METADATA.sub("", cleaned)
        for raw_url in removable:
            cleaned = cleaned.replace(raw_url, "")
        cleaned = re.sub(r"\b(?:Article URL|Comments URL):\s*", "", cleaned, flags=re.I)
        cleaned = _clean_text(cleaned)
        details = tuple(dict.fromkeys(
            value
            for value in (
                cleaned,
                _clean_text(candidate.get("source_excerpt")),
                _clean_text(candidate.get("source_body")),
            )
            if value
        ))
        evidence = "\n".join((title, *details) if title else details)
        return NormalizedSourceContent(
            source_title=title,
            evidence_text=evidence or title,
            canonical_url=canonical_url,
            publisher_name=publisher_name_from_url(canonical_url) or "Hacker News",
            discovered_via="hacker_news",
            evidence_quality="ready" if details else "title_only",
            request_url=request_url,
        )

    evidence_parts = (
        title,
        _clean_text(candidate.get("source_summary")),
        _clean_text(candidate.get("source_excerpt")),
        _clean_text(candidate.get("source_body")),
    )
    nonempty = tuple(dict.fromkeys(part for part in evidence_parts if part))
    return NormalizedSourceContent(
        source_title=title,
        evidence_text="\n".join(nonempty),
        canonical_url=canonical_url,
        publisher_name=publisher_name_from_url(canonical_url) or source,
        discovered_via=source_type,
        evidence_quality="ready" if len(nonempty) > 1 else "title_only",
        request_url=request_url,
    )
