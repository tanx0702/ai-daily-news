from src.briefing.adapters import brief_item_to_display_dict, content_fingerprint
from src.briefing.models import BriefItem, EvidenceBinding, SourceEvidence


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


def test_display_projection_and_fingerprint_ignore_media_enrichment():
    display = brief_item_to_display_dict(_brief_item())
    enriched = {**display, "image_url": "https://cdn.example/model.jpg"}

    assert display["title"] == "Example 发布模型"
    assert display["brief"] == "Example 发布了一款模型。"
    assert display["source_url"] == "https://example.com/model"
    assert content_fingerprint([display]) == content_fingerprint([enriched])


def test_display_projection_exposes_title_only_mode_without_raw_evidence():
    item = _brief_item()
    item = BriefItem.from_dict({
        **item.to_dict(),
        "brief": "",
        "brief_mode": "title_only",
        "brief_reason": "brief_empty",
    })

    display = brief_item_to_display_dict(item)

    assert display["brief"] == ""
    assert display["brief_mode"] == "title_only"
    assert "evidence_text" not in display
