import json

from src.briefing.builder import BriefBuilder
from src.briefing.config import BriefingConfig
from src.briefing.models import MergedEvent, SourceEvidence
from src.llm_config import LLMConfig


def event(index: int, *, chinese: bool = False) -> MergedEvent:
    if chinese:
        title = f"示例公司发布模型 {index}"
        evidence_text = f"示例公司发布模型 {index}。该模型提供新的文本能力。"
    else:
        title = f"Example releases Model {index}"
        evidence_text = f"Example releases Model {index}. The model adds a text API."
    source = SourceEvidence(
        publisher_id=f"example-{index}",
        publisher_name="Example",
        channel="rss",
        authority="official",
        is_official=True,
        official_identity_source="rss_source_config",
        source_title=title,
        evidence_text=evidence_text,
        url=f"https://example.com/model-{index}",
        published_at="2026-08-07T08:00:00+00:00",
    )
    return MergedEvent(
        event_key=f"event-{index}",
        canonical_evidence=source,
        editorial_score=10 - index,
    )


def generated_item(index: int, event_key: str, source_url: str) -> dict:
    return {
        "index": index,
        "event_key": event_key,
        "chinese_title": f"示例公司发布模型 {index}",
        "brief": f"示例公司发布模型 {index}。",
        "evidence_bindings": [
            {
                "claim": f"示例公司发布模型 {index}",
                "source_quote": f"Example releases Model {index}",
                "source_url": source_url,
            }
        ],
    }


class FakeResponse:
    def __init__(self, payload):
        content = payload if isinstance(payload, str) else json.dumps(payload)
        self.choices = [type("Choice", (), {"message": type("Message", (), {"content": content})()})()]


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response)


class FakeClient:
    def __init__(self, responses):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(responses)


def builder_with_responses(responses, *, api_key="key"):
    client = FakeClient(responses)
    builder = BriefBuilder(
        BriefingConfig.from_env({}),
        LLMConfig(api_key=api_key, model="model", base_url="https://llm.example/v1"),
        client_factory=lambda **_kwargs: client,
    )
    return builder, client


def test_builder_splits_requests_into_batches_of_at_most_five():
    events = [event(index) for index in range(1, 7)]
    first = {
        "items": [
            generated_item(index, item.event_key, item.canonical_evidence.url)
            for index, item in enumerate(events[:5], 1)
        ]
    }
    second = {
        "items": [generated_item(1, events[5].event_key, events[5].canonical_evidence.url)]
    }
    builder, client = builder_with_responses([first, second])

    results = builder.build_batch(events, attempts={})

    assert len(results) == 6
    assert all(result.draft is not None for result in results)
    assert len(client.chat.completions.calls) == 2
    request_sizes = [
        len(json.loads(call["messages"][1]["content"])["events"])
        for call in client.chat.completions.calls
    ]
    assert request_sizes == [5, 1]


def test_builder_requests_5000_output_tokens():
    item = event(1)
    payload = {
        "items": [generated_item(1, item.event_key, item.canonical_evidence.url)]
    }
    builder, client = builder_with_responses([payload])

    results = builder.build_batch([item], attempts={})

    assert results[0].draft is not None
    assert client.chat.completions.calls[0]["max_tokens"] == 5000


def test_builder_accepts_valid_indexed_schema_and_preserves_event_mapping():
    events = [event(1), event(2)]
    payload = {
        "items": [
            generated_item(2, events[1].event_key, events[1].canonical_evidence.url),
            generated_item(1, events[0].event_key, events[0].canonical_evidence.url),
        ]
    }
    builder, _ = builder_with_responses([payload])

    results = builder.build_batch(events, attempts={})

    assert [result.event_key for result in results] == ["event-1", "event-2"]
    assert [result.draft.input_index for result in results] == [1, 2]
    assert all(result.generation_attempt == 1 for result in results)


def test_missing_duplicate_and_unindexed_results_fail_only_affected_items():
    events = [event(1), event(2), event(3)]
    item_one = generated_item(1, "event-1", events[0].canonical_evidence.url)
    item_two_a = generated_item(2, "event-2", events[1].canonical_evidence.url)
    item_two_b = generated_item(2, "event-2", events[1].canonical_evidence.url)
    unindexed = generated_item(3, "event-3", events[2].canonical_evidence.url)
    del unindexed["index"]
    builder, _ = builder_with_responses(
        [{"items": [item_one, item_two_a, item_two_b, unindexed]}]
    )

    results = builder.build_batch(events, attempts={})

    assert results[0].draft is not None
    assert results[1].draft is None
    assert results[1].reason_code == "invalid_builder_response"
    assert results[2].draft is None
    assert results[2].reason_code == "invalid_builder_response"


