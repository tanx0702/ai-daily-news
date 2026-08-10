"""Versioned, read-compatible ``latest.json`` contracts for fact briefs."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from src.briefing.models import BriefItem, DraftDecision, DraftExecution


logger = logging.getLogger(__name__)
_LATEST_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class LatestSnapshot:
    """An immutable read model for the current or legacy latest.json schema."""

    schema_version: int
    brief_items: tuple[BriefItem, ...] = ()
    draft_decision: DraftDecision | None = None
    draft_execution: DraftExecution | None = None
    diagnostics: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    legacy_news: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version not in (1, _LATEST_SCHEMA_VERSION):
            raise ValueError(f"unsupported latest schema version: {self.schema_version}")
        object.__setattr__(self, "brief_items", tuple(self.brief_items))
        object.__setattr__(self, "diagnostics", _freeze_json_mapping(self.diagnostics))
        object.__setattr__(
            self,
            "legacy_news",
            tuple(_freeze_json_mapping(item) for item in self.legacy_news),
        )
        if self.schema_version == _LATEST_SCHEMA_VERSION and self.legacy_news:
            raise ValueError("v2 latest snapshots cannot contain legacy news")
        if self.schema_version == _LATEST_SCHEMA_VERSION:
            if self.draft_execution is None:
                raise ValueError("v2 latest snapshots require draft_execution")
            if self.draft_decision is None and (
                self.brief_items
                or self.draft_execution.status != "failed"
                or self.draft_execution.reason != "invalid_configuration"
            ):
                raise ValueError(
                    "a null draft_decision is only valid for invalid configuration"
                )


def build_latest_v2(
    brief_items: Sequence[BriefItem],
    draft_decision: DraftDecision | None,
    draft_execution: DraftExecution,
    *,
    diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the only payload written by the v2 fact-brief pipeline."""
    if not all(isinstance(item, BriefItem) for item in brief_items):
        raise TypeError("brief_items must contain BriefItem instances")
    if draft_decision is not None and not isinstance(draft_decision, DraftDecision):
        raise TypeError("draft_decision must be a DraftDecision")
    if not isinstance(draft_execution, DraftExecution):
        raise TypeError("draft_execution must be a DraftExecution")
    if diagnostics is not None and not isinstance(diagnostics, Mapping):
        raise TypeError("diagnostics must be a mapping")

    snapshot = LatestSnapshot(
        schema_version=_LATEST_SCHEMA_VERSION,
        brief_items=tuple(brief_items),
        draft_decision=draft_decision,
        draft_execution=draft_execution,
        diagnostics=diagnostics or {},
    )
    return {
        "schema_version": _LATEST_SCHEMA_VERSION,
        "brief_items": [item.to_dict() for item in snapshot.brief_items],
        "draft_decision": (
            snapshot.draft_decision.to_dict() if snapshot.draft_decision else None
        ),
        "draft_execution": snapshot.draft_execution.to_dict(),
        "diagnostics": _thaw_json(snapshot.diagnostics),
    }


def load_latest(path: str | Path) -> LatestSnapshot:
    """Load v2 data or read-only adapt a legacy v1 latest.json file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read latest snapshot: {path}") from exc
    if not isinstance(data, Mapping):
        raise ValueError("latest snapshot root must be an object")

    version = data.get("schema_version")
    if version in (None, 1):
        return _load_v1(data)
    if version != _LATEST_SCHEMA_VERSION:
        raise ValueError(f"unsupported latest schema version: {version}")
    return _load_v2(data)


def _load_v2(data: Mapping[str, Any]) -> LatestSnapshot:
    brief_items = data.get("brief_items")
    diagnostics = data.get("diagnostics", {})
    if not isinstance(brief_items, list):
        raise ValueError("v2 brief_items must be a list")
    if not isinstance(diagnostics, Mapping):
        raise ValueError("v2 diagnostics must be an object")
    try:
        raw_decision = data.get("draft_decision")
        return LatestSnapshot(
            schema_version=_LATEST_SCHEMA_VERSION,
            brief_items=tuple(BriefItem.from_dict(item) for item in brief_items),
            draft_decision=(
                DraftDecision.from_dict(raw_decision)
                if raw_decision is not None
                else None
            ),
            draft_execution=DraftExecution.from_dict(data["draft_execution"]),
            diagnostics=diagnostics,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid v2 latest snapshot") from exc


def _load_v1(data: Mapping[str, Any]) -> LatestSnapshot:
    news = data.get("news", [])
    if not isinstance(news, list) or not all(isinstance(item, Mapping) for item in news):
        raise ValueError("v1 news must be a list of objects")
    logger.warning(
        "Deprecated latest.json v1 schema loaded; this compatibility adapter is read-only"
    )
    return LatestSnapshot(
        schema_version=1,
        diagnostics={},
        legacy_news=tuple(news),
    )


def _freeze_json_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze_json(value) for key, value in value.items()})


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_json_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw_json(item) for item in value]
    return value
