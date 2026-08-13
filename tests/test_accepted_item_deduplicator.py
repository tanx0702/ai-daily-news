from src.briefing.config import BriefingConfig
from src.briefing.deduplicator import AcceptedItemDeduplicator
from src.briefing.models import BriefItem, EvidenceBinding, MergedEvent, SourceEvidence
from src.briefing.semantic_reviewer import SemanticReview


def item(
    event_key: str,
    *,
    channel: str,
    authority: str,
    is_official: bool,
    publisher_id: str,
    title: str,
    brief: str,
    source_title: str,
    evidence_text: str,
    url: str,
) -> BriefItem:
    source = SourceEvidence(
        publisher_id=publisher_id,
        publisher_name=publisher_id,
        channel=channel,
        authority=authority,
        is_official=is_official,
        official_identity_source="source_config" if is_official else "",
        source_title=source_title,
        evidence_text=evidence_text,
        url=url,
        published_at="2026-08-11T17:00:00+00:00",
    )
    return BriefItem(
        event_key=event_key,
        chinese_title=title,
        brief=brief,
        canonical_source=source,
        related_sources=(),
        published_at=source.published_at,
        evidence_bindings=(EvidenceBinding(title, source_title, url),),
        content_origin="llm",
        validation_mode="rules_and_llm",
    )


RSS_ITEM = item(
    "event-rss",
    channel="rss",
    authority="professional_media",
    is_official=False,
    publisher_id="theverge-com",
    title="OpenAI 前 COO Brad Lightcap 宣布离职",
    brief="Brad Lightcap 在任职八年后宣布离开 OpenAI。",
    source_title="Another OpenAI executive takes off",
    evidence_text=(
        "Brad Lightcap, OpenAI's former COO, announced his departure after eight years."
    ),
    url="https://theverge.example/brad-lightcap",
)
X_ITEM = item(
    "event-x",
    channel="x",
    authority="community",
    is_official=False,
    publisher_id="community-x",
    title="Brad Lightcap 离开 OpenAI",
    brief="Brad Lightcap 在任职八年后离开 OpenAI。",
    source_title="OpenAI COO Brad Lightcap is leaving the company",
    evidence_text="OpenAI COO Brad Lightcap is leaving after eight years.",
    url="https://x.com/community/status/123",
)
OFFICIAL_ITEM = item(
    "event-official",
    channel="rss",
    authority="official",
    is_official=True,
    publisher_id="openai",
    title="OpenAI 宣布 Brad Lightcap 离职",
    brief="Brad Lightcap 在任职八年后宣布离开 OpenAI。",
    source_title="Brad Lightcap departs OpenAI",
    evidence_text="Brad Lightcap announced his departure from OpenAI after eight years.",
    url="https://openai.example/brad-lightcap",
)


def deduplicator():
    return AcceptedItemDeduplicator(BriefingConfig.from_env({}), reviewer=None)


class CountingSemanticReviewer:
    def __init__(self):
        self.diagnostics = {"semantic_llm_success_count": 7}

    def review(self, left, right):
        self.diagnostics["semantic_llm_success_count"] += 1
        return SemanticReview("same_event", "rules_and_llm")


def test_weaker_semantic_duplicate_is_rejected():
    outcome = deduplicator().evaluate(X_ITEM, [RSS_ITEM])

    assert outcome.accept_candidate is False
    assert outcome.removed_event_keys == ()
    assert outcome.duplicate_of == RSS_ITEM.event_key
    assert outcome.reason_code == "semantic_duplicate"
    assert outcome.comparison_mode == "rules"


def test_stronger_candidate_replaces_existing_item():
    outcome = deduplicator().evaluate(OFFICIAL_ITEM, [X_ITEM])

    assert outcome.accept_candidate is True
    assert outcome.removed_event_keys == (X_ITEM.event_key,)
    assert outcome.duplicate_of is None
    assert outcome.reason_code == "semantic_duplicate"


def test_replacement_precheck_uses_the_same_priority_as_final_evaluation():
    candidate = MergedEvent(
        "event-candidate",
        RSS_ITEM.canonical_source,
        editorial_score=100,
    )

    assert deduplicator().can_replace_any(candidate, [RSS_ITEM]) is False


def test_distinct_candidate_is_accepted_without_removal():
    distinct = item(
        "event-release",
        channel="rss",
        authority="professional_media",
        is_official=False,
        publisher_id="theverge-com",
        title="OpenAI 发布 Model 5",
        brief="OpenAI 发布了 Model 5。",
        source_title="OpenAI releases Model 5",
        evidence_text="OpenAI releases Model 5 through its API.",
        url="https://theverge.example/model-5",
    )

    outcome = deduplicator().evaluate(distinct, [RSS_ITEM])

    assert outcome.accept_candidate is True
    assert outcome.removed_event_keys == ()
    assert outcome.reason_code is None


