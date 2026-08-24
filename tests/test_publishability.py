from src.briefing.models import SourceEvidence
from src.briefing.publishability import (
    claim_supported_by_quote,
    source_anchored_title,
    validate_display_publishability,
    validate_source_publishability,
    validate_update_display_publishability,
    validate_update_source_publishability,
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


def test_ai_update_accepts_concrete_result_without_release_action():
    evidence = source(
        "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark",
        "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark.",
        content_type="ai_update",
    )

    result = validate_update_source_publishability(evidence)

    assert result.accepted is True
    assert result.event_type == "ai_update"


def test_ai_update_rejects_vague_or_promotional_content():
    cases = (
        source(
            "Interesting AI trend",
            "Interesting AI trend",
            content_type="ai_update",
        ),
        source(
            "Join our Qwen3.8 workshop for a 20% discount",
            "Join our Qwen3.8 workshop for a 20% discount",
            content_type="ai_update",
        ),
    )

    for evidence in cases:
        assert validate_update_source_publishability(evidence).reason_codes == (
            "update_missing_concrete_detail",
        )


def test_ai_update_display_requires_source_bound_subject_and_detail():
    evidence = source(
        "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark",
        "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark.",
        content_type="ai_update",
    )

    accepted = validate_update_display_publishability(
        "Qwen3.8-27B GGUF 在 Div-300 得分高出 10%",
        "",
        evidence,
    )
    invented = validate_update_display_publishability(
        "Qwen3.8-27B GGUF 在 Div-300 得分高出 20%",
        "",
        evidence,
    )
    inverted = validate_update_display_publishability(
        "Qwen3.8-27B GGUF 在 Div-300 得分低于 10%",
        "",
        evidence,
    )

    assert accepted.accepted is True
    assert invented.reason_codes == ("update_claim_not_source_bound",)
    assert inverted.reason_codes == ("update_claim_not_source_bound",)


def test_ai_update_subject_must_precede_relation_and_not_be_publisher_prefix():
    comparison_only = source(
        "Scores 10% higher than GPT-4 on MMLU benchmark",
        content_type="ai_update",
    )
    publisher_prefix = source(
        "VentureBeat: scores 10% higher on MMLU benchmark",
        content_type="ai_update",
        publisher_name="VentureBeat",
    )
    named_project = source(
        "ProjectNova scores 10% higher on MMLU benchmark",
        content_type="ai_update",
    )

    assert validate_update_source_publishability(comparison_only).reason_codes == (
        "update_missing_subject",
    )
    assert validate_update_source_publishability(publisher_prefix).reason_codes == (
        "update_missing_subject",
    )
    assert validate_update_source_publishability(named_project).accepted is True


def test_ai_update_requires_metric_or_named_detail_not_generic_technical_word():
    generic = source(
        "Qwen3.8 improves benchmark",
        content_type="ai_update",
    )
    named = source(
        "Qwen3.8 improves MMLU-Pro benchmark",
        content_type="ai_update",
    )

    assert validate_update_source_publishability(generic).reason_codes == (
        "update_missing_concrete_detail",
    )
    assert validate_update_source_publishability(named).accepted is True


def test_fact_publishability_does_not_adopt_ai_update_rules():
    evidence = source(
        "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark",
        content_type="fact_event",
    )

    assert validate_source_publishability(evidence).reason_codes == (
        "non_news_content",
    )


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
            "ChatGPT's Computer History tracks your clicks and keystrokes",
            "ChatGPT 的 Computer History 跟踪 clicks and keystrokes",
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


def test_source_anchored_title_requires_subject_and_detail_anchor():
    supported = source(
        "Nvidia dramatically reduces amount of OpenAI infra financing it may guarantee"
    )
    title = source_anchored_title(supported)

    assert title == "Nvidia 减少 OpenAI"
    assert validate_display_publishability(title, "", supported).accepted is True
    assert source_anchored_title(source("Nvidia reduces it")) is None


def test_source_anchored_title_keeps_literal_detail_anchor_after_action():
    supported = source(
        "ChatGPT’s Computer History tracks your clicks and keystrokes"
    )
    title = source_anchored_title(supported)

    assert title == "ChatGPT 追踪 clicks"
    assert validate_display_publishability(title, "", supported).accepted is True


def test_source_anchored_title_preserves_publishable_literal_subject():
    supported = source("Sainsbury's pauses AI cameras after shopper ousted")
    title = source_anchored_title(supported)

    assert title == "Sainsbury's 暂停 cameras"
    assert validate_display_publishability(title, "", supported).accepted is True


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
