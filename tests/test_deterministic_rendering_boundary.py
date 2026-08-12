import os
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src import cover, generator, wechat_draft
from src.briefing.adapters import content_fingerprint
from src.briefing.models import BriefItem, EvidenceBinding, SourceEvidence
from src.media_assets import resolve_article_media


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


def test_renderers_accept_brief_items_through_the_display_projection():
    item = _brief_item()

    daily = generator.render_daily_html([item], "2026-08-07")
    wechat = generator.render_wechat_article([item], "2026-08-07", "https://example.com")

    for rendered in (daily, wechat):
        assert "Example 发布模型" in rendered
        assert "Example 发布了一款模型。" in rendered
        assert "https://example.com/model" in rendered


def test_media_and_cover_use_copies_for_brief_item_inputs():
    item = _brief_item()
    fingerprint = content_fingerprint([item])
    validation = {
        "valid": True,
        "url": "https://example.com/model.jpg",
        "jpeg_bytes": b"jpeg",
        "sha256": "hash",
        "phash": "0" * 16,
        "width": 640,
        "height": 360,
        "source": "test",
        "score": 80,
        "score_reasons": [],
    }

    with patch("src.media_assets._fetch_page_image_candidates", return_value=[{"url": validation["url"]}]):
        with patch("src.media_assets.validate_media_candidate", return_value=validation):
            enriched, _ = resolve_article_media([item])

    with TemporaryDirectory() as directory:
        with patch.dict(os.environ, {"COVER_RENDER_MODE": "editorial"}, clear=False):
            cover.generate_cover_from_news(
                [item],
                "2026-08-07",
                output_path=os.path.join(directory, "cover.jpg"),
            )

    assert enriched[0] is not item
    assert enriched[0]["media_state"] == "trusted"
    assert content_fingerprint([item]) == fingerprint


def test_wechat_uses_supplied_deterministic_content_when_ai_flag_is_enabled():
    captured = {}

    def create_draft(_token, _title, content, **_kwargs):
        captured["content"] = content
        return "draft-id"

    with patch.dict(
        os.environ,
        {
            "WECHAT_APP_ID": "app-id",
            "WECHAT_APP_SECRET": "secret",
            "WECHAT_USE_AI_TEMPLATE": "1",
        },
        clear=False,
    ):
        with patch("src.wechat_draft._get_access_token", return_value="token"):
            with patch("src.wechat_draft._enrich_news_with_images", side_effect=lambda _token, items: items):
                with patch("src.wechat_draft._create_draft", side_effect=create_draft):
                    with patch("src.generator.render_wechat_article_ai", side_effect=AssertionError):
                        result = wechat_draft.publish_daily_article(
                            [_brief_item()],
                            "2026-08-07",
                            "https://example.com",
                            rendered_content="<p>deterministic</p>",
                        )

    assert result == {"status": "draft_created", "media_id": "draft-id"}
    assert captured["content"] == "<p>deterministic</p>"
