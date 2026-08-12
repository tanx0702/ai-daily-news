from datetime import datetime, timezone

from src.briefing.clusterer import EventClusterer
from src.briefing.evidence import source_evidence_from_candidate
from src.briefing.models import SourceEvidence


def evidence(**overrides):
    values = {
        "publisher_id": "openai",
        "publisher_name": "OpenAI",
        "channel": "rss",
        "authority": "official",
        "is_official": True,
        "official_identity_source": "rss_source_config",
        "source_title": "OpenAI releases Model 5 for developers",
        "evidence_text": "OpenAI releases Model 5 for developers with a new API.",
        "url": "https://openai.com/news/model-5",
        "published_at": "2026-08-07T08:00:00+00:00",
    }
    values.update(overrides)
    return SourceEvidence(**values)


def test_source_evidence_uses_controlled_aliases_for_publisher_identity():
    candidate = {
        "title": "OpenAI releases Model 5",
        "summary": "OpenAI releases Model 5 for developers.",
        "url": "https://openai.com/news/model-5",
        "source": "OpenAI Blog",
        "source_type": "rss",
        "source_tier": "primary",
        "published_at": datetime(2026, 8, 7, 8, tzinfo=timezone.utc),
    }

    normalized = source_evidence_from_candidate(
        candidate,
        publisher_aliases={"openai.com": "openai"},
    )

    assert normalized.publisher_id == "openai"
    assert normalized.authority == "official"
    assert normalized.is_official is True
    assert normalized.official_identity_source == "rss_source_config"


def test_source_evidence_uses_only_frozen_source_fields_not_candidate_summary():
    candidate = {
        "title": "Raw title",
        "source_title": "Raw source title",
        "summary": "LLM invented a funding round.",
        "source_summary": "Raw source summary.",
        "source_excerpt": "Raw source excerpt.",
        "source_body": "Raw source body.",
        "url": "https://example.com/news",
        "source": "Example",
        "source_type": "rss",
        "published_at": datetime(2026, 8, 7, 8, tzinfo=timezone.utc),
    }

    normalized = source_evidence_from_candidate(candidate)

    assert "Raw source summary." in normalized.evidence_text
    assert "Raw source excerpt." in normalized.evidence_text
    assert "Raw source body." in normalized.evidence_text
    assert "LLM invented a funding round." not in normalized.evidence_text


def test_source_evidence_rejects_an_unproven_candidate_summary_as_evidence():
    candidate = {
        "title": "Raw source title",
        "summary": "Generated claim without source provenance.",
        "url": "https://example.com/news",
        "source": "Example",
        "source_type": "rss",
        "published_at": datetime(2026, 8, 7, 8, tzinfo=timezone.utc),
    }

    normalized = source_evidence_from_candidate(candidate)

    assert normalized.evidence_text == "Raw source title"


def test_unknown_x_account_is_non_official_even_if_payload_claims_official():
    candidate = {
        "title": "Unknown account says OpenAI released Model 5",
        "summary": "Unknown account says OpenAI released Model 5.",
        "url": "https://x.com/unknown/status/42",
        "source": "Unknown (X)",
        "source_type": "x",
        "source_tier": "primary",
        "x_handle": "unknown",
        "x_official": True,
        "published_at": datetime(2026, 8, 7, 8, tzinfo=timezone.utc),
    }

    normalized = source_evidence_from_candidate(candidate)

    assert normalized.is_official is False
    assert normalized.official_identity_source == ""
    assert normalized.authority == "community"


def test_x_official_identity_requires_a_controlled_mapping_or_trusted_collector():
    candidate = {
        "title": "OpenAI posts an update",
        "summary": "Original X post text.",
        "url": "https://x.com/openai/status/42",
        "source": "OpenAI (X)",
        "source_type": "x",
        "x_handle": "openai",
        "x_official": True,
        "x_official_source": "display-name-verified",
        "published_at": datetime(2026, 8, 7, 8, tzinfo=timezone.utc),
    }

    untrusted = source_evidence_from_candidate(candidate)
    mapped = source_evidence_from_candidate(
        candidate,
        official_x_accounts={"openai": "controlled_x_account_config"},
    )
    collector_trusted = source_evidence_from_candidate(
        candidate,
        trusted_x_collector=True,
    )

    assert untrusted.is_official is False
    assert mapped.is_official is True
    assert mapped.official_identity_source == "controlled_x_account_config"
    assert collector_trusted.is_official is True
    assert collector_trusted.official_identity_source == "trusted_x_collector"


def test_clusterer_merges_rss_and_x_reports_and_prefers_official_source():
    rss = evidence()
    x = evidence(
        publisher_id="researcher",
        publisher_name="Researcher",
        channel="x",
        authority="community",
        is_official=False,
        official_identity_source="",
        source_title="OpenAI releases Model 5 for developers",
        evidence_text="OpenAI releases Model 5 for developers.",
        url="https://x.com/researcher/status/42",
        published_at="2026-08-07T08:05:00+00:00",
    )

    result = EventClusterer().cluster([x, rss])

    assert len(result.events) == 1
    assert result.events[0].canonical_evidence == rss
    assert result.events[0].related_evidence == (x,)
    assert result.quarantined == ()
    assert result.diagnostics["merged_count"] == 1


def test_clusterer_keeps_similar_but_independent_events_separate():
    model_release = evidence()
    office_opening = evidence(
        publisher_id="openai-london",
        source_title="OpenAI opens a new London office",
        evidence_text="OpenAI opens a new London office for its research team.",
        url="https://openai.com/news/london-office",
        published_at="2026-08-07T08:10:00+00:00",
    )

    result = EventClusterer().cluster([model_release, office_opening])

    assert len(result.events) == 2
    assert result.quarantined == ()


def test_clusterer_quarantines_ambiguous_duplicate_instead_of_backfill_event():
    stronger = evidence(
        source_title="OpenAI makes Model 5 available to selected API developers",
        evidence_text=(
            "OpenAI makes Model 5 available to selected API developers in a limited preview."
        ),
    )
    ambiguous = evidence(
        publisher_id="media",
        publisher_name="AI Media",
        authority="professional_media",
        is_official=False,
        official_identity_source="",
        source_title="Selected developers begin receiving access to OpenAI Model 5",
        evidence_text="Selected developers begin receiving access to OpenAI Model 5.",
        url="https://media.example/openai-model-5-access",
        published_at="2026-08-07T08:30:00+00:00",
    )

    result = EventClusterer().cluster([ambiguous, stronger])

    assert len(result.events) == 1
    assert result.events[0].canonical_evidence == stronger
    assert result.events[0].related_evidence == ()
    assert len(result.quarantined) == 1
    assert result.quarantined[0].evidence == ambiguous
    assert result.quarantined[0].duplicate_of == result.events[0].event_key
    assert result.quarantined[0].reason_code == "ambiguous_duplicate"
    assert result.diagnostics["quarantined_count"] == 1


def test_exact_normalized_url_is_a_confirmed_duplicate():
    original = evidence(url="https://openai.com/news/model-5?utm_source=rss")
    repeat = evidence(
        publisher_id="openai-copy",
        url="https://OPENAI.com/news/model-5#details",
        evidence_text="OpenAI releases Model 5 for developers with a new API. More details.",
    )

    result = EventClusterer().cluster([repeat, original])

    assert len(result.events) == 1
    assert len(result.events[0].related_evidence) == 1


def test_clusterer_preserves_collector_score_by_canonical_url():
    item = evidence()

    result = EventClusterer().cluster([item], editorial_scores={item.url: 87.5})

    assert result.events[0].editorial_score == 87.5
