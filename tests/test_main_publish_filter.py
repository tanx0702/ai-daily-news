import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import main as daily_main
from src.briefing.builder import BriefBuilder as RealBriefBuilder
from src.briefing.clusterer import EventClusterer as RealEventClusterer
from src.briefing.deduplicator import (
    AcceptedItemDeduplicator as RealAcceptedItemDeduplicator,
)
from src.briefing.semantic_reviewer import (
    SemanticDuplicateReviewer as RealSemanticDuplicateReviewer,
)


NOW = datetime(2026, 8, 7, 12, tzinfo=timezone.utc)


def _candidate(index: int) -> dict:
    titles = (
        "星河科技发布中文推理模型",
        "量子实验室公开视觉研究数据集",
        "云端公司上线代码助手功能",
        "大学团队发表机器人控制论文",
        "芯片企业推出人工智能加速卡",
        "开发平台新增模型评测工具",
    )
    title = titles[index - 1]
    return {
        "id": f"candidate-{index}",
        "title": title,
        "summary": f"{title}。该来源介绍了对应的技术更新。",
        "url": f"https://example{index}.com/news/{index}",
        "source": f"示例来源 {index}",
        "source_type": "rss",
        "source_tier": "primary",
        "published_at": datetime(2026, 8, 7, 8, index, tzinfo=timezone.utc),
        "_score": 100 - index,
    }


def _env(*, top_n: int = 5, skip: bool = True) -> dict[str, str]:
    return {
        "DAILY_MIN_ITEMS": "5",
        "DAILY_TOP_N": str(top_n),
        "DAILY_CANDIDATE_POOL_N": "45",
        "DAILY_X_MAX_ITEMS": "5",
        "X_FEED_MAX_AGE_HOURS": "6",
        "DAILY_NEWS_HOURS": "36",
        "SKIP_WECHAT_DRAFT": "1" if skip else "0",
        "ENABLE_ARTICLE_IMAGE_FETCH": "0",
        "LLM_API_KEY": "",
        "AGNES_API_KEY": "",
        "OPENAI_API_KEY": "",
        "QUALITY_LLM_API_KEY": "",
        "IMAGE_API_KEY": "",
        "PAGES_URL": "https://example.test",
    }


def _run_with_candidates(tmp_path: Path, candidates: list[dict], env: dict):
    with (
        patch.dict("os.environ", env, clear=True),
        patch("src.collector.collect_candidates", return_value=candidates) as collect,
        patch("src.cover.generate_cover_from_news", return_value=str(tmp_path / "cover.jpg")),
        patch("src.services.production_snapshot.save_production_snapshot"),
    ):
        result = daily_main._run_pipeline(docs_dir=str(tmp_path), now=NOW)
    return result, collect


def test_invalid_configuration_fails_before_any_external_or_render_call(tmp_path):
    env = _env(top_n=21)
    with (
        patch.dict("os.environ", env, clear=True),
        patch("src.collector.collect_candidates") as collect,
        patch("src.briefing.builder.BriefBuilder") as builder,
        patch("src.pipeline_artifacts.render_and_save_daily_html") as render,
        patch("src.wechat_draft.publish_daily_article") as publish,
    ):
        result = daily_main._run_pipeline(docs_dir=str(tmp_path), now=NOW)

    assert result["status"] == "failed"
    assert result["draft_decision"] is None
    assert result["draft_execution"]["reason"] == "invalid_configuration"
    collect.assert_not_called()
    builder.assert_not_called()
    render.assert_not_called()
    publish.assert_not_called()


def test_five_valid_items_create_a_dry_run_and_write_schema_v2(tmp_path):
    result, collect = _run_with_candidates(
        tmp_path,
        [_candidate(index) for index in range(1, 6)],
        _env(),
    )

    assert result["status"] == "dry_run"
    assert result["draft_decision"]["action"] == "create"
    assert len(result["brief_items"]) == 5
    assert collect.call_args.kwargs["limit"] == 45
    payload = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["draft_execution"]["status"] == "dry_run"
    assert len(payload["brief_items"]) == 5
    assert "publication" not in payload
    assert "quality_gate" not in payload
    assert "candidate_audit" not in payload
    debug_payload = json.loads(
        (tmp_path / "debug" / "2026-08-07-briefing.json").read_text(encoding="utf-8")
    )
    assert len(debug_payload["candidate_audit"]) == 5
    assert debug_payload["candidate_audit"][0]["event"]["canonical_evidence"]["evidence_text"]


