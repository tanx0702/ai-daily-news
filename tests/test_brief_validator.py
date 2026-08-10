import json
from datetime import datetime, timezone

from src.briefing.config import BriefingConfig
from src.briefing.models import BuiltBrief, EvidenceBinding, MergedEvent, SourceEvidence
from src.briefing.validator import BriefValidator
from src.llm_config import LLMConfig


NOW = datetime(2026, 8, 7, 12, tzinfo=timezone.utc)


def event(**source_overrides):
    values = {
        "publisher_id": "openai",
        "publisher_name": "OpenAI",
        "channel": "rss",
        "authority": "official",
        "is_official": True,
        "official_identity_source": "rss_source_config",
        "source_title": "OpenAI 发布 Model 5",
        "evidence_text": "OpenAI&nbsp;发布 Model 5。该模型提供文本 API。",
        "url": "https://openai.com/news/model-5",
        "published_at": "2026-08-07T08:00:00+00:00",
    }
    values.update(source_overrides)
    source = SourceEvidence(**values)
    return MergedEvent(
        event_key="event-model-5",
        canonical_evidence=source,
        editorial_score=9.5,
    )


def draft(item=None, **overrides):
    item = item or event()
    values = {
        "event_key": item.event_key,
        "input_index": 1,
        "chinese_title": "OpenAI 发布 Model 5",
        "brief": "OpenAI 发布 Model 5。该模型提供文本 API。",
        "evidence_bindings": (
            EvidenceBinding(
                "OpenAI 发布 Model 5",
                "OpenAI 发布 Model 5",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "该模型提供文本 API",
                "该模型提供文本 API",
                item.canonical_evidence.url,
            ),
        ),
        "content_origin": "llm",
    }
    values.update(overrides)
    return BuiltBrief(**values)


def validator(*, quality_config=None, client_factory=None):
    return BriefValidator(
        BriefingConfig.from_env({}),
        quality_config,
        client_factory=client_factory,
    )


