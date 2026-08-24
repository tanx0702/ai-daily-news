from src.briefing.config import BriefingConfig
from src.briefing.models import BriefItem, EvidenceBinding, MergedEvent, SourceEvidence
from src.briefing.selector import BriefSelector


def event(
    key: str,
    *,
    score: float = 1.0,
    publisher: str = "Example",
    authority: str = "professional_media",
    channel: str = "rss",
    published_at: str = "2026-08-07T08:00:00+00:00",
    related=(),
    rank_reasons=(),
    content_type: str = "fact_event",
    opinion_author: str = "",
) -> MergedEvent:
    source = SourceEvidence(
        publisher_id=publisher.lower().replace(" ", "-"),
        publisher_name=publisher,
        channel=channel,
        authority=authority,
        is_official=authority == "official",
        official_identity_source="source_config" if authority == "official" else "",
        source_title=f"{publisher} update",
        evidence_text=f"{publisher} update.",
        url=f"https://example.test/{key}",
        published_at=published_at,
        content_type=content_type,
        opinion_author=opinion_author,
        opinion_eligible=content_type == "attributed_opinion",
        original_post=content_type == "attributed_opinion",
        context_complete=content_type == "attributed_opinion",
        stance_type="opinion" if content_type == "attributed_opinion" else "",
    )
    return MergedEvent(
        event_key=key,
        canonical_evidence=source,
        related_evidence=tuple(related),
        editorial_score=score,
        rank_reasons=tuple(rank_reasons),
    )


def item(value: MergedEvent) -> BriefItem:
    source = value.canonical_evidence
    return BriefItem(
        event_key=value.event_key,
        chinese_title="示例更新",
        brief="示例更新。",
        canonical_source=source,
        related_sources=value.related_evidence,
        published_at=source.published_at,
        evidence_bindings=(
            EvidenceBinding("示例更新", source.evidence_text, source.url),
        ),
        content_origin="source",
        validation_mode="rules_only",
        content_type=source.content_type,
        opinion_author=source.opinion_author,
    )


def config(**values) -> BriefingConfig:
    defaults = {
        "min_items": 5,
        "max_items": 15,
        "candidate_pool_size": 45,
        "max_x_items": 5,
        "x_feed_max_age_hours": 6,
    }
    defaults.update(values)
    return BriefingConfig(**defaults)


def test_pending_orders_by_score_authority_date_then_event_key():
    events = [
        event("z", score=10, authority="professional_media"),
        event("b", score=10, authority="official"),
        event("a", score=10, authority="official"),
        event("newer", score=9, published_at="2026-08-07T09:00:00+00:00"),
        event("older", score=9, published_at="2026-08-07T08:00:00+00:00"),
    ]

    selector = BriefSelector(events, config())

    assert [value.event_key for value in selector.pending()] == [
        "a", "b", "z", "newer", "older",
    ]


def test_community_github_release_is_reserve_behind_professional_news():
    github_release = event(
        "small-release",
        score=100,
        publisher="GitHub",
        authority="community",
        channel="github",
    )
    media_report = event(
        "reported-news",
        score=10,
        publisher="Reuters",
        authority="professional_media",
    )

    selector = BriefSelector([github_release, media_report], config())

    assert [value.event_key for value in selector.pending()] == [
        "reported-news",
        "small-release",
    ]


def test_source_preferences_demote_without_deleting_events():
    events = [
        event("a-first", score=10, publisher="Publisher A"),
        event("a-second", score=9, publisher="Publisher A"),
        event("b", score=8, publisher="Publisher B"),
    ]

    selector = BriefSelector(events, config(max_items_per_source=1))

    assert [value.event_key for value in selector.pending()] == [
        "a-first", "b", "a-second",
    ]


def test_x_quota_is_occupied_only_after_acceptance_and_related_x_does_not_count():
    x_first = event("x-first", channel="x")
    x_second = event("x-second", channel="x")
    related_x = x_second.canonical_evidence
    rss = event("rss", channel="rss", related=(related_x,))
    selector = BriefSelector([x_first, x_second, rss], config(max_x_items=1))

    selector.reject(x_first.event_key, "unsupported_claim")
    assert selector.accept(item(x_second)) is True
    assert selector.x_count == 1
    assert selector.accept(item(rss)) is True
    assert selector.x_count == 1
    assert "x-first" not in [value.event_key for value in selector.pending()]


