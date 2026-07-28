"""Read saved shadow runs for the private editorial review page."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from src.domain.models import FeedbackLabel


_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def list_review_runs(history_dir: Path, *, limit: int = 30) -> list[dict[str, str]]:
    """Return recent completed shadow runs that are safe to select in the UI."""
    if limit <= 0 or not history_dir.is_dir():
        return []

    runs: list[dict[str, str]] = []
    for path in history_dir.glob("shadow-*.json"):
        if path.name.endswith(".feedback.json"):
            continue
        payload = _read_json(path)
        if not payload or _workflow_state(payload) != "completed":
            continue
        run_id = _string(payload.get("run_id"))
        generated_at = _string(payload.get("generated_at"))
        if not _is_valid_run_id(run_id) or not _parse_datetime(generated_at):
            continue
        runs.append({"run_id": run_id, "generated_at": generated_at})

    runs.sort(key=lambda item: _parse_datetime(item["generated_at"]), reverse=True)
    return runs[:limit]


def load_review_run(history_dir: Path, run_id: str | None = None) -> dict[str, Any] | None:
    """Load one completed run with display fields and each candidate's latest label."""
    selected_run_id = run_id or _latest_run_id(history_dir)
    if not selected_run_id or not _is_valid_run_id(selected_run_id):
        return None

    payload = _read_json(history_dir / f"{selected_run_id}.json")
    if not payload or _workflow_state(payload) != "completed":
        return None
    if _string(payload.get("run_id")) != selected_run_id:
        return None

    latest_feedback = _latest_feedback(history_dir, selected_run_id)
    analyses = _by_candidate_id(_items(_mapping(payload.get("analysis")).get("items")))
    decisions = _by_candidate_id(_items(_mapping(payload.get("editorial")).get("decisions")))
    candidates: list[dict[str, Any]] = []

    for candidate in _items(payload.get("candidates")):
        candidate_id = _string(candidate.get("candidate_id"))
        if not candidate_id:
            continue
        candidates.append(
            {
                "candidate_id": candidate_id,
                "title": _string(candidate.get("source_title")),
                "summary": _string(candidate.get("source_summary")),
                "source": _string(candidate.get("source")),
                "source_type": _string(candidate.get("source_type")),
                "published_at": _string(candidate.get("published_at")),
                "source_url": _safe_http_url(_string(candidate.get("source_url"))),
                "content_quality": _string(candidate.get("content_quality")),
                "content_quality_reason": _string(candidate.get("content_quality_reason")),
                "evidence_details": _string_mapping(candidate.get("evidence_details")),
                "analysis": _analysis_display(analyses.get(candidate_id, {})),
                "decision": _decision_display(decisions.get(candidate_id, {})),
                "feedback": latest_feedback.get(candidate_id),
            }
        )

    return {
        "run_id": selected_run_id,
        "generated_at": _string(payload.get("generated_at")),
        "candidates": candidates,
    }


def _latest_run_id(history_dir: Path) -> str:
    runs = list_review_runs(history_dir, limit=1)
    return runs[0]["run_id"] if runs else ""


def _latest_feedback(history_dir: Path, run_id: str) -> dict[str, dict[str, str]]:
    payload = _read_json(history_dir / f"{run_id}.feedback.json")
    if not payload:
        return {}

    latest: dict[str, dict[str, str]] = {}
    for event in _items(payload.get("events")):
        candidate_id = _string(event.get("candidate_id"))
        label = _string(event.get("label"))
        recorded_at = _parse_datetime(_string(event.get("recorded_at")))
        if not candidate_id or label not in {item.value for item in FeedbackLabel}:
            continue
        if recorded_at is None:
            continue
        previous = latest.get(candidate_id)
        previous_at = _parse_datetime(previous["recorded_at"]) if previous else None
        if previous_at is None or recorded_at >= previous_at:
            latest[candidate_id] = {
                "label": label,
                "note": _string(event.get("note")),
                "recorded_at": recorded_at.isoformat(),
            }
    return latest


def _analysis_display(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "importance_score": item.get("importance_score"),
        "evidence_score": item.get("evidence_score"),
        "impact_score": item.get("impact_score"),
        "risk_level": _string(item.get("risk_level")),
        "importance_reason": _string(item.get("importance_reason")),
        "verifiability_reason": _string(item.get("verifiability_reason")),
        "impact_analysis": _string(item.get("impact_analysis")),
    }


def _decision_display(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action": _string(item.get("action")),
        "rank": item.get("rank"),
        "reason": _string(item.get("reason")),
        "audience": _string(item.get("audience")),
        "angle": _string(item.get("angle")),
        "title_direction": _string(item.get("title_direction")),
    }


def _by_candidate_id(items: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {
        candidate_id: item
        for item in items
        if (candidate_id := _string(item.get("candidate_id")))
    }


def _workflow_state(payload: Mapping[str, Any]) -> str:
    return _string(_mapping(payload.get("workflow")).get("state"))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _safe_http_url(value: str) -> str:
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _is_valid_run_id(value: str) -> bool:
    return bool(_RUN_ID_PATTERN.fullmatch(value))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_mapping(value: object) -> dict[str, str]:
    return {
        str(key): _string(item)
        for key, item in _mapping(value).items()
        if isinstance(key, str) and _string(item)
    }


def _items(value: object) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""
