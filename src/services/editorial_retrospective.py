"""Aggregate shadow-run history and human feedback for editorial calibration."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from src.file_utils import atomic_write_text


_FEEDBACK_LABELS = ("good_topic", "bad_topic", "duplicate", "not_interesting")
_SCORE_BUCKETS = ("0-3.9", "4-6.9", "7-8.4", "8.5-10")
_RISK_LEVELS = ("low", "medium", "high")
_EDITORIAL_ACTIONS = ("write", "reserve", "reject")
_NEGATIVE_FEEDBACK_LABELS = ("bad_topic", "duplicate", "not_interesting")
_HIGH_IMPORTANCE_THRESHOLD = 8.5
_LOW_IMPORTANCE_THRESHOLD = 7.0
_MAX_PRIORITY_SAMPLES = 10


def build_editorial_retrospective(
    history_dir: Path,
    *,
    days: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return calibration metrics for valid shadow reports in the requested window."""
    if days <= 0:
        raise ValueError("days must be a positive integer")

    generated_at = _as_utc(now or datetime.now(timezone.utc))
    cutoff = generated_at - timedelta(days=days)
    warnings: list[str] = []
    runs = _load_runs(history_dir, cutoff=cutoff, warnings=warnings)
    records = _candidate_records(runs)
    latest_feedback, feedback_event_count = _latest_feedback(
        history_dir,
        runs=runs,
        records=records,
        warnings=warnings,
    )

    return {
        "schema_version": "editorial-retrospective-v2",
        "generated_at": generated_at.isoformat(),
        "window": {
            "days": days,
            "starts_at": cutoff.isoformat(),
            "ends_at": generated_at.isoformat(),
        },
        "coverage": _coverage(runs, records, latest_feedback, feedback_event_count),
        "feedback": _feedback_summary(records, latest_feedback),
        "analyst_calibration": _analyst_calibration(records, latest_feedback),
        "editorial_outcomes": _editorial_outcomes(records, latest_feedback),
        "warnings": _sample_warnings(runs, latest_feedback, warnings),
    }


