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


def test_hn_discovered_publisher_keeps_configured_tier():
    """An HN-discovered Reuters/CNBC story must not be labelled `community`.

    Regression from the real 2026-10-16 draft: six of twenty items arrived via
    Hacker News, and the code trusted only the URL registry for their authority.
    Reuters, CNBC, FT, ZDNet and medRxiv were absent from that registry, so every
    one of them fell through to `community` — which then sank them in the headline
    ranking and made cover selection reject them as untrusted.
    """
    candidate = {
        "title": "亚洲芯片股因 AI 融资担忧而下跌",
        "url": "https://www.cnbc.com/2026/10/14/asia-chip-stocks.html",
        "source": "CNBC",
        "source_type": "rss",
        "source_tier": "media",
        "published_at": "2026-10-14T10:00:00+00:00",
        "summary": "Asian chip stocks fell on AI financing worries.",
    }

    evidence = source_evidence_from_candidate(candidate)

    assert evidence is not None
    assert evidence.authority == "professional_media"
    assert evidence.publisher_name == "CNBC"


def test_hn_discovered_research_feed_is_research():
    candidate = {
        "title": "Conversational AI may sway clinician judgement",
        "url": "https://www.medrxiv.org/content/10.1101/2026.10.14.1",
        "source": "medRxiv",
        "source_type": "rss",
        "source_tier": "research",
        "published_at": "2026-10-14T10:00:00+00:00",
        "summary": "A preprint on conversational AI and clinician judgement.",
    }

    evidence = source_evidence_from_candidate(candidate)

    assert evidence is not None
    assert evidence.authority == "research"


def test_configured_publishers_are_registered_with_their_tier():
    """Every configured feed must resolve to its declared authority.

    A registry gap silently falls back to ``community``, which then mis-ranks the
    headline and makes cover selection treat the source as untrusted. The 2026-10-16
    run shipped ``Bbc`` and ``Businessinsider`` items labelled ``community``
    because only the UK BBC domain was registered and Business Insider never was.
    """
    import json
    import pathlib

    from src.source_normalization import publisher_trust_from_url

    config = json.loads(
        pathlib.Path("config/rss_sources.json").read_text(encoding="utf-8")
    )
    # Hugging Face is deliberately community: model likes/downloads are activity,
    # not news, so its feed must not promote it to professional_media.
    expected_community = {"Hugging Face Blog"}  # noqa: F841

    gaps = []
    for source in config["sources"]:
        tier = source.get("tier")
        if tier not in {"media", "primary", "research"}:
            continue
        if source["name"] in expected_community:
            continue
        authority, _, _ = publisher_trust_from_url(source["url"])
        if authority == "community":
            gaps.append((source["name"], source["url"]))

    assert gaps == [], f"unregistered configured publishers: {gaps}"


def test_hn_news_publishers_seen_in_production_are_registered():
    """Publishers that actually reached the brief must not resolve to community.

    The 2026-10-19 run labelled a Politico item ``community``; the same gap hit
    The Economist, Tom's Hardware, Quanta and several others that arrive through
    Hacker News rather than a configured feed.
    """
    from src.source_normalization import publisher_trust_from_url

    expected = {
        "https://www.politico.com/news/2026/10/19/anthropic-ai-bill": "professional_media",
        "https://www.economist.com/science-and-technology/2026/10/19/x": "professional_media",
        "https://www.tomshardware.com/pc-components/x": "professional_media",
        "https://www.techpolicy.press/x": "professional_media",
        "https://news.cgtn.com/news/x.html": "professional_media",
        "https://www.thebignewsletter.com/p/x": "professional_media",
        "https://news.gallup.com/poll/x.aspx": "professional_media",
        "https://www.vals.ai/benchmarks/x": "professional_media",
        "https://www.quantamagazine.org/x": "research",
        "https://engineering.fb.com/2026/10/19/x": "official",
        # Unregistered personal/indie sites must stay community.
        "https://paretoops.dev/x": "community",
        "https://jrdsgl.com/x": "community",
    }
    for url, authority in expected.items():
        got, _, _ = publisher_trust_from_url(url)
        assert got == authority, f"{url} -> {got}, expected {authority}"


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
        "source_tier": "community",
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


def test_untrusted_x_candidate_cannot_override_publisher_name():
    candidate = {
        "title": "Fake Publisher: OpenAI releases GPT-5.6",
        "summary": "OpenAI releases GPT-5.6",
        "url": "https://x.com/example/status/42",
        "source": "Fake Publisher (X)",
        "source_type": "x",
        "published_at": "2026-08-14T00:00:00+00:00",
        "x_handle": "example",
        "x_source_name": "Fake Publisher",
    }

    evidence = source_evidence_from_candidate(candidate, trusted_x_collector=False)

    assert evidence is not None
    assert evidence.publisher_name == "X"


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