def test_x_disabled_and_quarantined_events_never_enter_pending():
    selector = BriefSelector(
        [event("x", channel="x"), event("safe", channel="rss"), event("bad")],
        config(max_x_items=0),
        quarantined_keys=("bad",),
    )

    assert [value.event_key for value in selector.pending()] == ["safe"]
    assert selector.excluded_counts["x_limit"] == 1


def test_duplicate_input_event_keys_are_recorded_instead_of_silently_disappearing():
    selector = BriefSelector(
        [event("same", score=2), event("same", score=1)],
        config(),
    )

    assert len(selector.pending()) == 1
    assert selector.excluded_counts["duplicate_event"] == 1


def test_selector_atomically_replaces_an_accepted_item():
    weak = event("weak", channel="x")
    strong = event("strong", channel="rss")
    selector = BriefSelector([weak, strong], config(max_x_items=1))
    weak_item = item(weak)
    strong_item = item(strong)

    assert selector.accept(weak_item) is True
    assert selector.replace_accepted(
        strong_item,
        remove_event_keys=(weak.event_key,),
    ) is True

    assert selector.accepted_items == (strong_item,)
    assert selector.x_count == 0
    assert weak.event_key not in [value.event_key for value in selector.pending()]


def test_selector_restores_x_limited_candidate_after_replacement_frees_quota():
    weak = event("weak", channel="x")
    waiting = event("waiting", channel="x")
    strong = event("strong", channel="rss")
    selector = BriefSelector([weak, waiting, strong], config(max_x_items=1))

    assert selector.accept(item(weak)) is True
    assert selector.accept(item(waiting)) is False
    assert waiting.event_key not in [value.event_key for value in selector.pending()]

    assert selector.replace_accepted(
        item(strong),
        remove_event_keys=(weak.event_key,),
    ) is True

    assert waiting.event_key in [value.event_key for value in selector.pending()]
    assert selector.excluded_counts.get("x_limit", 0) == 0


def test_selector_rejects_opinion_limit_and_duplicate_author():
    opinions = [
        event(
            f"opinion-{index}",
            channel="x",
            content_type="attributed_opinion",
            opinion_author="Same Author" if index == 2 else f"Author {index}",
        )
        for index in range(1, 5)
    ]
    selector = BriefSelector(opinions, config(max_opinion_items=3, max_x_items=8))

    assert selector.accept(item(opinions[0])) is True
    assert selector.accept(item(opinions[1])) is True
    assert selector.accept(item(opinions[2])) is True
    assert selector.accept(item(opinions[3])) is False
    assert selector.opinion_count == 3

    duplicate = event(
        "duplicate-author",
        channel="x",
        content_type="attributed_opinion",
        opinion_author="Author 1",
    )
    duplicate_selector = BriefSelector(
        [opinions[0], duplicate], config(max_opinion_items=3, max_x_items=8)
    )
    assert duplicate_selector.accept(item(opinions[0])) is True
    assert duplicate_selector.accept(item(duplicate)) is False


def test_selector_enforces_update_and_opinion_caps():
    updates = [event(f"update-{index}", content_type="ai_update") for index in range(8)]
    opinions = [
        event(
            f"opinion-{index}",
            content_type="attributed_opinion",
            opinion_author=f"Author {index}",
        )
        for index in range(8)
    ]
    ninth_update = event("update-9", content_type="ai_update")
    ninth_opinion = event(
        "opinion-9",
        content_type="attributed_opinion",
        opinion_author="Author 9",
    )
    selector = BriefSelector(
        [*updates, *opinions, ninth_update, ninth_opinion],
        config(max_items=20, max_x_items=20, max_update_items=8, max_opinion_items=8),
    )

    for value in updates + opinions:
        assert selector.accept(item(value)) is True

    assert selector.accept(item(ninth_update)) is False
    assert selector.accept(item(ninth_opinion)) is False
    assert selector.update_count == 8
