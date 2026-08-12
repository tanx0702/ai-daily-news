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
