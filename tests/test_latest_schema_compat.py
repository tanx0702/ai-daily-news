import json
import logging

from src.briefing.latest import build_latest_v2, load_latest
from src.briefing.models import (
    BriefItem,
    DraftDecision,
    DraftExecution,
    EvidenceBinding,
    SourceEvidence,
)


def _brief_item() -> BriefItem:
    source = SourceEvidence(
        publisher_id="example",
        publisher_name="Example",
        channel="rss",
        authority="official",
        is_official=True,
        official_identity_source="rss_source_config",
        source_title="Example launches a model",
        evidence_text="Example launches a model with a documented API.",
        url="https://example.com/model",
        published_at="2026-08-07T08:00:00+00:00",
    )
    return BriefItem(
        event_key="example-model",
        chinese_title="Example 发布模型",
        brief="Example 发布了一款模型。",
        canonical_source=source,
        related_sources=(),
        published_at="2026-08-07T08:00:00+00:00",
        evidence_bindings=(
            EvidenceBinding(
                claim="Example 发布模型",
                source_quote="Example launches a model",
                source_url=source.url,
            ),
        ),
        content_origin="llm",
        validation_mode="rules_only",
    )


def _decision() -> DraftDecision:
    return DraftDecision("create", 5, 5, 15, 0, 5)


def _execution() -> DraftExecution:
    return DraftExecution(
        "dry_run",
        None,
        "2026-08-07T08:00:00+00:00",
        "2026-08-07T08:01:00+00:00",
    )


def test_build_latest_v2_writes_only_fact_brief_schema_fields():
    payload = build_latest_v2(
        [_brief_item()],
        _decision(),
        _execution(),
        diagnostics={"rules_only_count": 1},
    )

    assert payload["schema_version"] == 2
    assert set(payload) == {
        "schema_version",
        "brief_items",
        "draft_decision",
        "draft_execution",
        "diagnostics",
    }
    assert payload["brief_items"][0]["event_key"] == "example-model"
    assert "news" not in payload
    assert "quality_gate" not in payload
    assert "publication" not in payload


def test_build_latest_v2_returns_json_safe_nested_diagnostics():
    payload = build_latest_v2(
        [_brief_item()],
        _decision(),
        _execution(),
        diagnostics={"collection": {"source_counts": [1, 2]}},
    )

    json.dumps(payload)
    assert payload["diagnostics"]["collection"]["source_counts"] == [1, 2]


def test_load_latest_round_trips_v2_contract(tmp_path):
    payload = build_latest_v2([_brief_item()], _decision(), _execution())
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    snapshot = load_latest(path)

    assert snapshot.schema_version == 2
    assert snapshot.brief_items[0].event_key == "example-model"
    assert snapshot.brief_items[0].chinese_title == "Example 发布模型"
    assert snapshot.brief_items[0].canonical_source.url == "https://example.com/model"
    assert snapshot.draft_decision == _decision()
    assert snapshot.draft_execution == _execution()
    assert snapshot.legacy_news == ()


def test_load_latest_adapts_unversioned_v1_read_only_and_logs_deprecation(
    tmp_path, caplog
):
    path = tmp_path / "latest.json"
    path.write_text(
        json.dumps({"date": "2026-08-07", "news": [{"title": "Legacy"}]}),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        snapshot = load_latest(path)

    assert snapshot.schema_version == 1
    assert snapshot.brief_items == ()
    assert snapshot.draft_decision is None
    assert snapshot.draft_execution is None
    assert snapshot.legacy_news[0]["title"] == "Legacy"
    assert "deprecated" in caplog.text.lower()


def test_v2_allows_null_decision_only_for_preflight_failure(tmp_path):
    execution = DraftExecution(
        "failed",
        "invalid_configuration",
        "2026-08-07T08:00:00+00:00",
        "2026-08-07T08:00:00+00:00",
    )

    payload = build_latest_v2([], None, execution)
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    snapshot = load_latest(path)

    assert payload["draft_decision"] is None
    assert snapshot.draft_decision is None
    assert snapshot.draft_execution == execution