def test_normalized_duplicate_url_is_compared_before_semantic_prefilter():
    original = item(
        "event-original-url",
        channel="rss",
        authority="professional_media",
        is_official=False,
        publisher_id="media-original",
        title="一家实验室公布更新",
        brief="一家实验室公布了新的技术更新。",
        source_title="A laboratory publishes an update",
        evidence_text="A laboratory published a technical update.",
        url="https://MEDIA.example/news/update?utm_source=rss",
    )
    repeat = item(
        "event-repeat-url",
        channel="rss",
        authority="professional_media",
        is_official=False,
        publisher_id="media-repeat",
        title="研究团队披露进展",
        brief="研究团队披露了一项新的进展。",
        source_title="Research team shares progress",
        evidence_text="The research team shared progress on its work.",
        url="https://media.example/news/update#details",
    )

    outcome = deduplicator().evaluate(repeat, [original])

    assert outcome.accept_candidate is True
    assert outcome.removed_event_keys == (original.event_key,)
    assert outcome.reason_code == "semantic_duplicate"


def test_unresolved_review_removes_lower_priority_candidate_without_human_review():
    first = item(
        "event-first",
        channel="rss",
        authority="professional_media",
        is_official=False,
        publisher_id="media-first",
        title="OpenAI 一名高管离职",
        brief="一名 OpenAI 高管宣布离职。",
        source_title="OpenAI executive takes off",
        evidence_text="An OpenAI executive announced a departure.",
        url="https://media.example/first",
    )
    second = item(
        "event-second",
        channel="x",
        authority="community",
        is_official=False,
        publisher_id="community-second",
        title="OpenAI 一位负责人离开公司",
        brief="一位 OpenAI 负责人正在离开公司。",
        source_title="OpenAI leader leaves company",
        evidence_text="An OpenAI leader is leaving the company.",
        url="https://x.com/community/status/456",
    )

    outcome = deduplicator().evaluate(second, [first])

    assert outcome.accept_candidate is False
    assert outcome.duplicate_of == first.event_key
    assert outcome.reason_code == "semantic_duplicate_unresolved"
    assert outcome.comparison_mode == "rules"


def test_stronger_unresolved_candidate_replaces_weaker_item_conservatively():
    weak = item(
        "event-weak",
        channel="x",
        authority="community",
        is_official=False,
        publisher_id="community-weak",
        title="OpenAI 一名高管离职",
        brief="一名 OpenAI 高管宣布离职。",
        source_title="OpenAI executive takes off",
        evidence_text="An OpenAI executive announced a departure.",
        url="https://x.com/community/status/999",
    )
    strong = item(
        "event-strong",
        channel="rss",
        authority="professional_media",
        is_official=False,
        publisher_id="media-strong",
        title="OpenAI 一位负责人离开公司",
        brief="一位 OpenAI 负责人正在离开公司。",
        source_title="OpenAI leader leaves company",
        evidence_text="An OpenAI leader is leaving the company.",
        url="https://media.example/strong",
    )

    outcome = deduplicator().evaluate(strong, [weak])

    assert outcome.accept_candidate is True
    assert outcome.removed_event_keys == (weak.event_key,)
    assert outcome.reason_code == "semantic_duplicate_unresolved"
    assert outcome.comparison_mode == "rules"


def test_deduplicator_reports_only_reviewer_counts_from_its_own_stage():
    reviewer = CountingSemanticReviewer()
    instance = AcceptedItemDeduplicator(
        BriefingConfig.from_env({}),
        reviewer=reviewer,
    )
    generic_rss = item(
        "event-generic-rss",
        channel="rss",
        authority="professional_media",
        is_official=False,
        publisher_id="media-rss",
        title="OpenAI 高管离职",
        brief="一名 OpenAI 高管宣布离职。",
        source_title="OpenAI executive takes off",
        evidence_text="An OpenAI executive announced a departure.",
        url="https://media.example/generic-rss",
    )
    generic_x = item(
        "event-generic-x",
        channel="x",
        authority="community",
        is_official=False,
        publisher_id="community-x",
        title="OpenAI 负责人离开公司",
        brief="一名 OpenAI 负责人正在离开公司。",
        source_title="OpenAI leader leaves company",
        evidence_text="An OpenAI leader is leaving the company.",
        url="https://x.com/community/status/1000",
    )

    outcome = instance.evaluate(generic_x, [generic_rss])

    assert outcome.accept_candidate is False
    assert instance.diagnostics["semantic_llm_success_count"] == 1
