from datetime import datetime, timezone
import json
from pathlib import Path

from src.briefing.config import BriefingConfig
from src.briefing.clusterer import EventClusterer
from src.briefing.evidence import source_evidence_from_candidate
from src.briefing.models import SourceEvidence
from src.briefing.semantic_reviewer import (
    SemanticDuplicateReviewer,
    SemanticReview,
)
from src.llm_config import LLMConfig


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


def test_distinct_events_with_reversed_subjects_get_different_event_keys():
    class DistinctReviewer:
        diagnostics = {}

        def review(self, left, right):
            return SemanticReview("distinct", "rules_and_llm")

    result = EventClusterer(reviewer=DistinctReviewer()).cluster(
        (
            evidence(
                source_title="OpenAI acquires Acme AI",
                evidence_text="OpenAI acquired Acme AI in a transaction.",
                url="https://media.example/openai-acquires-acme",
            ),
            evidence(
                source_title="Acme AI acquires OpenAI",
                evidence_text="Acme AI acquired OpenAI in a transaction.",
                url="https://media.example/acme-acquires-openai",
            ),
        )
    )

    assert len(result.events) == 2
    assert len({event.event_key for event in result.events}) == 2


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


def test_clusterer_merges_release_synonym_instead_of_backfill_event():
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
    assert result.events[0].related_evidence == (ambiguous,)
    assert result.quarantined == ()
    assert result.diagnostics["semantic_duplicate_merged_count"] == 1


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


def _brad_lightcap_fixture() -> list[SourceEvidence]:
    path = (
        Path(__file__).parent
        / "fixtures"
        / "briefing"
        / "2026-08-12-brad-lightcap-duplicates.json"
    )
    return [
        SourceEvidence(**value)
        for value in json.loads(path.read_text(encoding="utf-8"))
    ]


def test_clusterer_merges_three_brad_lightcap_sources_and_prefers_rss():
    result = EventClusterer(BriefingConfig.from_env({})).cluster(
        _brad_lightcap_fixture()
    )

    assert len(result.events) == 1
    assert result.events[0].canonical_evidence.channel == "rss"
    assert {value.channel for value in result.events[0].related_evidence} == {
        "rss",
        "x",
    }
    assert result.diagnostics["semantic_duplicate_merged_count"] == 2


class UnavailableSemanticReviewer:
    def __init__(self):
        self.calls = 0
        self.diagnostics = {"semantic_llm_unavailable_count": 0}

    def review(self, left, right):
        self.calls += 1
        self.diagnostics["semantic_llm_unavailable_count"] += 1
        return SemanticReview("uncertain", "rules", "semantic_llm_unavailable")


class BridgeSemanticReviewer:
    diagnostics = {}

    def __init__(self, first_url: str, second_url: str, bridge_url: str):
        self.first_url = first_url
        self.second_url = second_url
        self.bridge_url = bridge_url

    def review(self, left, right):
        pair = {left.url, right.url}
        if pair == {self.first_url, self.second_url}:
            return SemanticReview("distinct", "rules_and_llm")
        if pair == {self.first_url, self.bridge_url}:
            return SemanticReview("uncertain", "rules_and_llm")
        raise AssertionError(f"unexpected semantic review pair: {pair}")


class SameRowSemanticReviewer:
    diagnostics = {}

    def __init__(self, canonical_url: str, related_url: str, candidate_url: str):
        self.canonical_url = canonical_url
        self.related_url = related_url
        self.candidate_url = candidate_url

    def review(self, left, right):
        pair = {left.url, right.url}
        if pair == {self.canonical_url, self.related_url}:
            return SemanticReview("same_event", "rules_and_llm")
        if pair == {self.canonical_url, self.candidate_url}:
            return SemanticReview("uncertain", "rules_and_llm")
        raise AssertionError(f"unexpected semantic review pair: {pair}")


def test_uncertain_reviewer_failure_quarantines_lower_priority_source():
    strong = evidence(
        source_title="OpenAI executive leaves the company",
        evidence_text="An OpenAI executive announced a departure.",
    )
    weak = evidence(
        publisher_id="community",
        publisher_name="Community",
        channel="x",
        authority="community",
        is_official=False,
        official_identity_source="",
        source_title="OpenAI leader takes off",
        evidence_text="An OpenAI leader is leaving the company.",
        url="https://x.com/community/status/99",
        published_at="2026-08-07T08:05:00+00:00",
    )
    reviewer = UnavailableSemanticReviewer()

    result = EventClusterer(
        BriefingConfig.from_env({}),
        reviewer=reviewer,
    ).cluster([weak, strong])

    assert len(result.events) == 1
    assert result.events[0].canonical_evidence == strong
    assert len(result.quarantined) == 1
    assert result.quarantined[0].evidence == weak
    assert result.quarantined[0].reason_code == "semantic_duplicate_unresolved"
    assert reviewer.calls == 1
    assert result.diagnostics["semantic_llm_unavailable_count"] == 1