def test_validator_accepts_quotes_after_html_entity_and_whitespace_normalization():
    item = event()

    result = validator().validate(item, draft(item), generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert result.validation_mode == "rules_only"
    assert result.validated_item.chinese_title == "OpenAI 发布 Model 5"
    assert result.validated_item.evidence_bindings == draft(item).evidence_bindings


def test_validator_requests_rebuild_for_quote_missing_from_canonical_evidence():
    item = event()
    unsupported = draft(
        item,
        evidence_bindings=(
            EvidenceBinding(
                "OpenAI 发布 Model 5",
                "This quote is absent",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, unsupported, generation_attempt=1, now=NOW)

    assert result.action == "rebuild"
    assert "unsupported_claim" in result.reason_codes
    assert result.rebuild_request.generation_attempt == 2


def test_validator_rejects_unrelated_quote_even_when_quote_is_real():
    item = event(evidence_text="OpenAI 发布 Model 5。巴黎今天下雨。")
    unrelated = draft(
        item,
        brief="OpenAI 发布 Model 5。",
        evidence_bindings=(
            EvidenceBinding(
                "OpenAI 发布 Model 5",
                "巴黎今天下雨",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, unrelated, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert "unsupported_claim" in result.reason_codes


def test_validator_rejects_a_substring_binding_that_hides_an_added_clause():
    item = event()
    expanded = draft(
        item,
        brief="OpenAI 发布 Model 5，并降低用户成本。",
        evidence_bindings=(
            EvidenceBinding(
                "OpenAI 发布 Model 5",
                "OpenAI 发布 Model 5",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, expanded, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert "unsupported_claim" in result.reason_codes


def test_validator_requires_chinese_in_the_title_and_each_summary_sentence():
    item = event()
    variants = [
        draft(
            item,
            chinese_title="OpenAI releases Model 5",
            evidence_bindings=(
                EvidenceBinding(
                    "OpenAI releases Model 5",
                    "OpenAI 发布 Model 5",
                    item.canonical_evidence.url,
                ),
                EvidenceBinding(
                    "该模型提供文本 API",
                    "该模型提供文本 API",
                    item.canonical_evidence.url,
                ),
            ),
        ),
        draft(
            item,
            brief="OpenAI provides a text API.",
            evidence_bindings=(
                EvidenceBinding(
                    "OpenAI 发布 Model 5",
                    "OpenAI 发布 Model 5",
                    item.canonical_evidence.url,
                ),
                EvidenceBinding(
                    "OpenAI provides a text API",
                    "该模型提供文本 API",
                    item.canonical_evidence.url,
                ),
            ),
        ),
    ]

    for generated in variants:
        result = validator().validate(item, generated, generation_attempt=2, now=NOW)
        assert result.action == "reject"
        assert result.reason_codes == ("invalid_builder_response",)


def test_validator_does_not_remove_punctuation_when_matching_quotes():
    item = event(evidence_text="OpenAI: 发布 Model 5。该模型提供文本 API。")
    changed_quote = draft(
        item,
        evidence_bindings=(
            EvidenceBinding(
                "OpenAI 发布 Model 5",
                "OpenAI 发布 Model 5",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "该模型提供文本 API",
                "该模型提供文本 API",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, changed_quote, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert "unsupported_claim" in result.reason_codes


def test_validator_rejects_added_model_number_money_and_date():
    item = event()
    added = draft(
        item,
        chinese_title="OpenAI 于 8 月 8 日发布 Model 6",
        brief="OpenAI 以 1000 万美元推出 Model 6。",
        evidence_bindings=(
            EvidenceBinding(
                "OpenAI 于 8 月 8 日发布 Model 6",
                "OpenAI 发布 Model 5",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "OpenAI 以 1000 万美元推出 Model 6",
                "OpenAI 发布 Model 5",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, added, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert "unsupported_claim" in result.reason_codes


def test_validator_rejects_company_action_without_action_evidence():
    item = event(evidence_text="OpenAI 与 Example 讨论了合作可能。")
    acquisition = draft(
        item,
        chinese_title="OpenAI 收购 Example",
        brief="OpenAI 收购 Example。",
        evidence_bindings=(
            EvidenceBinding(
                "OpenAI 收购 Example",
                "OpenAI 与 Example 讨论了合作可能",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, acquisition, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert "unsupported_claim" in result.reason_codes


def test_validator_rejects_non_official_x_written_as_company_announcement():
    item = event(
        publisher_id="researcher",
        publisher_name="某研究者",
        channel="x",
        authority="research",
        is_official=False,
        official_identity_source="",
        evidence_text="某研究者分享：OpenAI 发布 Model 5。",
        url="https://x.com/researcher/status/42",
    )
    overstated = draft(
        item,
        brief="OpenAI 发布 Model 5。",
        evidence_bindings=(
            EvidenceBinding(
                "OpenAI 发布 Model 5",
                "OpenAI 发布 Model 5",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, overstated, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert "community_claim_overstated" in result.reason_codes


def test_validator_accepts_non_official_x_with_explicit_attribution():
    item = event(
        publisher_id="researcher",
        publisher_name="某研究者",
        channel="x",
        authority="research",
        is_official=False,
        official_identity_source="",
        source_title="某研究者分享 OpenAI Model 5 信息",
        evidence_text="某研究者分享：OpenAI 发布 Model 5。",
        url="https://x.com/researcher/status/42",
    )
    attributed = draft(
        item,
        chinese_title="某研究者分享 OpenAI Model 5 信息",
        brief="该研究者称 OpenAI 发布 Model 5。",
        evidence_bindings=(
            EvidenceBinding(
                "某研究者分享 OpenAI Model 5 信息",
                "某研究者分享：OpenAI 发布 Model 5",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "该研究者称 OpenAI 发布 Model 5",
                "某研究者分享：OpenAI 发布 Model 5",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, attributed, generation_attempt=1, now=NOW)

    assert result.action == "accept"


def test_validator_rejects_missing_url_evidence_and_stale_time():
    missing_url = event(url="")
    missing_evidence = event(evidence_text="")
    stale = event(published_at="2026-08-05T00:00:00+00:00")

    assert validator().validate(
        missing_url, draft(missing_url), generation_attempt=1, now=NOW
    ).reason_codes == ("missing_source_url",)
    assert validator().validate(
        missing_evidence, draft(missing_evidence), generation_attempt=1, now=NOW
    ).reason_codes == ("missing_evidence",)
    assert validator().validate(
        stale, draft(stale), generation_attempt=1, now=NOW
    ).reason_codes == ("stale_item",)


def test_validator_rejects_github_activity_only_as_publication_fact():
    item = event(
        channel="github",
        authority="community",
        is_official=False,
        official_identity_source="",
        source_title="Example repository is recently active",
        evidence_text="The repository gained 500 stars and had 20 recent commits.",
        url="https://github.com/example/repo",
    )
    activity = draft(
        item,
        chinese_title="Example 项目近期活跃",
        brief="该项目获得 500 个 star，并有 20 次近期 commit。",
        evidence_bindings=(
            EvidenceBinding(
                "Example 项目近期活跃",
                "repository gained 500 stars",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "500 个 star，并有 20 次近期 commit",
                "500 stars and had 20 recent commits",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, activity, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert "github_activity_only" in result.reason_codes


def test_validator_rejects_commentary_and_trend_language():
    item = event()
    commentary = draft(
        item,
        chinese_title="OpenAI 发布值得关注的 Model 5",
        brief="这标志着行业趋势发生革命性变化。",
        evidence_bindings=(
            EvidenceBinding(
                "OpenAI 发布 Model 5",
                "OpenAI 发布 Model 5",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, commentary, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert "unsupported_claim" in result.reason_codes


class FakeResponse:
    def __init__(self, payload):
        content = payload if isinstance(payload, str) else json.dumps(payload)
        self.choices = [type("Choice", (), {"message": type("Message", (), {"content": content})()})()]


class FakeClient:
    def __init__(self, response):
        self.calls = []
        self.response = response
        completions = type("Completions", (), {})()
        completions.create = self.create
        chat = type("Chat", (), {})()
        chat.completions = completions
        self.chat = chat

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return FakeResponse(self.response)


def quality_validator(response):
    client = FakeClient(response)
    instance = validator(
        quality_config=LLMConfig(
            api_key="quality-key",
            model="quality-model",
            base_url="https://quality.example/v1",
        ),
        client_factory=lambda **_kwargs: client,
    )
    return instance, client


def review_item(action="accept", reasons=None, **extras):
    value = {
        "index": 1,
        "event_key": "event-model-5",
        "action": action,
        "reason_codes": reasons or [],
    }
    value.update(extras)
    return value


def test_valid_quality_review_marks_rules_and_llm_without_changing_content():
    instance, _ = quality_validator({"items": [review_item()]})
    original = draft()

    result = instance.validate(event(), original, generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert result.validation_mode == "rules_and_llm"
    assert result.validated_item.chinese_title == original.chinese_title
    assert result.validated_item.brief == original.brief


def test_quality_issue_rebuilds_once_then_rejects():
    response = {"items": [review_item("rebuild", ["unsupported_claim"])]}
    first_validator, _ = quality_validator(response)
    second_validator, _ = quality_validator(response)

    first = first_validator.validate(event(), draft(), generation_attempt=1, now=NOW)
    second = second_validator.validate(event(), draft(), generation_attempt=2, now=NOW)

    assert first.action == "rebuild"
    assert first.rebuild_request.generation_attempt == 2
    assert second.action == "reject"
    assert second.reason_codes == ("unsupported_claim",)


def test_quality_timeout_degrades_to_rules_only_acceptance():
    instance, _ = quality_validator(TimeoutError("quality timeout"))

    result = instance.validate(event(), draft(), generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert result.validation_mode == "rules_only"
    assert "quality_llm_unavailable" in result.reason_codes
    assert "rules_only_used" in result.reason_codes


def test_invalid_quality_schema_is_discarded_and_does_not_apply_fix():
    response = {
        "items": [
            review_item(
                "accept",
                [],
                fixed_title="未经验证的新标题",
            )
        ]
    }
    instance, _ = quality_validator(response)
    original = draft()

    result = instance.validate(event(), original, generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert result.validation_mode == "rules_only"
    assert "quality_llm_invalid_response" in result.reason_codes
    assert result.validated_item.chinese_title == original.chinese_title


def test_missing_or_duplicate_quality_index_degrades_to_rules_only():
    responses = [
        {"items": []},
        {"items": [review_item(), review_item()]},
        {"items": [{"event_key": "event-model-5", "action": "accept", "reason_codes": []}]},
    ]

    for response in responses:
        instance, _ = quality_validator(response)
        result = instance.validate(event(), draft(), generation_attempt=1, now=NOW)
        assert result.action == "accept"
        assert result.validation_mode == "rules_only"
        assert "quality_llm_invalid_response" in result.reason_codes
