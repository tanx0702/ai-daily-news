from src.briefing.models import SourceEvidence
from src.briefing.publishability import (
    claim_supported_by_quote,
    validate_display_publishability,
    validate_source_publishability,
)


def source(title, evidence_text=None, **overrides):
    values = {
        "publisher_id": "publisher",
        "publisher_name": "OpenAI",
        "channel": "rss",
        "authority": "professional_media",
        "is_official": False,
        "official_identity_source": "",
        "source_title": title,
        "evidence_text": evidence_text or title,
        "url": "https://example.test/story",
        "published_at": "2026-08-14T00:00:00+00:00",
    }
    values.update(overrides)
    return SourceEvidence(**values)


def test_complete_title_only_news_event_is_publishable():
    result = validate_display_publishability(
        "OpenAI 发布 GPT-5.6 Ultrafast",
        "",
        source("OpenAI releases GPT-5.6 Ultrafast"),
    )

    assert result.accepted is True
    assert result.title_completeness == "complete"
    assert result.event_type == "release"


def test_vague_topic_titles_are_not_publishable():
    cases = (
        ("Mistral AI 战略", "Mistral AI strategy"),
        ("GPT-5.6 Sol Ultrafast 加速", "GPT-5.6 Sol Ultrafast"),
        ("Demis Hassabis 提及 3.7 Flash", "Demis Hassabis mentions 3.7 Flash"),
        ("公司发布模型", "A company releases a model"),
    )

    for title, source_title in cases:
        result = validate_display_publishability(title, "", source(source_title))
        assert result.accepted is False, title
        assert result.reason_codes, title


def test_hn_metrics_and_tutorial_titles_are_not_news_events():
    metrics = source(
        "AI text watermarking",
        "AI text watermarking\nPoints: 6\n# Comments: 2",
        discovered_via="hacker_news",
    )
    tutorial = source(
        "How AI text watermarking works",
        discovered_via="hacker_news",
        evidence_quality="title_only",
    )

    assert validate_source_publishability(metrics).reason_codes == (
        "metadata_only_evidence",
    )
    assert validate_source_publishability(tutorial).reason_codes == (
        "non_news_content",
    )


def test_claim_cannot_compose_subject_action_and_object_across_sentences():
    quote = "Mistral office research. OpenAI releases GPT-5.6."

    assert claim_supported_by_quote("Mistral 发布 GPT-5.6", quote) is False
    assert claim_supported_by_quote("OpenAI 发布 GPT-5.6", quote) is True


def test_action_and_model_must_be_supported_by_same_binding_quote():
    result = validate_display_publishability(
        "OpenAI 升级 GPT-5.7",
        "",
        source("OpenAI releases GPT-5.6"),
    )

    assert result.accepted is False
    assert result.reason_codes[0] in {
        "title_action_not_source_bound",
        "title_claim_not_source_bound",
    }
