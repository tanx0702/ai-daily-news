from src.briefing.models import SourceEvidence
from src.briefing.publishability import (
    claim_supported_by_quote,
    source_anchored_title,
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


def test_source_publishability_recognizes_common_factual_news_verbs():
    titles = (
        "Nvidia dramatically reduces amount of OpenAI infra financing it may guarantee",
        "OpenAI reportedly disbanded its preparedness team",
        "Sainsbury's pauses AI cameras after shopper ousted",
        "ChatGPT's Computer History tracks your clicks and keystrokes",
        "Anthropic revenue jumps 14x to more than $11.5B in second quarter",
    )

    for title in titles:
        result = validate_source_publishability(source(title))

        assert result.accepted is True, title


def test_publishability_accepts_cross_language_titles_with_anchored_details():
    cases = (
        (
            "Nvidia dramatically reduces amount of OpenAI infra financing it may guarantee",
            "Nvidia 降低 OpenAI",
        ),
        (
            "OpenAI reportedly disbanded its preparedness team",
            "OpenAI 解散 preparedness team",
        ),
        (
            "Sainsbury's pauses AI cameras after shopper ousted",
            "Sainsbury's 暂停 cameras",
        ),
        (
            "ChatGPT's Computer History tracks your clicks and keystrokes",
            "ChatGPT's Computer History 追踪 clicks",
        ),
        (
            "Anthropic revenue jumps 14x to more than $11.5B in second quarter",
            "Anthropic 增长 14x",
        ),
    )

    for source_title, display_title in cases:
        result = validate_display_publishability(
            display_title,
            "",
            source(source_title),
        )

        assert result.accepted is True, display_title


def test_publishability_accepts_reduces_and_chatgpt_with_anchored_titles():
    cases = (
        (
            "Nvidia dramatically reduces amount of OpenAI infra financing it may guarantee",
            "Nvidia 减少 OpenAI",
        ),
        (
            "ChatGPT’s Computer History tracks your clicks and keystrokes",
            "ChatGPT 的 Computer History 追踪 clicks and keystrokes",
        ),
    )

    for source_title, display_title in cases:
        result = validate_display_publishability(
            display_title,
            "",
            source(source_title),
        )

        assert result.accepted is True, display_title


def test_source_anchored_title_requires_two_source_anchors():
    supported = source(
        "Nvidia dramatically reduces amount of OpenAI infra financing it may guarantee"
    )
    title = source_anchored_title(supported)

    assert title == "Nvidia 减少 OpenAI"
    assert validate_display_publishability(title, "", supported).accepted is True
    assert source_anchored_title(source("Nvidia reduces costs")) is None


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
