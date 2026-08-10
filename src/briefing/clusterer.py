"""Deterministic event clustering and ambiguous-duplicate quarantine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
import hashlib
import re
from types import MappingProxyType
from typing import Mapping, Sequence
from urllib.parse import urlparse, urlunparse

from src.briefing.models import MergedEvent, QuarantinedEvent, SourceEvidence


_AUTHORITY_ORDER = {
    "official": 0,
    "research": 1,
    "professional_media": 2,
    "community": 3,
}
_ACTION_GROUPS = {
    "release": {
        "release", "releases", "released", "launch", "launches", "launched",
        "rollout", "rolls", "available", "access", "receiving", "发布", "推出", "上线",
    },
    "funding": {"funding", "funded", "raises", "raised", "融资", "投资", "估值"},
    "acquisition": {"acquire", "acquires", "acquired", "buy", "buys", "收购", "合并"},
    "open_source": {"open-source", "opensource", "开源"},
    "office": {"office", "campus", "总部", "办公室"},
    "research": {"paper", "study", "research", "论文", "研究"},
}
_STOP_WORDS = {
    "a", "an", "the", "to", "for", "of", "and", "with", "its", "new",
    "begin", "begins", "makes", "make", "selected",
}


@dataclass(frozen=True, slots=True)
class ClusterResult:
    events: tuple[MergedEvent, ...]
    quarantined: tuple[QuarantinedEvent, ...]
    diagnostics: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "quarantined", tuple(self.quarantined))
        object.__setattr__(
            self,
            "diagnostics",
            MappingProxyType({str(key): int(value) for key, value in self.diagnostics.items()}),
        )


def _normalized_url(value: str) -> str:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not host:
        return ""
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    path = re.sub(r"/+", "/", parsed.path).rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), netloc, path, "", "", ""))


def _x_status_id(value: str) -> str:
    parsed = urlparse(value)
    if (parsed.hostname or "").lower() not in {"x.com", "www.x.com"}:
        return ""
    match = re.search(r"/status/(\d+)(?:/|$)", parsed.path)
    return match.group(1) if match else ""


def _tokens(value: str) -> set[str]:
    words = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9.+-]*|[\u4e00-\u9fff]{2,}", value.lower())
        if token not in _STOP_WORDS
    }
    return words


def _title_similarity(a: str, b: str) -> float:
    a_normal = " ".join(sorted(_tokens(a)))
    b_normal = " ".join(sorted(_tokens(b)))
    if not a_normal or not b_normal:
        return 0.0
    set_a = set(a_normal.split())
    set_b = set(b_normal.split())
    jaccard = len(set_a & set_b) / len(set_a | set_b)
    sequence = SequenceMatcher(None, a_normal, b_normal).ratio()
    return max(jaccard, sequence)


def _action_groups(value: str) -> set[str]:
    tokens = _tokens(value)
    lowered = value.lower()
    return {
        group
        for group, markers in _ACTION_GROUPS.items()
        if markers & tokens or any(marker in lowered for marker in markers if len(marker) > 1)
    }


def _entities(value: str) -> set[str]:
    tokens = _tokens(value)
    return {
        token
        for token in tokens
        if any(char.isdigit() for char in token)
        or token in {
            "openai", "anthropic", "claude", "gemini", "google", "deepmind",
            "meta", "llama", "microsoft", "nvidia", "mistral", "deepseek", "qwen",
        }
    }


def _published_timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _strength(evidence: SourceEvidence) -> tuple[int, int, int, float, str]:
    return (
        _AUTHORITY_ORDER.get(evidence.authority, 9),
        0 if evidence.is_official else 1,
        -len(evidence.evidence_text),
        -_published_timestamp(evidence.published_at),
        evidence.url,
    )


def _event_key(evidence: SourceEvidence) -> str:
    signature = " ".join(sorted(_tokens(evidence.source_title)))
    if not signature:
        signature = _normalized_url(evidence.url) or evidence.publisher_id
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    return f"event-{digest}"


def _relationship(a: SourceEvidence, b: SourceEvidence) -> str:
    url_a = _normalized_url(a.url)
    url_b = _normalized_url(b.url)
    status_a = _x_status_id(a.url)
    status_b = _x_status_id(b.url)
    if url_a and url_a == url_b:
        return "confirmed"
    if status_a and status_a == status_b:
        return "confirmed"

    similarity = _title_similarity(a.source_title, b.source_title)
    if similarity >= 0.82:
        return "confirmed"

    actions_a = _action_groups(a.source_title)
    actions_b = _action_groups(b.source_title)
    shared_actions = actions_a & actions_b
    shared_entities = _entities(a.source_title) & _entities(b.source_title)
    time_distance = abs(
        _published_timestamp(a.published_at) - _published_timestamp(b.published_at)
    )
    if similarity >= 0.40 and shared_actions and shared_entities and time_distance <= 48 * 3600:
        return "ambiguous"
    return "distinct"


class EventClusterer:
    """Cluster only confirmed duplicates and quarantine unresolved overlap."""

    def cluster(
        self,
        evidence: Sequence[SourceEvidence],
        editorial_scores: Mapping[str, float] | None = None,
    ) -> ClusterResult:
        scores = dict(editorial_scores or {})
        ordered = sorted(evidence, key=_strength)
        event_rows: list[dict[str, object]] = []
        quarantined: list[QuarantinedEvent] = []
        merged_count = 0

        for candidate in ordered:
            matched_row: dict[str, object] | None = None
            relationship = "distinct"
            for row in event_rows:
                canonical = row["canonical"]
                assert isinstance(canonical, SourceEvidence)
                current = _relationship(candidate, canonical)
                if current == "confirmed":
                    matched_row = row
                    relationship = current
                    break
                if current == "ambiguous" and matched_row is None:
                    matched_row = row
                    relationship = current

            if matched_row is None:
                event_rows.append(
                    {
                        "event_key": _event_key(candidate),
                        "canonical": candidate,
                        "related": [],
                    }
                )
                continue

            event_key = str(matched_row["event_key"])
            if relationship == "confirmed":
                related = matched_row["related"]
                assert isinstance(related, list)
                related.append(candidate)
                merged_count += 1
            else:
                quarantined.append(
                    QuarantinedEvent(
                        evidence=candidate,
                        duplicate_of=event_key,
                        reason_code="ambiguous_duplicate",
                    )
                )

        events = tuple(
            MergedEvent(
                event_key=str(row["event_key"]),
                canonical_evidence=row["canonical"],
                related_evidence=tuple(row["related"]),
                editorial_score=float(
                    scores.get(
                        str(row["event_key"]),
                        scores.get(row["canonical"].url, 0.0),
                    )
                ),
                rank_reasons=("canonical_authority",),
            )
            for row in event_rows
        )
        return ClusterResult(
            events=events,
            quarantined=tuple(quarantined),
            diagnostics={
                "candidate_count": len(evidence),
                "event_count": len(events),
                "merged_count": merged_count,
                "quarantined_count": len(quarantined),
            },
        )