def test_builder_rejects_wrong_types_and_event_key_mismatch():
    events = [event(1), event(2)]
    wrong_type = generated_item(1, "event-1", events[0].canonical_evidence.url)
    wrong_type["brief"] = ["not", "a", "string"]
    wrong_event = generated_item(2, "other-event", events[1].canonical_evidence.url)
    builder, _ = builder_with_responses([{"items": [wrong_type, wrong_event]}])

    results = builder.build_batch(events, attempts={})

    assert [result.reason_code for result in results] == [
        "invalid_builder_response",
        "invalid_builder_response",
    ]


def test_second_invalid_attempt_uses_complete_chinese_source_fallback():
    item = event(1, chinese=True)
    builder, _ = builder_with_responses(["not-json"])

    results = builder.build_batch([item], attempts={item.event_key: 1})

    result = results[0]
    assert result.generation_attempt == 2
    assert result.reason_code == "source_fallback_used"
    assert result.draft.content_origin == "source"
    assert result.draft.chinese_title == item.canonical_evidence.source_title
    assert result.draft.brief
    assert len(result.draft.evidence_bindings) >= 2
    assert all(
        binding.source_url == item.canonical_evidence.url
        for binding in result.draft.evidence_bindings
    )


def test_source_fallback_keeps_each_summary_sentence_in_chinese():
    item = event(1, chinese=True)
    source = item.canonical_evidence
    item = MergedEvent(
        event_key=item.event_key,
        canonical_evidence=SourceEvidence(
            publisher_id=source.publisher_id,
            publisher_name=source.publisher_name,
            channel=source.channel,
            authority=source.authority,
            is_official=source.is_official,
            official_identity_source=source.official_identity_source,
            source_title=source.source_title,
            evidence_text=f"{source.source_title}。English-only detail.",
            url=source.url,
            published_at=source.published_at,
        ),
    )
    builder, _ = builder_with_responses(["not-json"])

    result = builder.build_batch([item], attempts={item.event_key: 1})[0]

    assert result.draft.brief == item.canonical_evidence.source_title
    assert all(
        any("\u4e00" <= character <= "\u9fff" for character in sentence)
        for sentence in result.draft.brief.split("。")
        if sentence
    )


def test_source_fallback_binds_each_comma_separated_display_claim():
    item = event(1, chinese=True)
    source = item.canonical_evidence
    item = MergedEvent(
        event_key=item.event_key,
        canonical_evidence=SourceEvidence(
            publisher_id=source.publisher_id,
            publisher_name=source.publisher_name,
            channel=source.channel,
            authority=source.authority,
            is_official=source.is_official,
            official_identity_source=source.official_identity_source,
            source_title=source.source_title,
            evidence_text=(
                f"{source.source_title}。该模型提供新的文本能力，并公开 API 说明。"
            ),
            url=source.url,
            published_at=source.published_at,
        ),
    )
    builder, _ = builder_with_responses([], api_key="")

    result = builder.build_batch([item], attempts={})[0]
    bound_claims = {binding.claim for binding in result.draft.evidence_bindings}

    assert "该模型提供新的文本能力" in bound_claims
    assert "并公开 API 说明" in bound_claims


def test_builder_returns_attempt_one_to_the_caller_before_attempt_two_fallback():
    item = event(1, chinese=True)
    builder, client = builder_with_responses(["not-json", "not-json"])

    first = builder.build_batch([item], attempts={})[0]
    second = builder.build_batch(
        [item], attempts={item.event_key: first.generation_attempt}
    )[0]

    assert first.generation_attempt == 1
    assert first.draft is None
    assert first.reason_code == "invalid_builder_response"
    assert second.generation_attempt == 2
    assert second.reason_code == "source_fallback_used"
    assert second.draft is not None
    assert len(client.chat.completions.calls) == 2


def test_second_invalid_attempt_excludes_english_translation_failure():
    item = event(1)
    builder, _ = builder_with_responses(["not-json"])

    result = builder.build_batch([item], attempts={item.event_key: 1})[0]

    assert result.draft is None
    assert result.reason_code == "translation_failed"
    assert result.generation_attempt == 2


def test_nonrecoverable_error_opens_circuit_and_avoids_later_calls():
    chinese = event(1, chinese=True)
    english = event(2)
    builder, client = builder_with_responses([RuntimeError("401 invalid api key")])

    first = builder.build_batch([chinese], attempts={})[0]
    second = builder.build_batch([english], attempts={})[0]

    assert first.draft.content_origin == "source"
    assert first.circuit_open is True
    assert second.reason_code == "translation_failed"
    assert second.circuit_open is True
    assert len(client.chat.completions.calls) == 1


def test_missing_llm_configuration_uses_source_or_translation_failure_without_call():
    builder, client = builder_with_responses([], api_key="")

    chinese, english = builder.build_batch(
        [event(1, chinese=True), event(2)],
        attempts={},
    )

    assert chinese.draft.content_origin == "source"
    assert chinese.reason_code == "source_fallback_used"
    assert english.draft is None
    assert english.reason_code == "translation_failed"
    assert client.chat.completions.calls == []
