"""Local JSON history and human-feedback storage for shadow runs."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.domain.models import FeedbackLabel
from src.file_utils import atomic_write_text


_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def save_shadow_report(report: dict[str, Any], *, history_dir: Path) -> Path:
    """Persist one immutable JSON report and return its absolute path."""
    run_id = _require_run_id(report.get("run_id"))
    path = history_dir / f"{run_id}.json"
    if path.exists():
        raise FileExistsError(f"Shadow report already exists for run_id={run_id}")
    atomic_write_text(str(path), json.dumps(report, ensure_ascii=False, indent=2))
    return path


def record_feedback(
    *,
    history_dir: Path,
    run_id: str,
    candidate_id: str,
    label: str | FeedbackLabel,
    note: str = "",
    recorded_at: datetime | None = None,
) -> tuple[dict[str, str], Path]:
    """Append a validated human-feedback event without changing the report."""
    run_id = _require_run_id(run_id)
    try:
        feedback_label = FeedbackLabel(label)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in FeedbackLabel)
        raise ValueError(f"Unsupported feedback label: {label}. Allowed: {allowed}") from exc

    report_path = history_dir / f"{run_id}.json"
    if not report_path.is_file():
        raise FileNotFoundError(f"Shadow report not found for run_id={run_id}")
    report = _load_json(report_path)
    candidate_ids = {
        str(item.get("candidate_id") or "")
        for item in report.get("editorial", {}).get("decisions", [])
        if isinstance(item, dict)
    }
    if candidate_id not in candidate_ids:
        raise ValueError(f"Unknown candidate_id for run {run_id}: {candidate_id}")

    timestamp = (recorded_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    event = {
        "run_id": run_id,
        "candidate_id": candidate_id,
        "label": feedback_label.value,
        "note": note.strip(),
        "recorded_at": timestamp.isoformat(),
    }
    feedback_path = history_dir / f"{run_id}.feedback.json"
    payload = _load_json(feedback_path) if feedback_path.is_file() else {
        "schema_version": "shadow-feedback-v1",
        "run_id": run_id,
        "events": [],
    }
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError(f"Invalid feedback history for run_id={run_id}")
    events.append(event)
    payload["events"] = events
    atomic_write_text(str(feedback_path), json.dumps(payload, ensure_ascii=False, indent=2))
    return event, feedback_path


def _require_run_id(value: object) -> str:
    run_id = str(value or "")
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(f"Invalid run_id: {run_id!r}")
    return run_id


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read JSON history: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Invalid JSON history root: {path}")
    return data