def test_pipeline_passes_90_second_default_timeout_to_brief_builder(tmp_path):
    env = _env()
    with (
        patch.dict("os.environ", env, clear=True),
        patch(
            "src.collector.collect_candidates",
            return_value=[_candidate(index) for index in range(1, 6)],
        ),
        patch("src.cover.generate_cover_from_news", return_value=str(tmp_path / "cover.jpg")),
        patch("src.services.production_snapshot.save_production_snapshot"),
        patch("src.briefing.builder.BriefBuilder", wraps=RealBriefBuilder) as builder,
    ):
        daily_main._run_pipeline(docs_dir=str(tmp_path), now=NOW)

    assert builder.call_args.kwargs["timeout"] == 90


def test_pipeline_injects_bounded_semantic_reviewer_into_clusterer(tmp_path):
    env = {
        **_env(),
        "QUALITY_GATE_TIMEOUT": "33",
        "SEMANTIC_DEDUP_TIMEOUT": "17",
        "SEMANTIC_DEDUP_MAX_LLM_CALLS": "7",
    }
    with (
        patch.dict("os.environ", env, clear=True),
        patch(
            "src.collector.collect_candidates",
            return_value=[_candidate(index) for index in range(1, 6)],
        ),
        patch("src.cover.generate_cover_from_news", return_value=str(tmp_path / "cover.jpg")),
        patch("src.services.production_snapshot.save_production_snapshot"),
        patch(
            "src.briefing.semantic_reviewer.SemanticDuplicateReviewer",
            wraps=RealSemanticDuplicateReviewer,
        ) as reviewer,
        patch(
            "src.briefing.clusterer.EventClusterer",
            wraps=RealEventClusterer,
        ) as clusterer,
        patch(
            "src.briefing.deduplicator.AcceptedItemDeduplicator",
            wraps=RealAcceptedItemDeduplicator,
        ) as deduplicator,
    ):
        daily_main._run_pipeline(docs_dir=str(tmp_path), now=NOW)

    assert reviewer.call_args.kwargs["timeout"] == 17
    assert reviewer.call_args.kwargs["max_calls"] == 7
    assert clusterer.call_args.args[0].semantic_dedup_window_hours == 48
    assert isinstance(
        clusterer.call_args.kwargs["reviewer"],
        RealSemanticDuplicateReviewer,
    )
    assert deduplicator.call_args.args[0].semantic_dedup_window_hours == 48
    assert deduplicator.call_args.kwargs["reviewer"] is (
        clusterer.call_args.kwargs["reviewer"]
    )


def test_four_valid_items_block_but_still_write_local_artifacts(tmp_path):
    with patch("src.wechat_draft.publish_daily_article") as publish:
        result, _ = _run_with_candidates(
            tmp_path,
            [_candidate(index) for index in range(1, 5)],
            _env(skip=False),
        )

    assert result["status"] == "blocked"
    assert result["draft_decision"]["reasons"] == ["insufficient_items"]
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "wechat.html").exists()
    assert (tmp_path / "latest.json").exists()
    publish.assert_not_called()


def test_only_create_decision_calls_wechat_with_deterministic_html(tmp_path):
    env = _env(skip=False)
    with (
        patch.dict("os.environ", env, clear=True),
        patch(
            "src.collector.collect_candidates",
            return_value=[_candidate(index) for index in range(1, 6)],
        ),
        patch("src.cover.generate_cover_from_news", return_value=str(tmp_path / "cover.jpg")),
        patch("src.services.production_snapshot.save_production_snapshot"),
        patch(
            "src.wechat_draft.publish_daily_article",
            return_value={"status": "draft_created", "media_id": "media-1"},
        ) as publish,
    ):
        result = daily_main._run_pipeline(docs_dir=str(tmp_path), now=NOW)

    assert result["status"] == "draft_created"
    assert result["draft_decision"]["action"] == "create"
    assert result["draft_execution"]["media_id"] == "media-1"
    assert "星河科技发布中文推理模型" in publish.call_args.kwargs["rendered_content"]


