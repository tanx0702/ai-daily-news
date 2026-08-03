"""Persist production collection candidates for private shadow-run reuse."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.domain.models import CollectionDiagnostics
from src.file_utils import atomic_write_text


_SCHEMA_VERSION = "production-snapshot-v1"
_DEFAULT_SNAPSHOT_DIR = Path("docs/debug/shadow")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_FIELDS = ("published_at", "source_published_at")


def save_production_snapshot(
    items: list[dict[str, Any]],
    *,
    date_str: str,
    snapshot_dir: Path | str = _DEFAULT_SNAPSHOT_DIR,
    collection_diagnostics: CollectionDiagnostics | Mapping[str, Any] | None = None,
) -> Path:
    """Atomically save JSON-safe production candidates and return the snapshot path."""
    _validate_date(date_str)
    _validate_items(items)
    diagnostics = _serialize_diagnostics(collection_diagnostics, len(items))
    path = Path(snapshot_dir) / f"production-candidates-{date_str}.json"
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "report_date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": _json_safe(items),
    }
    if diagnostics is not None:
        payload["collection_diagnostics"] = diagnostics
    try:
        content = json.dumps(payload, ensure_ascii=False, indent=2)
    except (TypeError, ValueError) as exc:
        raise ValueError("Snapshot items must be JSON-safe") from exc
    atomic_write_text(str(path), content)
    return path


def load_production_snapshot(path: Path | str) -> tuple[list[dict[str, Any]], CollectionDiagnostics]:
    """Load a validated snapshot and restore known candidate timestamp fields."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read production snapshot: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("Production snapshot root must be an object")
    if data.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError("Unsupported production snapshot schema")
    _validate_date(data.get("report_date"))
    _validate_generated_at(data.get("generated_at"))
    items = data.get("items")
    _validate_items(items)
    restored_items = [_restore_datetimes(item) for item in items]
    diagnostics = _load_diagnostics(data.get("collection_diagnostics"), len(items))
    return restored_items, diagnostics


def _validate_date(value: object) -> None:
    if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
        raise ValueError("Snapshot report_date must use YYYY-MM-DD")


def _validate_items(items: object) -> None:
    if not isinstance(items, list):
        raise ValueError("Production snapshot items must be a list")
    if any(not isinstance(item, dict) for item in items):
        raise ValueError("Production snapshot items must contain only objects")


def _validate_generated_at(value: object) -> None:
    generated_at = _parse_datetime(value, "generated_at")
    if generated_at.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError("Snapshot generated_at must use UTC")


def _serialize_diagnostics(
    value: CollectionDiagnostics | Mapping[str, Any] | None, item_count: int
) -> dict[str, int] | None:
    if value is None:
        return {"returned_candidate_count": item_count}
    if isinstance(value, CollectionDiagnostics):
        return {
            "fetched_total": value.fetched_total,
            "source_merge_removed": value.source_merge_removed,
            "filtered_total": value.filtered_total,
            "topic_cluster_removed": value.topic_cluster_removed,
            "final_editorial_dedup_removed": value.final_editorial_dedup_removed,
            "returned_candidate_count": value.returned_candidate_count,
        }
    if isinstance(value, Mapping):
        return dict(value)
    raise ValueError("collection_diagnostics must be a mapping or CollectionDiagnostics")


def _load_diagnostics(value: object, item_count: int) -> CollectionDiagnostics:
    if value is None:
        return CollectionDiagnostics(returned_candidate_count=item_count)
    if not isinstance(value, Mapping):
        raise ValueError("Snapshot collection_diagnostics must be an object")
    diagnostics = CollectionDiagnostics.from_mapping(value)
    if "returned_candidate_count" not in value:
        return CollectionDiagnostics(
            fetched_total=diagnostics.fetched_total,
            source_merge_removed=diagnostics.source_merge_removed,
            filtered_total=diagnostics.filtered_total,
            topic_cluster_removed=diagnostics.topic_cluster_removed,
            final_editorial_dedup_removed=diagnostics.final_editorial_dedup_removed,
            returned_candidate_count=item_count,
        )
    return diagnostics


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Snapshot datetimes must include timezone information")
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _restore_datetimes(item: dict[str, Any]) -> dict[str, Any]:
    restored = dict(item)
    for field in _DATETIME_FIELDS:
        if field in restored and restored[field] is not None:
            restored[field] = _parse_datetime(restored[field], field)
    return restored


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"Snapshot {field} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Snapshot {field} is not a valid ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Snapshot {field} must include timezone information")
    return parsed