def test_unlisted_person_same_event_response_is_quarantined_as_unresolved():
    class SameEventReviewer(SemanticDuplicateReviewer):
        def _available(self):
            return True

        def _request(self, left, right):
            return {
                "relationship": "same_event",
                "shared_subjects": ["Aidan Gomez"],
                "shared_action": "departure",
            }

    strong = evidence(
        publisher_id="cohere-official",
        publisher_name="Cohere",
        authority="official",
        is_official=True,
        official_identity_source="source_config",
        source_title="Cohere CEO Aidan Gomez leaves the company",
        evidence_text="Aidan Gomez announced his departure from Cohere.",
        url="https://cohere.example/aidan-gomez",
    )
    weak = evidence(
        publisher_id="community",
        publisher_name="Community",
        channel="x",
        authority="community",
        is_official=False,
        official_identity_source="",
        source_title="Aidan Gomez departs Cohere",
        evidence_text="Cohere confirmed that Aidan Gomez is leaving.",
        url="https://x.com/community/status/123456",
    )

    result = EventClusterer(
        BriefingConfig.from_env({}),
        reviewer=SameEventReviewer(
            LLMConfig("key", "model", "https://quality.example/v1")
        ),
    ).cluster([strong, weak])

    assert [event.canonical_evidence for event in result.events] == [strong]
    assert [value.evidence for value in result.quarantined] == [weak]
    assert result.quarantined[0].reason_code == "semantic_duplicate_unresolved"


def test_confirmed_duplicate_does_not_hide_an_earlier_unresolved_bridge():
    strongest = evidence(
        source_title="OpenAI executive takes off",
        evidence_text="An OpenAI executive announced a departure.",
        url="https://official.example/first",
    )
    second = evidence(
        publisher_id="media",
        publisher_name="Media",
        authority="professional_media",
        is_official=False,
        official_identity_source="",
        source_title="OpenAI leader leaves company",
        evidence_text="An OpenAI leader is leaving the company.",
        url="https://media.example/second",
    )
    bridge = evidence(
        publisher_id="community",
        publisher_name="Community",
        channel="x",
        authority="community",
        is_official=False,
        official_identity_source="",
        source_title=second.source_title,
        evidence_text=second.evidence_text,
        url="https://media.example/second#x-copy",
    )
    reviewer = BridgeSemanticReviewer(strongest.url, second.url, bridge.url)

    result = EventClusterer(
        BriefingConfig.from_env({}), reviewer=reviewer
    ).cluster([bridge, second, strongest])

    assert [event.canonical_evidence for event in result.events] == [strongest]
    assert {value.evidence for value in result.quarantined} == {second, bridge}
    assert all(
        value.duplicate_of == result.events[0].event_key
        for value in result.quarantined
    )
    assert all(
        value.reason_code == "semantic_duplicate_unresolved"
        for value in result.quarantined
    )
    assert all(value.relationship == "uncertain" for value in result.quarantined)
    assert all(
        value.comparison_mode == "rules_and_llm"
        for value in result.quarantined
    )


def test_confirmed_related_evidence_wins_over_uncertainty_in_the_same_event():
    canonical = evidence(
        source_title="OpenAI executive takes off",
        evidence_text="An OpenAI executive announced a departure.",
        url="https://official.example/canonical",
    )
    related = evidence(
        publisher_id="media",
        publisher_name="Media",
        authority="professional_media",
        is_official=False,
        official_identity_source="",
        source_title="OpenAI leader leaves company",
        evidence_text="An OpenAI leader is leaving the company.",
        url="https://media.example/related",
    )
    candidate = evidence(
        publisher_id="community",
        publisher_name="Community",
        channel="x",
        authority="community",
        is_official=False,
        official_identity_source="",
        source_title=related.source_title,
        evidence_text=related.evidence_text,
        url="https://media.example/related#copy",
    )
    reviewer = SameRowSemanticReviewer(canonical.url, related.url, candidate.url)

    result = EventClusterer(
        BriefingConfig.from_env({}), reviewer=reviewer
    ).cluster([candidate, related, canonical])

    assert len(result.events) == 1
    assert result.events[0].canonical_evidence == canonical
    assert set(result.events[0].related_evidence) == {related, candidate}
    assert result.quarantined == ()


def test_clusterer_does_not_call_reviewer_for_distinct_company_actions():
    reviewer = UnavailableSemanticReviewer()
    release = evidence()
    departure = evidence(
        source_title="Brad Lightcap leaves OpenAI",
        evidence_text="OpenAI COO Brad Lightcap announced his departure.",
        url="https://media.example/departure",
    )

    result = EventClusterer(
        BriefingConfig.from_env({}),
        reviewer=reviewer,
    ).cluster([release, departure])

    assert len(result.events) == 2
    assert reviewer.calls == 0


def test_clusterer_keeps_highly_similar_reports_outside_window_separate():
    first = evidence(
        published_at="2026-08-01T08:00:00+00:00",
    )
    later = evidence(
        publisher_id="media-later",
        publisher_name="Later Media",
        authority="professional_media",
        is_official=False,
        official_identity_source="",
        url="https://media.example/model-5-later",
        published_at="2026-08-04T08:00:00+00:00",
    )

    result = EventClusterer(BriefingConfig.from_env({})).cluster([first, later])

    assert len(result.events) == 2
    assert result.quarantined == ()


def test_clusterer_keeps_conflicting_model_versions_separate():
    model_five = evidence(
        source_title="OpenAI releases Model 5 for developers",
        evidence_text="OpenAI releases Model 5 for developers with a new API.",
        url="https://openai.example/model-5",
    )
    model_six = evidence(
        publisher_id="media-six",
        publisher_name="Media Six",
        authority="professional_media",
        is_official=False,
        official_identity_source="",
        source_title="OpenAI releases Model 6 for developers",
        evidence_text="OpenAI releases Model 6 for developers with a new API.",
        url="https://media.example/model-6",
    )

    result = EventClusterer(BriefingConfig.from_env({})).cluster(
        [model_five, model_six]
    )

    assert len(result.events) == 2
    assert result.quarantined == ()