def test_publisher_promo_footer_is_not_part_of_evidence_text():
    """Trailing 公众号 boilerplate must not reach ``evidence_text``.

    Regression from the 2026-09-24 real draft: the ifanr teaser line "最卷一夜！"
    was published as a summary because the source summary carried the article's
    own teaser plus the publisher's promotional footer, and the brief selector
    picked the teaser. Removing the footer leaves no fact-free sentence to bind.
    """
    candidate = {
        "title": "Claude 5.5 发布，性能直逼 Fable，还要卷价格",
        "source_summary": (
            "最卷一夜！ #欢迎关注爱范儿官方微信公众号：爱范儿（微信号：ifanr），"
            "更多精彩内容第一时间为您奉上。"
        ),
        "url": "https://www.ifanr.com/1681629",
        "source": "APPSO",
        "source_type": "rss",
        "published_at": "2026-09-23T02:25:19+00:00",
    }

    normalized = normalize_candidate_source(candidate)

    assert normalized.evidence_text == (
        "Claude 5.5 发布，性能直逼 Fable，还要卷价格\n最卷一夜！"
    )
    assert "欢迎关注" not in normalized.evidence_text
    assert "微信号" not in normalized.evidence_text


def test_click_to_read_original_stub_is_removed():
    candidate = {
        "title": "OpenAI 推出分级处理框架及案例研究，用于报告模型失调问题",
        "source_summary": "点击查看原文>",
        "url": "https://www.infoq.cn/article/sFUUaaIQZH3ecXb14WVs",
        "source": "InfoQ 中文",
        "source_type": "rss",
        "published_at": "2026-09-24T11:00:00+00:00",
    }

    normalized = normalize_candidate_source(candidate)

    assert normalized.evidence_text == (
        "OpenAI 推出分级处理框架及案例研究，用于报告模型失调问题"
    )


def test_hash_joined_promo_footer_is_removed_without_space():
    """A ``#``-joined footer must strip even with no whitespace before it.

    Regression from the 2026-10-14 draft: ifanr joined the teaser and the footer
    as ``...Scion#欢迎关注爱范儿官方微信公众号…`` with no space. The old guard only
    accepted a boundary of whitespace/sentence punctuation, so the strip was
    rejected and the whole line was published verbatim as a brief.
    """
    candidate = {
        "title": "刚刚，Anthropic 宣布弃用Git，统治编程20年的工具顶不住AI了",
        "source_summary": (
            "以 AI 发展速度，一年后我们又该弃用 Scion#欢迎关注爱范儿官方微信公众号："
            "爱范儿（微信号：ifanr），更多精彩内容第一时间为您奉上。"
        ),
        "url": "https://www.ifanr.com/1684269",
        "source": "Ifanr",
        "source_type": "rss",
        "published_at": "2026-10-14T02:00:00+00:00",
    }

    normalized = normalize_candidate_source(candidate)

    assert "欢迎关注" not in normalized.evidence_text
    assert "微信号" not in normalized.evidence_text
    assert "Scion" in normalized.evidence_text


def test_hash_then_promo_stub_is_removed():
    """``内容#点击查看原文`` keeps the body and drops the stub."""
    candidate = {
        "title": "OpenAI 推出分级处理框架及案例研究",
        "source_summary": "内容#点击查看原文",
        "url": "https://www.infoq.cn/article/abc",
        "source": "InfoQ 中文",
        "source_type": "rss",
        "published_at": "2026-10-14T02:00:00+00:00",
    }

    normalized = normalize_candidate_source(candidate)

    assert "点击查看原文" not in normalized.evidence_text
    assert "内容" in normalized.evidence_text


def test_promo_words_inside_prose_are_preserved():
    """A footer marker inside ordinary prose must not be truncated."""
    candidate = {
        "title": "微信宣布公众号功能升级，新增留言管理能力",
        "source_summary": "微信宣布公众号功能升级，新增留言管理能力",
        "url": "https://example.test/wechat-article",
        "source": "Example",
        "source_type": "rss",
        "published_at": "2026-09-23T00:00:00+00:00",
    }

    normalized = normalize_candidate_source(candidate)

    assert "公众号功能升级" in normalized.evidence_text
    assert normalized.evidence_text.endswith("新增留言管理能力")
