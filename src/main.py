"""Production entry point for the evidence-bound AI fact-brief pipeline."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from dotenv import load_dotenv


load_dotenv()

from src.logger_config import setup_logging


setup_logging()
logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _pipeline_exit_code(result: Mapping[str, Any]) -> int:
    """Keep blocked and failed runs visible to cron and existing log monitors."""
    return 0 if result.get("status") in {"draft_created", "dry_run"} else 1


def main() -> None:
    docs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "docs"))
    lock_path = os.environ.get(
        "DAILY_RUN_LOCK_PATH",
        os.path.join(docs_dir, ".daily_run.lock"),
    )
    lock_ttl = int(os.environ.get("DAILY_RUN_LOCK_TTL_SECONDS", str(6 * 60 * 60)))

    from src.run_guard import RunLockError, single_run_lock

    try:
        with single_run_lock(lock_path, ttl_seconds=lock_ttl):
            result = _run_pipeline(docs_dir=docs_dir)
    except RunLockError as exc:
        logger.warning("Another daily run appears active, skipping: %s", exc)
        raise SystemExit(2) from exc

    exit_code = _pipeline_exit_code(result)
    if exit_code:
        logger.error("Daily fact-brief pipeline ended without a draft: %s", result)
        raise SystemExit(exit_code)


def _run_pipeline(
    *,
    docs_dir: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one edition without allowing diagnostics to alter its decision."""
    from src.briefing.config import BriefingConfig, InvalidBriefingConfiguration
    from src.briefing.decision import failed_execution
    from src.briefing.latest import build_latest_v2
    from src.pipeline_artifacts import save_latest_data

    started_at = _aware_utc(now)
    output_dir = os.path.abspath(
        docs_dir or os.path.join(os.path.dirname(__file__), "..", "docs")
    )

    try:
        config = BriefingConfig.from_env()
    except InvalidBriefingConfiguration as exc:
        logger.error("Briefing preflight failed: %s", exc)
        execution = failed_execution("invalid_configuration", now=started_at)
        diagnostics = {"configuration_error": str(exc)}
        payload = build_latest_v2([], None, execution, diagnostics=diagnostics)
        try:
            save_latest_data(payload, output_dir)
        except Exception:
            logger.exception("Could not persist the invalid-configuration result")
        return _result((), None, execution)

    from src.time_utils import report_date_str

    date_str = report_date_str(started_at)
    pages_url = os.environ.get(
        "PAGES_URL",
        f"https://{os.environ.get('DOMAIN', 'tankex.xyz')}",
    ).rstrip("/")
    logger.info("AI fact brief pipeline started for %s", date_str)

    collection_diagnostics: dict[str, Any] = {}
    try:
        from src.collector import collect_candidates

        candidates = collect_candidates(
            hours=config.news_hours,
            limit=config.candidate_pool_size,
            rss_timeout=int(os.environ.get("DAILY_RSS_TIMEOUT", "30")),
            diagnostics=collection_diagnostics,
            now=started_at,
        )
    except Exception as exc:
        logger.exception("Candidate collection failed; continuing with an empty pool")
        candidates = []
        collection_diagnostics["collection_error"] = type(exc).__name__

    _save_shadow_snapshot(
        candidates,
        date_str=date_str,
        docs_dir=output_dir,
        collection_diagnostics=collection_diagnostics,
    )

    from src.briefing.clusterer import EventClusterer
    from src.briefing.evidence import source_evidence_from_candidate
    from src.briefing.semantic_reviewer import SemanticDuplicateReviewer
    from src.llm_config import resolve_quality_llm_config

    evidence = []
    scores_by_url: dict[str, float] = {}
    invalid_evidence_count = 0
    for candidate in candidates:
        source = source_evidence_from_candidate(
            candidate,
            trusted_x_collector=(
                str(candidate.get("source_type") or "").strip().lower() == "x"
            ),
        )
        if source is None:
            invalid_evidence_count += 1
            continue
        evidence.append(source)
        scores_by_url[source.url] = max(
            scores_by_url.get(source.url, float("-inf")),
            _candidate_score(candidate),
        )

    quality_llm_config = resolve_quality_llm_config()
    semantic_reviewer = SemanticDuplicateReviewer(
        quality_llm_config,
        timeout=config.semantic_dedup_timeout,
        max_calls=config.semantic_dedup_max_llm_calls,
    )
    clustered = EventClusterer(
        config,
        reviewer=semantic_reviewer,
    ).cluster(evidence, editorial_scores=scores_by_url)

    from src.briefing.builder import BriefBuilder
    from src.briefing.deduplicator import AcceptedItemDeduplicator
    from src.briefing.pipeline import run_brief_pipeline
    from src.briefing.validator import BriefValidator
    from src.llm_config import resolve_text_llm_config

    builder = BriefBuilder(
        config,
        resolve_text_llm_config(),
        timeout=int(os.environ.get("DAILY_LLM_TIMEOUT", "90")),
    )
    validator = BriefValidator(
        config,
        quality_llm_config,
        timeout=int(os.environ.get("QUALITY_GATE_TIMEOUT", "45")),
    )
    semantic_deduplicator = AcceptedItemDeduplicator(
        config,
        reviewer=semantic_reviewer,
    )
    briefing = run_brief_pipeline(
        clustered.events,
        clustered.quarantined,
        config,
        builder,
        validator,
        now=started_at,
        semantic_deduplicator=semantic_deduplicator,
        clustered_duplicates=clustered.merged_duplicates,
    )
    items = briefing.accepted_items
    decision = briefing.decision

    diagnostics: dict[str, Any] = {
        "collection": collection_diagnostics,
        "clustering": {
            **dict(clustered.diagnostics),
            "invalid_evidence_count": invalid_evidence_count,
        },
        "selection": {
            "excluded_counts": dict(briefing.exclusions),
            **dict(briefing.diagnostics),
        },
    }

    try:
        display_items, rendered_content = _render_artifacts(
            items,
            date_str=date_str,
            docs_dir=output_dir,
            pages_url=pages_url,
            diagnostics=diagnostics,
        )
    except Exception:
        logger.exception("Critical fact-brief artifact generation failed")
        execution = _execution(
            "failed",
            "artifact_write_failed",
            started_at,
            _aware_utc(now),
        )
        diagnostics["content_fingerprint"] = _content_fingerprint(items)
        _persist_latest(items, decision, execution, diagnostics, output_dir)
        _save_debug_report(
            date_str=date_str,
            docs_dir=output_dir,
            diagnostics=diagnostics,
            decision=decision.to_dict(),
            execution=execution.to_dict(),
            candidate_audit=briefing.audit_entries,
        )
        return _result(items, decision, execution)

    diagnostics["content_fingerprint"] = _content_fingerprint(display_items)
    diagnostics["final_x_count"] = decision.x_count

    if decision.action == "block":
        execution = _execution(
            "blocked",
            ",".join(decision.reasons) or "invalid_final_item",
            started_at,
            _aware_utc(now),
        )
    elif config.skip_wechat_draft:
        execution = _execution("dry_run", None, started_at, _aware_utc(now))
    else:
        # Persist a conservative pre-WeChat snapshot so a critical local write
        # failure cannot occur only after the remote draft has been created.
        diagnostics["execution_phase"] = "pre_wechat"
        pre_wechat_execution = _execution(
            "failed",
            "wechat_draft_failed",
            started_at,
            _aware_utc(now),
        )
        if not _persist_latest(
            items,
            decision,
            pre_wechat_execution,
            diagnostics,
            output_dir,
        ):
            execution = _execution(
                "failed",
                "artifact_write_failed",
                started_at,
                _aware_utc(now),
            )
        else:
            execution = _create_wechat_draft(
                items,
                date_str=date_str,
                pages_url=pages_url,
                docs_dir=output_dir,
                rendered_content=rendered_content,
                started_at=started_at,
                now=now,
            )
        diagnostics.pop("execution_phase", None)

    if not _persist_latest(items, decision, execution, diagnostics, output_dir):
        execution = _execution(
            "failed",
            "artifact_write_failed",
            started_at,
            _aware_utc(now),
        )
        _persist_latest(items, decision, execution, diagnostics, output_dir)

    _save_debug_report(
        date_str=date_str,
        docs_dir=output_dir,
        diagnostics=diagnostics,
        decision=decision.to_dict(),
        execution=execution.to_dict(),
        candidate_audit=briefing.audit_entries,
    )
    logger.info(
        "Fact brief finished: action=%s status=%s selected=%d",
        decision.action,
        execution.status,
        len(items),
    )
    return _result(items, decision, execution)