def save_editorial_retrospective(
    report: Mapping[str, Any],
    *,
    output_dir: Path,
    generated_at: datetime | None = None,
) -> tuple[Path, Path]:
    """Atomically save one retrospective as JSON and human-readable Markdown."""
    timestamp = _as_utc(generated_at or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"editorial-retrospective-{timestamp}.json"
    markdown_path = output_dir / f"editorial-retrospective-{timestamp}.md"
    if json_path.exists() or markdown_path.exists():
        raise FileExistsError(f"Retrospective output already exists for timestamp={timestamp}")

    atomic_write_text(str(json_path), json.dumps(report, ensure_ascii=False, indent=2))
    atomic_write_text(str(markdown_path), _render_markdown(report))
    return json_path, markdown_path


def _load_runs(
    history_dir: Path,
    *,
    cutoff: datetime,
    warnings: list[str],
) -> list[dict[str, Any]]:
    if not history_dir.is_dir():
        return []

    runs: list[dict[str, Any]] = []
    for path in sorted(history_dir.glob("shadow-*.json")):
        if path.name.endswith(".feedback.json"):
            continue
        payload = _read_json(path)
        if payload is None:
            warnings.append(f"已跳过无效历史文件: {path.name}")
            continue
        run_id = _string(payload.get("run_id"))
        recorded_at = _parse_datetime(payload.get("generated_at"))
        if not run_id or recorded_at is None:
            warnings.append(f"已跳过缺少运行标识或时间的历史文件: {path.name}")
            continue
        if recorded_at < cutoff:
            continue
        runs.append({"run_id": run_id, "generated_at": recorded_at, "payload": payload})
    return sorted(runs, key=lambda run: run["generated_at"])


def _candidate_records(runs: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for run in runs:
        payload = run["payload"]
        candidates = _items(payload.get("candidates"))
        analyses = {
            _string(item.get("candidate_id")): item
            for item in _items(_mapping(payload.get("analysis")).get("items"))
            if _string(item.get("candidate_id"))
        }
        decisions = {
            _string(item.get("candidate_id")): item
            for item in _items(_mapping(payload.get("editorial")).get("decisions"))
            if _string(item.get("candidate_id"))
        }
        candidate_by_id = {
            _string(item.get("candidate_id")): item
            for item in candidates
            if _string(item.get("candidate_id"))
        }
        all_candidate_ids = set(candidate_by_id) | set(analyses) | set(decisions)
        for candidate_id in all_candidate_ids:
            candidate = candidate_by_id.get(candidate_id, {})
            records[(run["run_id"], candidate_id)] = {
                "run_id": run["run_id"],
                "candidate_id": candidate_id,
                "title": _string(candidate.get("source_title")),
                "source": _string(candidate.get("source")) or "未知来源",
                "analysis": analyses.get(candidate_id, {}),
                "decision": decisions.get(candidate_id, {}),
            }
    return records


def _latest_feedback(
    history_dir: Path,
    *,
    runs: list[dict[str, Any]],
    records: Mapping[tuple[str, str], dict[str, Any]],
    warnings: list[str],
) -> tuple[dict[tuple[str, str], dict[str, Any]], int]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    event_count = 0
    for run in runs:
        path = history_dir / f"{run['run_id']}.feedback.json"
        if not path.is_file():
            continue
        payload = _read_json(path)
        if payload is None:
            warnings.append(f"已跳过无效反馈文件: {path.name}")
            continue
        for event in _items(payload.get("events")):
            candidate_id = _string(event.get("candidate_id"))
            label = _string(event.get("label"))
            key = (run["run_id"], candidate_id)
            if key not in records or label not in _FEEDBACK_LABELS:
                warnings.append(f"已跳过无效反馈事件: {path.name}")
                continue
            event_count += 1
            timestamp = _parse_datetime(event.get("recorded_at")) or datetime.min.replace(tzinfo=timezone.utc)
            previous = latest.get(key)
            previous_timestamp = previous["recorded_at"] if previous else datetime.min.replace(tzinfo=timezone.utc)
            if timestamp >= previous_timestamp:
                latest[key] = {
                    "label": label,
                    "note": _string(event.get("note")).strip(),
                    "recorded_at": timestamp,
                }
    return latest, event_count


def _coverage(
    runs: list[dict[str, Any]],
    records: Mapping[tuple[str, str], dict[str, Any]],
    latest_feedback: Mapping[tuple[str, str], dict[str, Any]],
    feedback_event_count: int,
) -> dict[str, Any]:
    states = Counter(
        _string(_mapping(run["payload"].get("workflow")).get("state"))
        for run in runs
    )
    dates = [run["generated_at"].date().isoformat() for run in runs]
    return {
        "run_count": len(runs),
        "completed_run_count": states["completed"],
        "failed_run_count": states["failed"],
        "candidate_count": len(records),
        "feedback_event_count": feedback_event_count,
        "reviewed_candidate_count": len(latest_feedback),
        "day_count": len(set(dates)),
        "first_run_at": runs[0]["generated_at"].isoformat() if runs else "",
        "last_run_at": runs[-1]["generated_at"].isoformat() if runs else "",
    }


def _feedback_summary(
    records: Mapping[tuple[str, str], dict[str, Any]],
    latest_feedback: Mapping[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    labels = _empty_label_counts()
    by_source: dict[str, dict[str, int]] = {}
    candidate_count_by_source = Counter(record["source"] for record in records.values())
    for key, feedback in latest_feedback.items():
        label = feedback["label"]
        source = records[key]["source"]
        labels[label] += 1
        by_source.setdefault(source, _empty_label_counts())[label] += 1
    source_coverage = []
    for source in sorted(candidate_count_by_source):
        source_labels = by_source.get(source, _empty_label_counts())
        reviewed_count = sum(source_labels.values())
        candidate_count = candidate_count_by_source[source]
        source_coverage.append(
            {
                "source": source,
                "candidate_count": candidate_count,
                "reviewed_candidate_count": reviewed_count,
                "review_coverage_rate": _ratio(reviewed_count, candidate_count),
                "good_topic_count": source_labels["good_topic"],
                "negative_feedback_count": sum(
                    source_labels[label] for label in _NEGATIVE_FEEDBACK_LABELS
                ),
            }
        )

    notes = [
        _sample_record(records[key], feedback)
        for key, feedback in latest_feedback.items()
        if feedback.get("note")
    ]
    notes.sort(key=lambda item: item["recorded_at"], reverse=True)

    return {
        "labels": labels,
        "by_source": {source: by_source[source] for source in sorted(by_source)},
        "source_coverage": source_coverage,
        "notes": notes,
    }


def _analyst_calibration(
    records: Mapping[tuple[str, str], dict[str, Any]],
    latest_feedback: Mapping[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    by_score = {bucket: _empty_label_counts() for bucket in _SCORE_BUCKETS}
    by_risk = {risk: _empty_label_counts() for risk in _RISK_LEVELS}
    for key, feedback in latest_feedback.items():
        analysis = _mapping(records[key]["analysis"])
        score = _number(analysis.get("importance_score"))
        risk = _string(analysis.get("risk_level"))
        by_score[_score_bucket(score)][feedback["label"]] += 1
        if risk in by_risk:
            by_risk[risk][feedback["label"]] += 1

    high_score_misjudgments = [
        _sample_record(records[key], feedback)
        for key, feedback in latest_feedback.items()
        if _number(_mapping(records[key]["analysis"]).get("importance_score"))
        >= _HIGH_IMPORTANCE_THRESHOLD
        and feedback["label"] in _NEGATIVE_FEEDBACK_LABELS
    ]
    high_score_misjudgments.sort(
        key=lambda item: (-item["importance_score"], item["recorded_at"]),
    )

    low_score_missed_opportunities = [
        _sample_record(records[key], feedback)
        for key, feedback in latest_feedback.items()
        if _number(_mapping(records[key]["analysis"]).get("importance_score"))
        < _LOW_IMPORTANCE_THRESHOLD
        and feedback["label"] == "good_topic"
    ]
    low_score_missed_opportunities.sort(
        key=lambda item: (item["importance_score"], item["recorded_at"]),
    )

    return {
        "by_importance_bucket": by_score,
        "by_risk_level": by_risk,
        "high_score_misjudgments": high_score_misjudgments[:_MAX_PRIORITY_SAMPLES],
        "low_score_missed_opportunities": low_score_missed_opportunities[:_MAX_PRIORITY_SAMPLES],
    }


def _editorial_outcomes(
    records: Mapping[tuple[str, str], dict[str, Any]],
    latest_feedback: Mapping[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    by_action = {action: _empty_label_counts() for action in _EDITORIAL_ACTIONS}
    rejection_reasons: Counter[str] = Counter()
    decision_count = 0
    for record in records.values():
        decision = _mapping(record["decision"])
        action = _string(decision.get("action"))
        if action not in by_action:
            continue
        decision_count += 1
        if action == "reject":
            reason = _string(decision.get("reason"))
            if reason:
                rejection_reasons[reason] += 1

    for key, feedback in latest_feedback.items():
        action = _string(_mapping(records[key]["decision"]).get("action"))
        if action in by_action:
            by_action[action][feedback["label"]] += 1

    write_misjudgments = [
        _sample_record(records[key], feedback)
        for key, feedback in latest_feedback.items()
        if _string(_mapping(records[key]["decision"]).get("action")) == "write"
        and feedback["label"] in _NEGATIVE_FEEDBACK_LABELS
    ]
    missed_good_topics = [
        _sample_record(records[key], feedback)
        for key, feedback in latest_feedback.items()
        if _string(_mapping(records[key]["decision"]).get("action"))
        in {"reserve", "reject"}
        and feedback["label"] == "good_topic"
    ]
    write_misjudgments.sort(key=lambda item: item["recorded_at"], reverse=True)
    missed_good_topics.sort(key=lambda item: item["recorded_at"], reverse=True)

    return {
        "decision_count": decision_count,
        "by_action": by_action,
        "write_good_topic_count": by_action["write"]["good_topic"],
        "write_non_good_topic_count": sum(
            by_action["write"][label]
            for label in _FEEDBACK_LABELS
            if label != "good_topic"
        ),
        "missed_good_topic_count": (
            by_action["reserve"]["good_topic"] + by_action["reject"]["good_topic"]
        ),
        "rejection_reasons": [
            {"reason": reason, "count": count}
            for reason, count in sorted(rejection_reasons.items())
        ],
        "write_misjudgments": write_misjudgments[:_MAX_PRIORITY_SAMPLES],
        "missed_good_topics": missed_good_topics[:_MAX_PRIORITY_SAMPLES],
    }


def _sample_record(record: Mapping[str, Any], feedback: Mapping[str, Any]) -> dict[str, Any]:
    analysis = _mapping(record["analysis"])
    decision = _mapping(record["decision"])
    recorded_at = feedback.get("recorded_at")
    return {
        "run_id": _string(record.get("run_id")),
        "candidate_id": _string(record.get("candidate_id")),
        "title": _string(record.get("title")),
        "source": _string(record.get("source")),
        "label": _string(feedback.get("label")),
        "note": _string(feedback.get("note")),
        "recorded_at": recorded_at.isoformat() if isinstance(recorded_at, datetime) else "",
        "importance_score": _number(analysis.get("importance_score")),
        "evidence_score": _number(analysis.get("evidence_score")),
        "risk_level": _string(analysis.get("risk_level")),
        "editorial_action": _string(decision.get("action")),
        "editorial_reason": _string(decision.get("reason")),
    }


def _sample_warnings(
    runs: list[dict[str, Any]],
    latest_feedback: Mapping[tuple[str, str], dict[str, Any]],
    warnings: list[str],
) -> list[str]:
    result = list(warnings)
    if not runs:
        result.append("没有可用的影子运行历史")
    if not latest_feedback:
        result.append("没有人工反馈")
    if len({run["generated_at"].date() for run in runs}) < 3:
        result.append("覆盖天数少于 3 天")
    if len(latest_feedback) < 20:
        result.append("人工反馈少于 20 条")
    return result


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: object) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 3) if denominator else 0.0


def _score_bucket(score: float) -> str:
    if score < 4.0:
        return "0-3.9"
    if score < 7.0:
        return "4-6.9"
    if score < 8.5:
        return "7-8.4"
    return "8.5-10"


def _empty_label_counts() -> dict[str, int]:
    return {label: 0 for label in _FEEDBACK_LABELS}


def _render_markdown(report: Mapping[str, Any]) -> str:
    coverage = _mapping(report.get("coverage"))
    window = _mapping(report.get("window"))
    feedback = _mapping(report.get("feedback"))
    labels = _mapping(feedback.get("labels"))
    source_coverage = _items(feedback.get("source_coverage"))
    notes = _items(feedback.get("notes"))
    calibration = _mapping(report.get("analyst_calibration"))
    high_score_misjudgments = _items(calibration.get("high_score_misjudgments"))
    low_score_missed_opportunities = _items(calibration.get("low_score_missed_opportunities"))
    outcomes = _mapping(report.get("editorial_outcomes"))
    reasons = _items(outcomes.get("rejection_reasons"))
    write_misjudgments = _items(outcomes.get("write_misjudgments"))
    missed_good_topics = _items(outcomes.get("missed_good_topics"))
    warnings = [str(item) for item in report.get("warnings", []) if isinstance(item, str)]

    lines = [
        "# 编辑复盘报告",
        "",
        f"生成时间：{_string(report.get('generated_at'))}",
        f"观察窗口：近 {window.get('days', 0)} 天",
        "",
        "## 覆盖范围",
        "",
        f"- 影子运行：{coverage.get('run_count', 0)} 次（完成 {coverage.get('completed_run_count', 0)}，失败 {coverage.get('failed_run_count', 0)}）",
        f"- 候选：{coverage.get('candidate_count', 0)} 条；已反馈候选：{coverage.get('reviewed_candidate_count', 0)} 条",
        f"- 原始反馈事件：{coverage.get('feedback_event_count', 0)} 条；覆盖 {coverage.get('day_count', 0)} 个自然日",
        "",
        "## 人工反馈",
        "",
        "| 标签 | 数量 |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {label} | {labels.get(label, 0)} |" for label in _FEEDBACK_LABELS)
    lines.extend(["", "## 来源表现", ""])
    if source_coverage:
        lines.extend([
            "| 来源 | 候选 | 已审核 | 覆盖率 | 值得写 | 负反馈 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ])
        lines.extend(
            "| {source} | {candidate_count} | {reviewed_candidate_count} | {coverage:.1%} | {good_topic_count} | {negative_feedback_count} |".format(
                source=_markdown_cell(item.get("source")),
                candidate_count=item.get("candidate_count", 0),
                reviewed_candidate_count=item.get("reviewed_candidate_count", 0),
                coverage=float(item.get("review_coverage_rate", 0.0)),
                good_topic_count=item.get("good_topic_count", 0),
                negative_feedback_count=item.get("negative_feedback_count", 0),
            )
            for item in source_coverage
        )
    else:
        lines.append("- 无")

    lines.extend(["", "## Analyst 偏差", "", "### 高分但被否定", ""])
    lines.extend(_render_sample_lines(high_score_misjudgments) or ["- 无"])
    lines.extend(["", "### 低分但被认可", ""])
    lines.extend(_render_sample_lines(low_score_missed_opportunities) or ["- 无"])
    lines.extend(
        [
            "",
            "## 编辑结果",
            "",
            f"- 入选且被认可：{outcomes.get('write_good_topic_count', 0)}",
            f"- 入选但被否定：{outcomes.get('write_non_good_topic_count', 0)}",
            f"- 候补/拒绝中被认可的遗漏机会：{outcomes.get('missed_good_topic_count', 0)}",
            "",
            "## 拒绝原因",
            "",
        ]
    )
    if reasons:
        lines.extend(f"- {item.get('reason', '')}：{item.get('count', 0)}" for item in reasons)
    else:
        lines.append("- 无")
    lines.extend(["", "## Editorial 偏差", "", "### 入选但被否定", ""])
    lines.extend(_render_sample_lines(write_misjudgments) or ["- 无"])
    lines.extend(["", "### 候补/拒绝但被认可", ""])
    lines.extend(_render_sample_lines(missed_good_topics) or ["- 无"])
    lines.extend(["", "## 人工备注", ""])
    lines.extend(_render_sample_lines(notes) or ["- 无"])
    lines.extend(["", "## 样本提示", ""])
    lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines) + "\n"


def _render_sample_lines(samples: list[Mapping[str, Any]]) -> list[str]:
    return [
        "- [{label}] {title}（{source}；重要性 {importance_score:g}；编辑动作 {editorial_action}）{note}".format(
            label=_markdown_cell(item.get("label")),
            title=_markdown_cell(item.get("title")) or "未命名候选",
            source=_markdown_cell(item.get("source")) or "未知来源",
            importance_score=_number(item.get("importance_score")),
            editorial_action=_markdown_cell(item.get("editorial_action")) or "-",
            note=f"：{_markdown_cell(item.get('note'))}" if item.get("note") else "",
        )
        for item in samples
    ]


def _markdown_cell(value: object) -> str:
    return _string(value).replace("|", r"\|").replace("\n", " ")
