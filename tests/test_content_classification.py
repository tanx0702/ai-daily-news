from dataclasses import replace

import pytest

from src.briefing.classification import classify_source_content
from src.briefing.models import SourceEvidence


def source_evidence(**overrides):
    values = {
        "publisher_id": "example",
        "publisher_name": "Example Media",
        "channel": "rss",
        "authority": "professional_media",
        "is_official": False,
        "official_identity_source": "",
        "source_title": "Example title",
        "evidence_text": "Example evidence.",
        "url": "https://example.test/story",
        "published_at": "2026-08-28T00:00:00+00:00",
    }
    values.update(overrides)
    return SourceEvidence(**values)


def test_formal_release_stays_fact_event():
    result = classify_source_content(source_evidence(
        source_title="OpenAI releases Model 5",
        evidence_text="OpenAI releases Model 5.",
    ))

    assert result.content_type == "fact_event"
    assert result.reason_codes == ("classified_fact_event",)


@pytest.mark.parametrize(
    "title, expected_type",
    [
        ("Anthropic gets its first court win over the Pentagon supply-chain label", "fact_event"),
        ("Barret Zoph is now at Google", "fact_event"),
        ("Gemini Omni 1.1 Flash lets you build with more control", "ai_update"),
        ("OpenAI, Anthropic and Google call for action against rogue AI", "fact_event"),
        ("Anthropic's new hardware standard lets AI agents control the physical world", "ai_update"),
    ],
)
def test_concrete_ai_news_actions_are_not_classified_as_non_news(title, expected_type):
    result = classify_source_content(source_evidence(source_title=title, evidence_text=title))

    assert result.content_type == expected_type


def test_professional_media_demo_becomes_ai_update():
    result = classify_source_content(source_evidence(
        source_title="H3 Max generates high-quality video",
        evidence_text="H3 Max generates high-quality video.",
        channel="rss",
    ))

    assert result.content_type == "ai_update"
    assert "capability:video" in result.detail_anchors


def test_eligible_personal_stance_stays_attributed_opinion():
    opinion = replace(
        source_evidence(
            publisher_id="karpathy",
            publisher_name="Andrej Karpathy",
            source_title="I think open models will win",
            evidence_text=(
                "I think open models will win because they are easier to adapt."
            ),
            channel="x",
        ),
        content_type="attributed_opinion",
        opinion_author="Andrej Karpathy",
        opinion_eligible=True,
        original_post=True,
        context_complete=True,
    )

    result = classify_source_content(opinion)

    assert result.content_type == "attributed_opinion"


def test_vague_or_promotional_candidate_is_rejected():
    result = classify_source_content(source_evidence(
        source_title="Join our amazing AI workshop",
        evidence_text="Register now for our amazing AI workshop.",
    ))

    assert result.content_type is None


def test_unverified_rumor_is_rejected_before_formal_action_classification():
    result = classify_source_content(source_evidence(
        source_title="Rumor: OpenAI reportedly acquires Example AI",
        evidence_text="Sources say OpenAI reportedly acquires Example AI.",
    ))

    assert result.content_type is None
    assert result.reason_codes == ("unverified_rumor",)
