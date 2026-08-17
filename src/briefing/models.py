"""Immutable, JSON-safe contracts for the production fact brief pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping, Sequence


_CHANNELS = {"rss", "x", "github", "huggingface", "arxiv", "hacker_news"}
_AUTHORITIES = {"official", "research", "professional_media", "community"}
_CONTENT_ORIGINS = {"llm", "source"}
_VALIDATION_MODES = {"rules_and_llm", "rules_only"}
_BRIEF_MODES = {"title_only", "expanded"}
_VALIDATION_ACTIONS = {"accept", "rebuild", "reject"}
_DECISION_ACTIONS = {"create", "block"}
_EXECUTION_STATUSES = {"draft_created", "dry_run", "blocked", "failed"}


def _validate_aware_iso(value: str, field_name: str) -> None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")


def _tuple_of_strings(values: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(str(value) for value in (values or ()))


def _frozen_counts(values: Mapping[str, int] | None) -> Mapping[str, int]:
    return MappingProxyType(
        {str(key): int(value) for key, value in dict(values or {}).items()}
    )


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    publisher_id: str
    publisher_name: str
    channel: str
    authority: str
    is_official: bool
    official_identity_source: str
    source_title: str
    evidence_text: str
    url: str
    published_at: str
    discovered_via: str = ""
    evidence_quality: str = "ready"
    source_item_id: str = ""
    thread_id: str = ""
    reply_to_item_id: str = ""
    quoted_item_id: str = ""

    def __post_init__(self) -> None:
        if self.channel not in _CHANNELS:
            raise ValueError(f"invalid channel: {self.channel}")
        if self.authority not in _AUTHORITIES:
            raise ValueError(f"invalid authority: {self.authority}")
        _validate_aware_iso(self.published_at, "published_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "publisher_id": self.publisher_id,
            "publisher_name": self.publisher_name,
            "channel": self.channel,
            "authority": self.authority,
            "is_official": self.is_official,
            "official_identity_source": self.official_identity_source,
            "source_title": self.source_title,
            "evidence_text": self.evidence_text,
            "url": self.url,
            "published_at": self.published_at,
            "discovered_via": self.discovered_via,
            "evidence_quality": self.evidence_quality,
            "source_item_id": self.source_item_id,
            "thread_id": self.thread_id,
            "reply_to_item_id": self.reply_to_item_id,
            "quoted_item_id": self.quoted_item_id,
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "publisher_id": self.publisher_id,
            "publisher_name": self.publisher_name,
            "channel": self.channel,
            "authority": self.authority,
            "is_official": self.is_official,
            "official_identity_source": self.official_identity_source,
            "url": self.url,
            "published_at": self.published_at,
            "discovered_via": self.discovered_via,
            "evidence_quality": self.evidence_quality,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceEvidence":
        return cls(
            publisher_id=str(data["publisher_id"]),
            publisher_name=str(data["publisher_name"]),
            channel=str(data["channel"]),
            authority=str(data["authority"]),
            is_official=bool(data["is_official"]),
            official_identity_source=str(data.get("official_identity_source") or ""),
            source_title=str(data.get("source_title") or ""),
            evidence_text=str(data.get("evidence_text") or ""),
            url=str(data["url"]),
            published_at=str(data["published_at"]),
            discovered_via=str(data.get("discovered_via") or ""),
            evidence_quality=str(data.get("evidence_quality") or "ready"),
            source_item_id=str(data.get("source_item_id") or ""),
            thread_id=str(data.get("thread_id") or ""),
            reply_to_item_id=str(data.get("reply_to_item_id") or ""),
            quoted_item_id=str(data.get("quoted_item_id") or ""),
        )

    @classmethod
    def from_public_dict(cls, data: Mapping[str, Any]) -> "SourceEvidence":
        return cls.from_dict(data)


@dataclass(frozen=True, slots=True)
class MergedEvent:
    event_key: str
    canonical_evidence: SourceEvidence
    related_evidence: tuple[SourceEvidence, ...] = ()
    editorial_score: float = 0.0
    rank_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "related_evidence", tuple(self.related_evidence))
        object.__setattr__(self, "rank_reasons", _tuple_of_strings(self.rank_reasons))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_key": self.event_key,
            "canonical_evidence": self.canonical_evidence.to_dict(),
            "related_evidence": [item.to_dict() for item in self.related_evidence],
            "editorial_score": self.editorial_score,
            "rank_reasons": list(self.rank_reasons),
        }


@dataclass(frozen=True, slots=True)
class QuarantinedEvent:
    evidence: SourceEvidence
    duplicate_of: str
    reason_code: str
    relationship: str = "uncertain"
    comparison_mode: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": self.evidence.to_dict(),
            "duplicate_of": self.duplicate_of,
            "reason_code": self.reason_code,
            "relationship": self.relationship,
            "comparison_mode": self.comparison_mode,
        }


@dataclass(frozen=True, slots=True)
class EvidenceBinding:
    claim: str
    source_quote: str
    source_url: str

    def to_dict(self) -> dict[str, str]:
        return {
            "claim": self.claim,
            "source_quote": self.source_quote,
            "source_url": self.source_url,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceBinding":
        return cls(
            claim=str(data["claim"]),
            source_quote=str(data["source_quote"]),
            source_url=str(data["source_url"]),
        )


@dataclass(frozen=True, slots=True)
class BuiltBrief:
    event_key: str
    input_index: int
    chinese_title: str
    brief: str
    evidence_bindings: tuple[EvidenceBinding, ...]
    content_origin: str
    brief_mode: str = ""
    brief_reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_bindings", tuple(self.evidence_bindings))
        if self.content_origin not in _CONTENT_ORIGINS:
            raise ValueError(f"invalid content_origin: {self.content_origin}")
        mode = self.brief_mode or ("title_only" if not self.brief.strip() else "expanded")
        if mode not in _BRIEF_MODES:
            raise ValueError(f"invalid brief_mode: {mode}")
        if mode == "title_only" and self.brief.strip():
            raise ValueError("title_only requires an empty brief")
        if mode == "expanded" and not self.brief.strip():
            raise ValueError("expanded requires a non-empty brief")
        object.__setattr__(self, "brief_mode", mode)
        if not self.brief.strip() and not self.brief_reason:
            object.__setattr__(self, "brief_reason", "brief_empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_key": self.event_key,
            "input_index": self.input_index,
            "chinese_title": self.chinese_title,
            "brief": self.brief,
            "evidence_bindings": [item.to_dict() for item in self.evidence_bindings],
            "content_origin": self.content_origin,
            "brief_mode": self.brief_mode,
            "brief_reason": self.brief_reason,
        }


@dataclass(frozen=True, slots=True)
class BriefItem:
    event_key: str
    chinese_title: str
    brief: str
    canonical_source: SourceEvidence
    related_sources: tuple[SourceEvidence, ...]
    published_at: str
    evidence_bindings: tuple[EvidenceBinding, ...]
    content_origin: str
    validation_mode: str
    brief_mode: str = ""
    brief_reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "related_sources", tuple(self.related_sources))
        object.__setattr__(self, "evidence_bindings", tuple(self.evidence_bindings))
        _validate_aware_iso(self.published_at, "published_at")
        if self.content_origin not in _CONTENT_ORIGINS:
            raise ValueError(f"invalid content_origin: {self.content_origin}")
        if self.validation_mode not in _VALIDATION_MODES:
            raise ValueError(f"invalid validation_mode: {self.validation_mode}")
        mode = self.brief_mode or ("title_only" if not self.brief.strip() else "expanded")
        if mode not in _BRIEF_MODES:
            raise ValueError(f"invalid brief_mode: {mode}")
        if mode == "title_only" and self.brief.strip():
            raise ValueError("title_only requires an empty brief")
        if mode == "expanded" and not self.brief.strip():
            raise ValueError("expanded requires a non-empty brief")
        object.__setattr__(self, "brief_mode", mode)
        if not self.brief.strip() and not self.brief_reason:
            object.__setattr__(self, "brief_reason", "brief_empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_key": self.event_key,
            "chinese_title": self.chinese_title,
            "brief": self.brief,
            "canonical_source": self.canonical_source.to_public_dict(),
            "related_sources": [item.to_public_dict() for item in self.related_sources],
            "published_at": self.published_at,
            "evidence_bindings": [item.to_dict() for item in self.evidence_bindings],
            "content_origin": self.content_origin,
            "validation_mode": self.validation_mode,
            "brief_mode": self.brief_mode,
            "brief_reason": self.brief_reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BriefItem":
        return cls(
            event_key=str(data["event_key"]),
            chinese_title=str(data["chinese_title"]),
            brief=str(data["brief"]),
            canonical_source=SourceEvidence.from_public_dict(data["canonical_source"]),
            related_sources=tuple(
                SourceEvidence.from_public_dict(item)
                for item in data.get("related_sources", [])
            ),
            published_at=str(data["published_at"]),
            evidence_bindings=tuple(
                EvidenceBinding.from_dict(item)
                for item in data.get("evidence_bindings", [])
            ),
            content_origin=str(data["content_origin"]),
            validation_mode=str(data["validation_mode"]),
            brief_mode=str(
                data.get("brief_mode")
                or ("title_only" if not str(data.get("brief", "")).strip() else "expanded")
            ),
            brief_reason=str(data.get("brief_reason") or ""),
        )


@dataclass(frozen=True, slots=True)
class RebuildRequest:
    event_key: str
    reason_codes: tuple[str, ...]
    generation_attempt: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", _tuple_of_strings(self.reason_codes))
        if self.generation_attempt != 2:
            raise ValueError("generation_attempt must be 2 for a rebuild")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_key": self.event_key,
            "reason_codes": list(self.reason_codes),
            "generation_attempt": self.generation_attempt,
        }


@dataclass(frozen=True, slots=True)
class ValidationResult:
    action: str
    reason_codes: tuple[str, ...]
    validation_mode: str
    validated_item: BriefItem | None = None
    rebuild_request: RebuildRequest | None = None
    audited_draft: BuiltBrief | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", _tuple_of_strings(self.reason_codes))
        if self.action not in _VALIDATION_ACTIONS:
            raise ValueError(f"invalid validation action: {self.action}")
        if self.validation_mode not in _VALIDATION_MODES:
            raise ValueError(f"invalid validation_mode: {self.validation_mode}")
        if self.action == "accept" and (
            self.validated_item is None or self.rebuild_request is not None
        ):
            raise ValueError("accept requires only a validated_item")
        if self.action == "rebuild" and (
            self.rebuild_request is None or self.validated_item is not None
        ):
            raise ValueError("rebuild requires only a rebuild_request")
        if self.action == "reject" and (
            self.validated_item is not None or self.rebuild_request is not None
        ):
            raise ValueError("reject cannot include an accepted or rebuild payload")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason_codes": list(self.reason_codes),
            "validation_mode": self.validation_mode,
            "validated_item": self.validated_item.to_dict() if self.validated_item else None,
            "rebuild_request": self.rebuild_request.to_dict() if self.rebuild_request else None,
            "audited_draft": self.audited_draft.to_dict() if self.audited_draft else None,
        }


@dataclass(frozen=True, slots=True)
class DraftDecision:
    action: str
    selected_count: int
    min_items: int
    max_items: int
    x_count: int
    max_x_items: int
    reasons: tuple[str, ...] = ()
    excluded_counts: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    source_counts: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", _tuple_of_strings(self.reasons))
        object.__setattr__(self, "excluded_counts", _frozen_counts(self.excluded_counts))
        object.__setattr__(self, "source_counts", _frozen_counts(self.source_counts))
        if self.action not in _DECISION_ACTIONS:
            raise ValueError(f"invalid action: {self.action}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "selected_count": self.selected_count,
            "min_items": self.min_items,
            "max_items": self.max_items,
            "x_count": self.x_count,
            "max_x_items": self.max_x_items,
            "reasons": list(self.reasons),
            "excluded_counts": dict(self.excluded_counts),
            "source_counts": dict(self.source_counts),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DraftDecision":
        return cls(
            action=str(data["action"]),
            selected_count=int(data["selected_count"]),
            min_items=int(data["min_items"]),
            max_items=int(data["max_items"]),
            x_count=int(data["x_count"]),
            max_x_items=int(data["max_x_items"]),
            reasons=tuple(data.get("reasons", [])),
            excluded_counts=data.get("excluded_counts", {}),
            source_counts=data.get("source_counts", {}),
        )


@dataclass(frozen=True, slots=True)
class DraftExecution:
    status: str
    reason: str | None
    started_at: str
    completed_at: str
    media_id: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _EXECUTION_STATUSES:
            raise ValueError(f"invalid status: {self.status}")
        _validate_aware_iso(self.started_at, "started_at")
        _validate_aware_iso(self.completed_at, "completed_at")
        if self.status == "draft_created" and not self.media_id:
            raise ValueError("media_id is required when status is draft_created")
        if self.status != "draft_created" and self.media_id:
            raise ValueError("media_id is only valid when status is draft_created")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "media_id": self.media_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DraftExecution":
        return cls(
            status=str(data["status"]),
            reason=str(data["reason"]) if data.get("reason") is not None else None,
            started_at=str(data["started_at"]),
            completed_at=str(data["completed_at"]),
            media_id=str(data["media_id"]) if data.get("media_id") is not None else None,
        )