def _render_artifacts(
    items,
    *,
    date_str: str,
    docs_dir: str,
    pages_url: str,
    diagnostics: dict[str, Any],
):
    from src.briefing.adapters import brief_item_to_display_dict, content_fingerprint
    from src.generator import render_wechat_article
    from src.pipeline_artifacts import (
        render_and_save_daily_html,
        render_and_save_wechat_preview,
    )

    before = content_fingerprint(items)
    if _env_bool("ENABLE_ARTICLE_IMAGE_FETCH", True) and items:
        from src.media_assets import resolve_article_media

        display_items, media_report = resolve_article_media(
            items,
            docs_dir=docs_dir,
            pages_url=pages_url,
            date_str=date_str,
            timeout=int(os.environ.get("ARTICLE_IMAGE_TIMEOUT", "8")),
        )
    else:
        display_items = [brief_item_to_display_dict(item) for item in items]
        media_report = {
            "total": len(items),
            "with_original_image": 0,
            "text_only": len(items),
        }
    after = content_fingerprint(display_items)
    if before != after:
        raise ValueError("public content changed during media adaptation")

    render_and_save_daily_html(
        display_items,
        date_str,
        docs_dir,
        pages_url,
        github_repo=os.environ.get("GITHUB_REPO", "tankex/ai-daily-news"),
    )

    from src.cover import generate_cover_from_news, select_cover_subject
    from src.llm_config import resolve_image_llm_config

    cover_subject = select_cover_subject(display_items)
    cover_title = items[0].chinese_title if items else "今日AI要闻"
    image_config = resolve_image_llm_config()
    generate_cover_from_news(
        display_items,
        date_str,
        output_path=os.path.join(docs_dir, "cover.jpg"),
        api_key=image_config.api_key,
        base_url=image_config.base_url,
        model=image_config.model,
        cover_title=cover_title,
        cover_subject=cover_subject,
    )

    render_and_save_wechat_preview(display_items, date_str, docs_dir, pages_url)
    rendered_content = render_wechat_article(
        display_items,
        date_str=date_str,
        pages_url=pages_url,
        cover_image_url=f"{pages_url}/cover.jpg",
    )
    diagnostics["media"] = media_report
    diagnostics["content_fingerprint_before_media"] = before
    diagnostics["content_fingerprint_after_media"] = after
    diagnostics["cover"] = {
        key: value for key, value in cover_subject.items() if key != "item"
    }
    return display_items, rendered_content


