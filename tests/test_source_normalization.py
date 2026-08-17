import json

import pytest

from src.briefing.evidence import source_evidence_from_candidate
from src.source_normalization import (
    normalize_candidate_source,
    public_source_url,
    publisher_name_from_url,
    publisher_trust_from_url,
    sanitize_source_url_for_audit,
)


def test_hn_metadata_is_removed_and_external_publisher_wins():
    candidate = {
        "title": "How text watermarking works",
        "summary": (
            '<p>Article URL: <a href="https://venturebeat.com/ai/watermarking/">'
            "article</a></p>"
            '<p>Comments URL: <a href="https://news.ycombinator.com/item?id=42">'
            "comments</a></p><p>Points: 6</p><p># Comments: 2</p>"
        ),
        "url": "https://news.ycombinator.com/item?id=42",
        "source": "Hacker News AI",
        "source_type": "rss",
        "source_tier": "primary",
        "published_at": "2026-08-14T00:00:00+00:00",
    }

    normalized = normalize_candidate_source(candidate)
    evidence = source_evidence_from_candidate(candidate)

    assert normalized.canonical_url == "https://venturebeat.com/ai/watermarking/"
    assert normalized.publisher_name == "VentureBeat"
    assert normalized.discovered_via == "hacker_news"
    assert normalized.evidence_text == "How text watermarking works"
    assert evidence is not None
    assert evidence.url == normalized.canonical_url
    assert evidence.publisher_name == "VentureBeat"
    assert evidence.authority == "professional_media"
    assert evidence.is_official is False
    assert evidence.discovered_via == "hacker_news"
    assert "Points" not in evidence.evidence_text
    assert "Comments" not in evidence.evidence_text


def test_hn_mismatched_visible_article_url_falls_back_to_discussion():
    result = normalize_candidate_source({
        "title": "OpenAI releases GPT-5.6",
        "summary": (
            '<p>Article URL: <a href="https://openai.com/news/gpt-5-6">'
            "https://evil.example/spoof</a></p>"
        ),
        "url": "https://news.ycombinator.com/item?id=47",
        "source": "Hacker News",
        "source_type": "hn",
    })

    assert result.canonical_url == "https://news.ycombinator.com/item?id=47"
    assert result.publisher_name == "Hacker News"
    assert result.evidence_text == result.source_title


@pytest.mark.parametrize(
    "bad_url",
    (
        "http://[::1",
        "https://example.test:80/news",
        "http://example.test:443/news",
        "http://2130706433/",
        "http://0x7f000001/",
    ),
)
def test_invalid_source_url_is_rejected_without_exception(bad_url):
    candidate = {
        "title": "OpenAI releases GPT-5.6",
        "source_title": "OpenAI releases GPT-5.6",
        "url": bad_url,
        "source": "Broken feed",
        "source_type": "rss",
        "published_at": "2026-08-14T00:00:00+00:00",
    }

    assert normalize_candidate_source(candidate).canonical_url == ""
    assert source_evidence_from_candidate(candidate) is None


def test_sensitive_query_never_reaches_evidence_or_audit_projection():
    raw = (
        "https://news.example.test/article?id=42&token=TOP-SECRET"
        "&signature=SIGNED#private"
    )
    candidate = {
        "title": "OpenAI releases GPT-5.6",
        "source_title": "OpenAI releases GPT-5.6",
        "url": raw,
        "source": "Example",
        "source_type": "rss",
        "published_at": "2026-08-14T00:00:00+00:00",
    }

    normalized = normalize_candidate_source(candidate)
    projected = sanitize_source_url_for_audit(raw)

    assert normalized.canonical_url == ""
    assert source_evidence_from_candidate(candidate) is None
    assert projected.startswith("invalid-url:sha256:")
    assert "TOP-SECRET" not in projected
    assert "SIGNED" not in projected
    assert public_source_url(
        "https://news.ycombinator.com/item?id=42&utm_source=feed"
    ) == "https://news.ycombinator.com/item?id=42"


def test_controlled_publishers_have_stable_names_and_trust():
    assert publisher_name_from_url("https://openai.com/news/gpt-5-6") == "OpenAI"
    assert publisher_trust_from_url("https://openai.com/news/gpt-5-6") == (
        "official",
        True,
        "canonical_domain_allowlist",
    )
    assert publisher_name_from_url("https://www.bbc.co.uk/news/ai") == "BBC"
    assert publisher_trust_from_url(
        "https://huggingface.co/some-user/some-model"
    ) == ("community", False, "")


def test_source_evidence_public_projection_contains_only_safe_url():
    candidate = {
        "title": "OpenAI releases GPT-5.6",
        "source_title": "OpenAI releases GPT-5.6",
        "url": "https://openai.com/news/gpt-5-6?utm_source=feed",
        "source": "OpenAI mirror",
        "source_type": "rss",
        "published_at": "2026-08-14T00:00:00+00:00",
    }

    evidence = source_evidence_from_candidate(candidate)
    assert evidence is not None
    payload = json.dumps(evidence.to_public_dict(), ensure_ascii=False)
    assert evidence.url == "https://openai.com/news/gpt-5-6"
    assert "utm_source" not in payload