def test_wechat_receives_resolved_media_items_for_post_upload_rendering(tmp_path):
    env = {**_env(skip=False), "ENABLE_ARTICLE_IMAGE_FETCH": "1"}

    def resolved_media(items, **_kwargs):
        from src.briefing.adapters import brief_item_to_display_dict

        display_items = [brief_item_to_display_dict(item) for item in items]
        display_items[0].update({
            "media_state": "trusted",
            "normalized_image_path": str(tmp_path / "article.jpg"),
            "article_image_url": "https://example.test/article.jpg",
            "image_type": "original",
        })
        return display_items, {
            "total": len(items),
            "with_original_image": 1,
            "text_only": len(items) - 1,
        }

    with (
        patch.dict("os.environ", env, clear=True),
        patch(
            "src.collector.collect_candidates",
            return_value=[_candidate(index) for index in range(1, 6)],
        ),
        patch("src.media_assets.resolve_article_media", side_effect=resolved_media),
        patch("src.cover.generate_cover_from_news", return_value=str(tmp_path / "cover.jpg")),
        patch("src.services.production_snapshot.save_production_snapshot"),
        patch(
            "src.wechat_draft.publish_daily_article",
            return_value={"status": "draft_created", "media_id": "media-1"},
        ) as publish,
    ):
        result = daily_main._run_pipeline(docs_dir=str(tmp_path), now=NOW)

    assert result["status"] == "draft_created"
    published_items = publish.call_args.args[0]
    assert published_items[0]["media_state"] == "trusted"
    assert published_items[0]["normalized_image_path"] == str(tmp_path / "article.jpg")


def test_wechat_failure_does_not_change_the_create_decision(tmp_path):
    env = _env(skip=False)
    with (
        patch.dict("os.environ", env, clear=True),
        patch(
            "src.collector.collect_candidates",
            return_value=[_candidate(index) for index in range(1, 6)],
        ),
        patch("src.cover.generate_cover_from_news", return_value=str(tmp_path / "cover.jpg")),
        patch("src.services.production_snapshot.save_production_snapshot"),
        patch(
            "src.wechat_draft.publish_daily_article",
            return_value={"status": "failed", "reason": "all_retries_exhausted"},
        ),
    ):
        result = daily_main._run_pipeline(docs_dir=str(tmp_path), now=NOW)

    assert result["status"] == "failed"
    assert result["draft_decision"]["action"] == "create"
    assert result["draft_execution"]["reason"] == "wechat_draft_failed"


def test_critical_artifact_failure_prevents_wechat_call(tmp_path):
    env = _env(skip=False)
    with (
        patch.dict("os.environ", env, clear=True),
        patch(
            "src.collector.collect_candidates",
            return_value=[_candidate(index) for index in range(1, 6)],
        ),
        patch(
            "src.pipeline_artifacts.render_and_save_daily_html",
            side_effect=OSError("disk full"),
        ),
        patch("src.services.production_snapshot.save_production_snapshot"),
        patch("src.wechat_draft.publish_daily_article") as publish,
    ):
        result = daily_main._run_pipeline(docs_dir=str(tmp_path), now=NOW)

    assert result["status"] == "failed"
    assert result["draft_decision"]["action"] == "create"
    assert result["draft_execution"]["reason"] == "artifact_write_failed"
    publish.assert_not_called()
    debug_payload = json.loads(
        (tmp_path / "debug" / "2026-08-07-briefing.json").read_text(encoding="utf-8")
    )
    assert len(debug_payload["candidate_audit"]) == 5


def test_initial_latest_write_failure_prevents_wechat_call(tmp_path):
    env = _env(skip=False)
    with (
        patch.dict("os.environ", env, clear=True),
        patch(
            "src.collector.collect_candidates",
            return_value=[_candidate(index) for index in range(1, 6)],
        ),
        patch(
            "src.cover.generate_cover_from_news",
            return_value=str(tmp_path / "cover.jpg"),
        ),
        patch("src.services.production_snapshot.save_production_snapshot"),
        patch(
            "src.pipeline_artifacts.save_latest_data",
            side_effect=OSError("disk full"),
        ),
        patch("src.wechat_draft.publish_daily_article") as publish,
    ):
        result = daily_main._run_pipeline(docs_dir=str(tmp_path), now=NOW)

    assert result["status"] == "failed"
    assert result["draft_decision"]["action"] == "create"
    assert result["draft_execution"]["reason"] == "artifact_write_failed"
    publish.assert_not_called()


def test_pipeline_exit_code_keeps_blocked_and_failed_visible_to_cron():
    assert daily_main._pipeline_exit_code({"status": "draft_created"}) == 0
    assert daily_main._pipeline_exit_code({"status": "dry_run"}) == 0
    assert daily_main._pipeline_exit_code({"status": "blocked"}) == 1
    assert daily_main._pipeline_exit_code({"status": "failed"}) == 1