def _create_wechat_draft(
    items,
    *,
    date_str: str,
    pages_url: str,
    docs_dir: str,
    rendered_content: str,
    started_at: datetime,
    now: datetime | None,
):
    from src.wechat_draft import publish_daily_article

    try:
        result = publish_daily_article(
            items,
            date_str,
            pages_url,
            cover_path=os.path.join(docs_dir, "cover.jpg"),
            rendered_content=rendered_content,
        )
    except Exception:
        logger.exception("WeChat draft boundary raised an exception")
        result = {"status": "failed"}

    if result.get("status") == "draft_created" and result.get("media_id"):
        return _execution(
            "draft_created",
            None,
            started_at,
            _aware_utc(now),
            media_id=str(result["media_id"]),
        )
    return _execution(
        "failed",
        "wechat_draft_failed",
        started_at,
        _aware_utc(now),
    )


def _persist_latest(items, decision, execution, diagnostics, docs_dir: str) -> bool:
    from src.briefing.latest import build_latest_v2
    from src.pipeline_artifacts import save_latest_data

    try:
        payload = build_latest_v2(
            items,
            decision,
            execution,
            diagnostics=diagnostics,
        )
        save_latest_data(payload, docs_dir)
        return True
    except Exception:
        logger.exception("Could not persist latest.json schema v2")
        return False


def _save_shadow_snapshot(
    candidates: list[dict],
    *,
    date_str: str,
    docs_dir: str,
    collection_diagnostics: Mapping[str, Any],
) -> None:
    """Keep shadow input diagnostic-only and ignore all returned editorial state."""
    try:
        from src.services.production_snapshot import save_production_snapshot

        save_production_snapshot(
            candidates,
            date_str=date_str,
            snapshot_dir=os.path.join(docs_dir, "debug", "shadow"),
            collection_diagnostics=collection_diagnostics,
        )
    except Exception:
        logger.warning("Production candidate snapshot was not saved", exc_info=True)


def _save_debug_report(
    *,
    date_str: str,
    docs_dir: str,
    diagnostics: Mapping[str, Any],
    decision: Mapping[str, Any],
    execution: Mapping[str, Any],
    candidate_audit: Sequence[Mapping[str, object]],
) -> None:
    from src.file_utils import atomic_write_text

    payload = {
        "date": date_str,
        "diagnostics": diagnostics,
        "draft_decision": decision,
        "draft_execution": execution,
        "candidate_audit": list(candidate_audit),
    }
    path = Path(docs_dir) / "debug" / f"{date_str}-briefing.json"
    try:
        atomic_write_text(
            str(path),
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        )
    except Exception:
        logger.warning("Briefing debug report was not saved", exc_info=True)


def _candidate_score(candidate: Mapping[str, Any]) -> float:
    raw = candidate.get("_score")
    if raw is None and isinstance(candidate.get("scores"), Mapping):
        raw = candidate["scores"].get("final")
    try:
        return float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _content_fingerprint(items) -> str:
    from src.briefing.adapters import content_fingerprint

    return content_fingerprint(items)


def _aware_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("pipeline timestamps must include a timezone")
    return current.astimezone(timezone.utc)


def _execution(
    status: str,
    reason: str | None,
    started_at: datetime,
    completed_at: datetime,
    *,
    media_id: str | None = None,
):
    from src.briefing.models import DraftExecution

    return DraftExecution(
        status=status,
        reason=reason,
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
        media_id=media_id,
    )


def _result(items, decision, execution) -> dict[str, Any]:
    return {
        "status": execution.status,
        "brief_items": [item.to_dict() for item in items],
        "draft_decision": decision.to_dict() if decision else None,
        "draft_execution": execution.to_dict(),
    }


if __name__ == "__main__":
    main()
