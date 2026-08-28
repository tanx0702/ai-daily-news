from datetime import datetime, timezone

from src.briefing.evidence import source_evidence_from_candidate
from src.briefing.opinion import evaluate_opinion_candidate


def _candidate(**overrides):
    candidate = {
        "id": "x-42",
        "title": "Andrej Karpathy: I think open models will win",
        "url": "https://x.com/karpathy/status/42",
        "source": "Andrej Karpathy (X)",
        "source_type": "x",
        "published_at": datetime(2026, 8, 18, tzinfo=timezone.utc),
        "published_source": "x_feed",
        "summary": "I think open models will win because they are easier to adapt.",
        "author": "Andrej Karpathy",
        "source_tier": "research",
        "x_handle": "karpathy",
        "x_tweet_id": "42",
        "x_thread_id": "42",
        "x_reply_to_id": "",
        "x_quoted_id": "",
        "x_is_repost": False,
        "x_context_complete": True,
        "opinion_eligible": True,
        "content_type": "attributed_opinion",
        "opinion_author": "Andrej Karpathy",
        "opinion_original_post": True,
        "opinion_context_complete": True,
        "opinion_stance_type": "opinion",
    }
    candidate.update(overrides)
    return candidate


def _eligible_source():
    return {
        "name": "Andrej Karpathy",
        "handle": "karpathy",
        "tier": "research",
        "official": False,
        "opinion_eligible": True,
    }


def test_original_eligible_person_with_substantive_claim_is_opinion():
    result = evaluate_opinion_candidate(_candidate(), _eligible_source())

    assert result.eligible is True
    assert result.original_post is True
    assert result.stance_type in {"opinion", "prediction", "critique", "comparison"}


def test_high_confidence_ai_stances_are_opinions():
    cases = (
        "Not every AI task benefits from more compute, because workflow context matters.",
        "人类对 AI Agents 的监督能力需要跟上智能体自主执行任务的发展速度。",
    )

    for summary in cases:
        result = evaluate_opinion_candidate(
            _candidate(summary=summary),
            _eligible_source(),
        )

        assert result.eligible is True, summary


def test_whitelisted_non_ai_stances_are_rejected():
    cases = (
        "I think the tennis final will be better than last year's match by far.",
        "In my view this election policy will fail because voters expect better results.",
    )

    for summary in cases:
        result = evaluate_opinion_candidate(
            _candidate(summary=summary),
            _eligible_source(),
        )

        assert result.eligible is False, summary
        assert result.reason_codes == ("opinion_no_ai_topic",)


def test_ai_live_announcement_without_stance_is_not_an_opinion():
    result = evaluate_opinion_candidate(
        _candidate(
            summary="Live on CNN tonight to discuss AI policy and recent model developments."
        ),
        _eligible_source(),
    )

    assert result.eligible is False
    assert result.reason_codes == ("opinion_no_substantive_claim",)


def test_model_release_party_announcement_is_promotional_not_opinion():
    result = evaluate_opinion_candidate(
        _candidate(
            summary=(
                "I think we should do another party for our next model release, "
                "because the last party was a lot of fun."
            )
        ),
        _eligible_source(),
    )

    assert result.eligible is False
    assert result.reason_codes == ("opinion_promotional_content",)


def test_repost_and_missing_reply_context_are_rejected():
    repost = evaluate_opinion_candidate(_candidate(x_is_repost=True), _eligible_source())
    missing_context = evaluate_opinion_candidate(
        _candidate(x_reply_to_id="41", x_context_complete=False), _eligible_source()
    )

    assert "opinion_repost_only" in repost.reason_codes
    assert "opinion_context_missing" in missing_context.reason_codes


def test_quote_without_substantive_own_comment_is_rejected():
    result = evaluate_opinion_candidate(
        _candidate(x_quoted_id="41", summary="https://x.com/other/status/41"),
        _eligible_source(),
    )

    assert "opinion_repost_only" in result.reason_codes


def test_promotional_content_is_rejected():
    result = evaluate_opinion_candidate(
        _candidate(summary="Join our course and register for the AI workshop"),
        _eligible_source(),
    )

    assert result.eligible is False
    assert "opinion_promotional_content" in result.reason_codes


def test_noneligible_media_account_cannot_become_opinion():
    result = evaluate_opinion_candidate(
        _candidate(), {"opinion_eligible": False, "tier": "media"}
    )

    assert result.eligible is False
    assert result.reason_codes == ("opinion_author_not_allowed",)


def test_opinion_metadata_round_trips_in_source_evidence():
    evidence = source_evidence_from_candidate(_candidate(), trusted_x_collector=True)

    assert evidence is not None
    assert evidence.content_type == "attributed_opinion"
    assert evidence.opinion_eligible is True
    assert source_evidence_from_candidate(
        _candidate(content_type="fact_event", opinion_original_post=False),
        trusted_x_collector=True,
    ).content_type == "fact_event"
