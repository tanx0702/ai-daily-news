import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import main as daily_main
from src.briefing.adapters import content_fingerprint
from src.briefing.latest import load_latest


FIXED_NOW = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "fact_brief_candidates.json"


def _environment() -> dict[str, str]:
    return {
        "DAILY_MIN_ITEMS": "5",
        "DAILY_TOP_N": "15",
        "DAILY_CANDIDATE_POOL_N": "45",
        "DAILY_X_MAX_ITEMS": "5",
        "X_FEED_MAX_AGE_HOURS": "6",
        "DAILY_NEWS_HOURS": "36",
        "SKIP_WECHAT_DRAFT": "1",
        "ENABLE_ARTICLE_IMAGE_FETCH": "0",
        "LLM_API_KEY": "",
        "QUALITY_LLM_API_KEY": "",
        "IMAGE_API_KEY": "",
        "PAGES_URL": "https://example.test",
    }


def test_frozen_candidates_create_a_unique_evidence_bound_dry_run(tmp_path):
    candidates = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(candidates) >= 18
    assert sum(item["source_type"] == "x" for item in candidates) > 5

    with (
        patch.dict("os.environ", _environment(), clear=True),
        patch("src.collector.collect_candidates", return_value=candidates),
        patch(
            "src.cover.generate_cover_from_news",
            return_value=str(tmp_path / "cover.jpg"),
        ),
        patch("src.services.production_snapshot.save_production_snapshot"),
        patch("src.wechat_draft.publish_daily_article") as publish,
        patch("requests.get", side_effect=AssertionError("unexpected network GET")),
        patch("requests.post", side_effect=AssertionError("unexpected network POST")),
    ):
        result = daily_main._run_pipeline(docs_dir=str(tmp_path), now=FIXED_NOW)

    publish.assert_not_called()
    assert result["status"] == "dry_run"
    assert result["draft_decision"]["action"] == "create"
    assert 5 <= len(result["brief_items"]) <= 15
    assert result["draft_decision"]["x_count"] <= 5
    event_keys = [item["event_key"] for item in result["brief_items"]]
    assert len(event_keys) == len(set(event_keys))
    assert all(item["evidence_bindings"] for item in result["brief_items"])

    snapshot = load_latest(tmp_path / "latest.json")
    selection = snapshot.diagnostics["selection"]
    assert selection["excluded_counts"]["semantic_duplicate_unresolved"] >= 1
    assert selection["excluded_counts"]["content_llm_unavailable"] >= 1
    assert "unsupported_claim" not in selection["excluded_counts"]
    assert selection["excluded_counts"]["x_limit"] >= 1
    assert selection["rules_only_count"] == 15
    assert selection["rules_and_llm_count"] == 0
    assert selection["build_attempt_count"] >= len(snapshot.brief_items)
    assert snapshot.diagnostics["content_fingerprint_before_media"] == snapshot.diagnostics[
        "content_fingerprint_after_media"
    ]
    assert snapshot.diagnostics["content_fingerprint"] == content_fingerprint(
        snapshot.brief_items
    )

    index_html = (tmp_path / "index.html").read_text(encoding="utf-8")
    wechat_html = (tmp_path / "wechat.html").read_text(encoding="utf-8")
    for item in snapshot.brief_items:
        assert item.chinese_title in index_html and item.chinese_title in wechat_html
        assert item.canonical_source.url in index_html
        assert item.canonical_source.url in wechat_html
