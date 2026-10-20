"""Untranslated place names and HN discussion threads must not ship.

Two regressions from the real 2026-10-20 draft:

* "US 禁止政府机构使用 DeepSeek AI，与 China 的科技紧张局势持续" kept both
  country names in English. The untranslated-prose check only inspected
  lowercase words, so capitalised place names (also "name-shaped" enough to be
  protected as anchors) never triggered it.
* "Hacker News 热议：开发者上线自己看不懂的 AI 生成代码该怎么办" shipped as a
  ``fact_event``. Its canonical URL was the HN thread itself, with no external
  article, but only the ``Points: N`` metadata patterns guarded that case.
"""

from src.briefing.publishability import validate_source_publishability
from src.briefing.models import SourceEvidence
from src.briefing.validator import _has_untranslated_title_prose


def _source(title: str, url: str, *, discovered_via: str = "hacker_news") -> SourceEvidence:
    return SourceEvidence(
        publisher_id="bbc-com",
        publisher_name="BBC",
        channel="hacker_news",
        authority="professional_media",
        is_official=False,
        official_identity_source="",
        source_title=title,
        evidence_text=title,
        url=url,
        published_at="2026-10-20T02:00:00+00:00",
        discovered_via=discovered_via,
        evidence_quality="ready",
    )


def test_country_names_left_in_english_are_flagged():
    title = "US 禁止政府机构使用 DeepSeek AI，与 China 的科技紧张局势持续"

    assert _has_untranslated_title_prose(title) is True


def test_country_names_translated_are_accepted():
    for title in (
        "美国禁止政府机构使用 DeepSeek AI，与中国科技紧张局势持续",
        "英国监管机构调查 OpenAI",
    ):
        assert _has_untranslated_title_prose(title) is False, title


def test_hn_discussion_thread_is_rejected_as_non_news():
    result = validate_source_publishability(
        _source(
            "Ask HN: How do you review AI-generated code you do not understand?",
            "https://news.ycombinator.com/item?id=50151036",
        )
    )

    assert result.accepted is False
    assert result.rejection_detail == "discussion_thread"


def test_hn_item_pointing_at_external_article_is_still_judged_normally():
    """A real story that merely arrived via HN must not be rejected."""
    result = validate_source_publishability(
        _source(
            "US bans DeepSeek AI from government use as tech tension with China continues",
            "https://www.bbc.com/news/articles/cjkgeq0k1wevo",
        )
    )

    assert result.rejection_detail != "discussion_thread"
