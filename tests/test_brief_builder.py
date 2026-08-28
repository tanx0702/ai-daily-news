from dataclasses import replace
import json

from src.briefing.builder import BriefBuilder, _source_quotes
from src.briefing.config import BriefingConfig
from src.briefing.models import MergedEvent, SourceEvidence
from src.llm_config import LLMConfig


def event(
    index: int,
    *,
    chinese: bool = False,
    content_type: str = "fact_event",
) -> MergedEvent:
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
        content_type=content_type,
    )
    return MergedEvent(
        event_key=f"event-{index}",
        canonical_evidence=source,
        editorial_score=10 - index,
    )


def generated_item(index: int, event_key: str, _source_url: str) -> dict:
    return {
        "index": index,
        "event_key": event_key,
        "chinese_title": f"示例公司发布模型 {index}",
        "brief": f"示例公司发布模型 {index}。",
        "evidence_targets": [
            {
                "target": "title",
                "source_quote_id": "q1",
            },
            {
                "target": "brief_1",
                "source_quote_id": "q1",
            },
        ],
    }


def test_source_quotes_are_stable_verbatim_segments():
    evidence = "Model 4.5 scores 10.2 points. Next result!\nNext result!"

    quotes = _source_quotes(evidence)

    assert quotes == (
        ("q1", "Model 4.5 scores 10.2 points."),
        ("q2", "Next result!"),
    )
    assert all(quote in evidence for _quote_id, quote in quotes)


def test_builder_sends_verbatim_quote_candidates_and_requests_ids():
    item = event(1)
    builder, client = builder_with_responses(
        [{"items": [generated_item(1, item.event_key, item.canonical_evidence.url)]}]
    )

    builder.build_batch([item], attempts={})

    call = client.chat.completions.calls[0]
    request_event = json.loads(call["messages"][1]["content"])["events"][0]
    assert request_event["source_quotes"] == [
        {"quote_id": "q1", "text": "Example releases Model 1."},
        {"quote_id": "q2", "text": "The model adds a text API."},
    ]
    prompt = call["messages"][0]["content"]
    assert "source_quote_id" in prompt
    assert "返回 target/source_quote/source_url" not in prompt


def test_builder_resolves_quote_ids_to_verbatim_quotes_and_canonical_url():
    item = event(1)
    payload = generated_item(1, item.event_key, item.canonical_evidence.url)
    payload["evidence_targets"] = [
        {"target": "title", "source_quote_id": "q1"},
        {"target": "title", "source_quote_id": "q2"},
        {"target": "brief_1", "source_quote_id": "q1"},
    ]
    builder, _ = builder_with_responses([{"items": [payload]}])

    result = builder.build_batch([item], attempts={})[0]

    assert [binding.source_quote for binding in result.draft.evidence_bindings] == [
        "Example releases Model 1.",
        "The model adds a text API.",
        "Example releases Model 1.",
    ]
    assert all(
        binding.source_url == item.canonical_evidence.url
        for binding in result.draft.evidence_bindings
    )


def test_builder_rejects_unknown_title_quote_id():
    item = event(1)
    payload = generated_item(1, item.event_key, item.canonical_evidence.url)
    payload["evidence_targets"][0]["source_quote_id"] = "q999"
    builder, _ = builder_with_responses([{"items": [payload]}])

    result = builder.build_batch([item], attempts={})[0]

    assert result.draft is None
    assert result.reason_code == "builder_item_malformed"


def test_builder_drops_brief_when_any_summary_quote_id_is_unknown():
    item = event(1)
    payload = generated_item(1, item.event_key, item.canonical_evidence.url)
    payload["evidence_targets"][1]["source_quote_id"] = "q999"
    builder, _ = builder_with_responses([{"items": [payload]}])

    result = builder.build_batch([item], attempts={})[0]

    assert result.draft.brief == ""
    assert result.draft.brief_mode == "title_only"
    assert result.draft.brief_reason == "brief_quote_unresolved"
    assert [binding.claim for binding in result.draft.evidence_bindings] == [
        result.draft.chinese_title
    ]


def test_builder_drops_all_summary_bindings_when_claim_matches_title():
    item = event(1)
    payload = generated_item(1, item.event_key, item.canonical_evidence.url)
    payload["evidence_targets"].extend(
        [
            {"target": "brief_1", "source_quote_id": "q2"},
            {"target": "brief_1", "source_quote_id": "q999"},
        ]
    )
    builder, _ = builder_with_responses([{"items": [payload]}])

    result = builder.build_batch([item], attempts={})[0]

    assert result.draft.brief_mode == "title_only"
    assert len(result.draft.evidence_bindings) == 1
    assert result.draft.evidence_bindings[0].claim == result.draft.chinese_title


def test_builder_rejects_legacy_copied_quote_response():
    item = event(1)
    payload = generated_item(1, item.event_key, item.canonical_evidence.url)
    payload["evidence_targets"][0] = {
        "target": "title",
        "source_quote": item.canonical_evidence.source_title,
        "source_url": item.canonical_evidence.url,
    }
    builder, _ = builder_with_responses([{"items": [payload]}])

    result = builder.build_batch([item], attempts={})[0]

    assert result.draft is None
    assert result.reason_code == "builder_item_malformed"


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


def builder_with_responses(
    responses,
    *,
    api_key="key",
    model="model",
    builder_batch_size=None,
):
    client = FakeClient(responses)
    config = BriefingConfig.from_env({})
    if builder_batch_size is not None:
        config = replace(config, builder_batch_size=builder_batch_size)
    builder = BriefBuilder(
        config,
        LLMConfig(api_key=api_key, model=model, base_url="https://llm.example/v1"),
        client_factory=lambda **_kwargs: client,
    )
    return builder, client


def test_builder_sends_one_event_per_request():
    events = [event(index) for index in range(1, 7)]
    responses = [
        {
            "items": [
                generated_item(
                    1,
                    item.event_key,
                    item.canonical_evidence.url,
                )
            ]
        }
        for item in events
    ]
    builder, client = builder_with_responses(responses)

    results = builder.build_batch(events, attempts={})

    assert len(results) == 6
    assert all(result.draft is not None for result in results)
    assert len(client.chat.completions.calls) == 6
    request_sizes = [
        len(json.loads(call["messages"][1]["content"])["events"])
        for call in client.chat.completions.calls
    ]
    assert request_sizes == [1, 1, 1, 1, 1, 1]


def test_builder_disables_sdk_retries_to_keep_timeout_budget_bounded():
    events = [event(1)]
    payload = {
        "items": [generated_item(1, events[0].event_key, events[0].canonical_evidence.url)]
    }
    client = FakeClient([payload])
    captured = {}
    builder = BriefBuilder(
        BriefingConfig.from_env({}),
        LLMConfig("key", "model", "https://llm.example/v1"),
        client_factory=lambda **kwargs: (captured.update(kwargs) or client),
    )

    builder.build_batch(events, attempts={})

    assert captured["max_retries"] == 0


def test_builder_requests_5000_output_tokens():
    item = event(1)
    payload = {
        "items": [generated_item(1, item.event_key, item.canonical_evidence.url)]
    }
    builder, client = builder_with_responses([payload])

    results = builder.build_batch([item], attempts={})

    assert results[0].draft is not None
    assert client.chat.completions.calls[0]["max_tokens"] == 5000
    assert builder.diagnostics["content_llm_success_count"] == 1


def test_builder_uses_low_reasoning_effort_for_glm_5_3_flash():
    item = event(1)
    payload = {
        "items": [generated_item(1, item.event_key, item.canonical_evidence.url)]
    }
    builder, client = builder_with_responses([payload], model="glm-5.3-flash")

    builder.build_batch([item], attempts={})

    assert client.chat.completions.calls[0]["extra_body"] == {
        "reasoning_effort": "low"
    }


def test_builder_does_not_send_glm_options_to_other_models():
    item = event(1)
    payload = {
        "items": [generated_item(1, item.event_key, item.canonical_evidence.url)]
    }
    builder, client = builder_with_responses([payload], model="deepseek-chat")

    builder.build_batch([item], attempts={})

    assert "extra_body" not in client.chat.completions.calls[0]


def test_builder_requests_entity_anchored_quotes_for_cross_language_targets():
    item = event(1)
    payload = {
        "items": [generated_item(1, item.event_key, item.canonical_evidence.url)]
    }
    builder, client = builder_with_responses([payload])

    builder.build_batch([item], attempts={})

    system_prompt = client.chat.completions.calls[0]["messages"][0]["content"]
    assert "跨语言" in system_prompt
    assert "产品、模型或机构名称" in system_prompt
    assert "允许 brief 为空字符串" in system_prompt
    assert "标题之外" in system_prompt


def test_builder_limits_cross_language_titles_to_verifiable_anchors():
    item = event(1)
    payload = {
        "items": [generated_item(1, item.event_key, item.canonical_evidence.url)]
    }
    builder, client = builder_with_responses([payload])

    builder.build_batch([item], attempts={})

    system_prompt = client.chat.completions.calls[0]["messages"][0]["content"]
    assert "非实体、非数字细节" in system_prompt
    assert "保留原文锚点" in system_prompt


def test_builder_preserves_ai_update_type_and_forbids_release_rewrite():
    item = event(1, content_type="ai_update")
    payload = {
        "items": [generated_item(1, item.event_key, item.canonical_evidence.url)]
    }
    builder, client = builder_with_responses([payload])

    result = builder.build_batch([item], attempts={})[0]

    assert result.draft is not None
    assert result.draft.content_type == "ai_update"
    system_prompt = client.chat.completions.calls[0]["messages"][0]["content"]
    assert "content_type=ai_update" in system_prompt
    assert "能力演示、实测观察、工作流或工具进展" in system_prompt
    assert "数字和榜单不是必需" in system_prompt
    assert "不得改写成正式发布" in system_prompt
    assert "不得改写成确定性行业结论" in system_prompt


def test_second_unbound_cross_language_rebuild_uses_safe_source_fallback():
    original = event(1)
    source = original.canonical_evidence
    nvidia_event = MergedEvent(
        event_key=original.event_key,
        canonical_evidence=SourceEvidence(
            publisher_id="reuters-com",
            publisher_name="Reuters",
            channel="rss",
            authority="professional_media",
            is_official=False,
            official_identity_source="",
            source_title=(
                "Nvidia dramatically reduces amount of OpenAI infra financing it may guarantee"
            ),
            evidence_text=(
                "Nvidia dramatically reduces amount of OpenAI infra financing it may guarantee"
            ),
            url=source.url,
            published_at=source.published_at,
        ),
    )
    payload = {"items": [generated_item(1, nvidia_event.event_key, source.url)]}
    builder, _ = builder_with_responses([payload])

    result = builder.build_batch(
        [nvidia_event],
        attempts={nvidia_event.event_key: 1},
        rebuild_reasons={nvidia_event.event_key: ("title_claim_not_source_bound",)},
    )[0]

    assert result.source_fallback_used is True
    assert result.reason_code == "title_claim_not_source_bound"
    assert result.draft is not None
    assert result.draft.content_origin == "source"
    assert result.draft.chinese_title == "Nvidia 减少 OpenAI"
    assert result.draft.brief == ""


def test_second_missing_action_rebuild_uses_safe_source_fallback():
    original = event(1)
    source = original.canonical_evidence
    sainsburys_event = MergedEvent(
        event_key=original.event_key,
        canonical_evidence=SourceEvidence(
            publisher_id="bbc-co-uk",
            publisher_name="BBC",
            channel="rss",
            authority="professional_media",
            is_official=False,
            official_identity_source="",
            source_title="Sainsbury's pauses AI cameras after shopper ousted",
            evidence_text="Sainsbury's pauses AI cameras after shopper ousted",
            url=source.url,
            published_at=source.published_at,
        ),
    )
    payload = {"items": [generated_item(1, sainsburys_event.event_key, source.url)]}
    builder, _ = builder_with_responses([payload])

    result = builder.build_batch(
        [sainsburys_event],
        attempts={sainsburys_event.event_key: 1},
        rebuild_reasons={sainsburys_event.event_key: ("title_missing_event_action",)},
    )[0]

    assert result.source_fallback_used is True
    assert result.reason_code == "title_missing_event_action"
    assert result.draft is not None
    assert result.draft.chinese_title == "Sainsbury's 暂停 cameras"


def test_builder_accepts_title_only_response_without_summary_target():
    item = event(1)
    payload = generated_item(1, item.event_key, item.canonical_evidence.url)
    payload["brief"] = ""
    payload["evidence_targets"] = [payload["evidence_targets"][0]]
    builder, _ = builder_with_responses([{"items": [payload]}])

    result = builder.build_batch([item], attempts={})[0]

    assert result.draft is not None
    assert result.draft.brief == ""
    assert result.draft.brief_mode == "title_only"
    assert result.draft.brief_reason == "brief_empty"


def test_source_fallback_uses_title_only_when_evidence_has_no_extra_fact():
    item = event(1, chinese=True)
    source = item.canonical_evidence
    title_only = MergedEvent(
        event_key=item.event_key,
        canonical_evidence=SourceEvidence(
            publisher_id=source.publisher_id,
            publisher_name=source.publisher_name,
            channel=source.channel,
            authority=source.authority,
            is_official=source.is_official,
            official_identity_source=source.official_identity_source,
            source_title=source.source_title,
            evidence_text=source.source_title,
            url=source.url,
            published_at=source.published_at,
        ),
    )
    builder, _ = builder_with_responses([], api_key="")

    result = builder.build_batch([title_only], attempts={})[0]

    assert result.draft is not None
    assert result.draft.brief == ""
    assert result.draft.brief_mode == "title_only"
    assert [binding.claim for binding in result.draft.evidence_bindings] == [
        source.source_title
    ]


def test_x_rebuild_payload_preserves_failure_reasons_and_protected_anchors():
    original = event(1)
    source = original.canonical_evidence
    x_event = MergedEvent(
        event_key=original.event_key,
        canonical_evidence=SourceEvidence(
            publisher_id="xai-x",
            publisher_name="xAI",
            channel="x",
            authority="official",
            is_official=True,
            official_identity_source="x_source_config",
            source_title="xAI releases Grok 4.6",
            evidence_text="xAI releases Grok 4.6 with a new API.",
            url="https://x.com/xai/status/1",
            published_at=source.published_at,
        ),
    )
    payload = {
        "items": [generated_item(1, x_event.event_key, x_event.canonical_evidence.url)]
    }
    builder, client = builder_with_responses([payload])

    builder.build_batch(
        [x_event],
        attempts={x_event.event_key: 1},
        rebuild_reasons={x_event.event_key: ("protected_token_missing",)},
    )

    call = client.chat.completions.calls[0]
    request_event = json.loads(call["messages"][1]["content"])["events"][0]
    assert request_event["rebuild_reasons"] == ["protected_token_missing"]
    assert {"@xai", "Grok", "4.6"} <= set(request_event["protected_anchors"])
    assert "@handle" in call["messages"][0]["content"]
    assert "数字" in call["messages"][0]["content"]


def test_malformed_source_url_does_not_break_anchor_extraction():
    original = event(1)
    source = original.canonical_evidence
    malformed_url_event = MergedEvent(
        event_key=original.event_key,
        canonical_evidence=SourceEvidence(
            publisher_id=source.publisher_id,
            publisher_name=source.publisher_name,
            channel="x",
            authority=source.authority,
            is_official=source.is_official,
            official_identity_source=source.official_identity_source,
            source_title=source.source_title,
            evidence_text=source.evidence_text,
            url="https://[",
            published_at=source.published_at,
        ),
    )
    payload = {
        "items": [generated_item(1, malformed_url_event.event_key, "https://[")]
    }
    builder, client = builder_with_responses([payload])

    builder.build_batch([malformed_url_event], attempts={})

    request_event = json.loads(
        client.chat.completions.calls[0]["messages"][1]["content"]
    )["events"][0]
    assert not any(anchor.startswith("@") for anchor in request_event["protected_anchors"])


def test_builder_records_invalid_timeout_unavailable_and_circuit_diagnostics():
    invalid_builder, _ = builder_with_responses(["not-json"])
    invalid_builder.build_batch([event(1)], attempts={})
    assert invalid_builder.diagnostics["content_llm_invalid_response_count"] == 1

    timeout_builder, _ = builder_with_responses([TimeoutError("timeout")])
    timeout_builder.build_batch([event(1)], attempts={})
    assert timeout_builder.diagnostics["content_llm_timeout_count"] == 1

    unavailable_builder, _ = builder_with_responses([], api_key="")
    unavailable_builder.build_batch([event(1, chinese=True)], attempts={})
    assert unavailable_builder.diagnostics["content_llm_unavailable_count"] == 1

    circuit_builder, _ = builder_with_responses([RuntimeError("401 invalid api key")])
    circuit_builder.build_batch([event(1, chinese=True)], attempts={})
    circuit_builder.build_batch([event(2, chinese=True)], attempts={})
    assert circuit_builder.diagnostics["content_llm_unavailable_count"] == 1
    assert circuit_builder.diagnostics["content_llm_circuit_open_count"] == 1


def test_builder_accepts_valid_indexed_schema_and_preserves_event_mapping():
    events = [event(1), event(2)]
    payload = {
        "items": [
            generated_item(2, events[1].event_key, events[1].canonical_evidence.url),
            generated_item(1, events[0].event_key, events[0].canonical_evidence.url),
        ]
    }
    builder, _ = builder_with_responses([payload], builder_batch_size=2)

    results = builder.build_batch(events, attempts={})

    assert [result.event_key for result in results] == ["event-1", "event-2"]
    assert [result.draft.input_index for result in results] == [1, 2]
    assert all(result.generation_attempt == 1 for result in results)


def test_builder_maps_targets_to_complete_display_claims_and_keeps_multiple_quotes():
    item = event(1)
    payload = generated_item(1, item.event_key, item.canonical_evidence.url)
    payload["brief"] = "示例公司推出文本 API，并继续支持本地运行。第二句补充版本信息。"
    payload["evidence_targets"] = [
        {
            "target": "title",
            "source_quote_id": "q1",
        },
        {
            "target": "title",
            "source_quote_id": "q2",
        },
        {
            "target": "brief_1",
            "source_quote_id": "q2",
        },
        {
            "target": "brief_2",
            "source_quote_id": "q1",
        },
    ]
    builder, _ = builder_with_responses([{"items": [payload]}])

    result = builder.build_batch([item], attempts={})[0]

    assert result.draft is not None
    assert [binding.claim for binding in result.draft.evidence_bindings] == [
        "示例公司发布模型 1",
        "示例公司发布模型 1",
        "示例公司推出文本 API，并继续支持本地运行",
        "第二句补充版本信息",
    ]


def test_builder_rejects_unknown_missing_and_unexpected_targets():
    items = [event(index) for index in range(1, 4)]
    unknown = generated_item(1, items[0].event_key, items[0].canonical_evidence.url)
    unknown["evidence_targets"][0]["target"] = "summary"
    missing = generated_item(2, items[1].event_key, items[1].canonical_evidence.url)
    missing["brief"] = "第一句。第二句。"
    unexpected = generated_item(3, items[2].event_key, items[2].canonical_evidence.url)
    unexpected["evidence_targets"].append(
        {
            "target": "brief_2",
            "source_quote_id": "q1",
        }
    )
    builder, _ = builder_with_responses(
        [{"items": [unknown, missing, unexpected]}],
        builder_batch_size=3,
    )

    results = builder.build_batch(items, attempts={})

    assert [result.reason_code for result in results] == [
        "builder_item_malformed",
        None,
        "builder_item_malformed",
    ]
    assert results[1].draft.brief_mode == "title_only"
    assert results[1].draft.brief_reason == "brief_quote_unresolved"


def test_missing_duplicate_and_unindexed_results_fail_only_affected_items():
    events = [event(1), event(2), event(3)]
    item_one = generated_item(1, "event-1", events[0].canonical_evidence.url)
    item_two_a = generated_item(2, "event-2", events[1].canonical_evidence.url)
    item_two_b = generated_item(2, "event-2", events[1].canonical_evidence.url)
    unindexed = generated_item(3, "event-3", events[2].canonical_evidence.url)
    del unindexed["index"]
    builder, _ = builder_with_responses(
        [{"items": [item_one, item_two_a, item_two_b, unindexed]}],
        builder_batch_size=3,
    )

    results = builder.build_batch(events, attempts={})

    assert results[0].draft is not None
    assert results[1].draft is None
    assert results[1].reason_code == "builder_item_duplicate"
    assert results[2].draft is None
    assert results[2].reason_code == "builder_item_malformed"


def test_builder_rejects_wrong_types_and_event_key_mismatch():
    events = [event(1), event(2)]
    wrong_type = generated_item(1, "event-1", events[0].canonical_evidence.url)
    wrong_type["brief"] = ["not", "a", "string"]
    wrong_event = generated_item(2, "other-event", events[1].canonical_evidence.url)
    builder, _ = builder_with_responses(
        [{"items": [wrong_type, wrong_event]}],
        builder_batch_size=2,
    )

    results = builder.build_batch(events, attempts={})

    assert [result.reason_code for result in results] == [
        "builder_item_malformed",
        "builder_item_malformed",
    ]


def test_second_invalid_attempt_uses_complete_chinese_source_fallback():
    item = event(1, chinese=True)
    builder, _ = builder_with_responses(["not-json"])

    results = builder.build_batch([item], attempts={item.event_key: 1})

    result = results[0]
    assert result.generation_attempt == 2
    assert result.reason_code == "invalid_builder_response"
    assert result.source_fallback_used is True
    assert result.draft.content_origin == "source"
    assert result.draft.chinese_title == item.canonical_evidence.source_title
    assert result.draft.brief
    assert len(result.draft.evidence_bindings) >= 2
    assert all(
        binding.source_url == item.canonical_evidence.url
        for binding in result.draft.evidence_bindings
    )


def test_source_fallback_drops_non_chinese_details_and_keeps_title_only():
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

    assert result.draft.chinese_title == item.canonical_evidence.source_title
    assert result.draft.brief == ""
    assert result.draft.brief_mode == "title_only"


def test_source_fallback_preserves_sentence_boundaries_for_two_summary_sentences():
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
            evidence_text=f"{source.source_title}。第一句事实。第二句事实。",
            url=source.url,
            published_at=source.published_at,
        ),
    )

    result = builder_with_responses([], api_key="")[0].build_batch([item], attempts={})[0]

    assert result.draft.brief == "第一句事实。第二句事实。"


def test_source_fallback_binds_complete_display_sentences_without_comma_splitting():
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

    assert "该模型提供新的文本能力，并公开 API 说明" in bound_claims
    assert "该模型提供新的文本能力" not in bound_claims
    assert "并公开 API 说明" not in bound_claims


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
    assert second.reason_code == "invalid_builder_response"
    assert second.source_fallback_used is True
    assert second.draft is not None
    assert len(client.chat.completions.calls) == 2


def test_second_invalid_json_attempt_is_not_reported_as_translation_failure():
    item = event(1)
    builder, _ = builder_with_responses(["not-json"])

    result = builder.build_batch([item], attempts={item.event_key: 1})[0]

    assert result.draft is None
    assert result.reason_code == "invalid_builder_response"
    assert result.generation_attempt == 2


def test_second_valid_json_attempt_reports_missing_item_precisely():
    item = event(1)
    builder, _ = builder_with_responses([{"items": []}])

    result = builder.build_batch([item], attempts={item.event_key: 1})[0]

    assert result.draft is None
    assert result.reason_code == "builder_item_missing"


def test_second_valid_json_attempt_reports_malformed_item_precisely():
    item = event(1)
    malformed = generated_item(1, item.event_key, item.canonical_evidence.url)
    malformed.pop("evidence_targets")
    builder, _ = builder_with_responses([{"items": [malformed]}])

    result = builder.build_batch([item], attempts={item.event_key: 1})[0]

    assert result.draft is None
    assert result.reason_code == "builder_item_malformed"


def test_second_valid_json_attempt_reports_duplicate_item_precisely():
    item = event(1)
    generated = generated_item(1, item.event_key, item.canonical_evidence.url)
    builder, _ = builder_with_responses([{"items": [generated, generated]}])

    result = builder.build_batch([item], attempts={item.event_key: 1})[0]

    assert result.draft is None
    assert result.reason_code == "builder_item_duplicate"


def test_second_timeout_attempt_reports_transport_failure():
    item = event(1)
    builder, _ = builder_with_responses([TimeoutError("timeout")])

    result = builder.build_batch([item], attempts={item.event_key: 1})[0]

    assert result.draft is None
    assert result.reason_code == "content_llm_timeout"


def test_second_structured_english_attempt_reports_translation_failure():
    item = event(1)
    untranslated = generated_item(1, item.event_key, item.canonical_evidence.url)
    untranslated["chinese_title"] = "Example releases Model 1"
    untranslated["brief"] = "Example releases Model 1."
    builder, _ = builder_with_responses([{"items": [untranslated]}])

    result = builder.build_batch([item], attempts={item.event_key: 1})[0]

    assert result.draft is None
    assert result.reason_code == "translation_failed"


def test_single_rebuild_item_without_index_is_malformed_not_missing():
    item = event(1)
    malformed = generated_item(1, item.event_key, item.canonical_evidence.url)
    malformed.pop("index")
    builder, _ = builder_with_responses([{"items": [malformed]}])

    result = builder.build_batch([item], attempts={item.event_key: 1})[0]

    assert result.draft is None
    assert result.reason_code == "builder_item_malformed"


def test_single_rebuild_item_with_unhashable_event_key_is_malformed():
    item = event(1)
    malformed = generated_item(1, item.event_key, item.canonical_evidence.url)
    malformed.pop("index")
    malformed["event_key"] = [item.event_key]
    builder, _ = builder_with_responses([{"items": [malformed]}])

    result = builder.build_batch([item], attempts={item.event_key: 1})[0]

    assert result.draft is None
    assert result.reason_code == "builder_item_malformed"


def test_single_rebuild_item_normalizes_two_sentence_brief_list():
    item = event(1)
    generated = generated_item(1, item.event_key, item.canonical_evidence.url)
    generated["brief"] = ["示例公司发布模型 1。", "该模型提供新的文本能力。"]
    generated["evidence_targets"].append(
        {
            "target": "brief_2",
            "source_quote_id": "q1",
        }
    )
    builder, _ = builder_with_responses([{"items": [generated]}])

    result = builder.build_batch([item], attempts={item.event_key: 1})[0]

    assert result.reason_code is None
    assert result.draft is not None
    assert result.draft.brief == "示例公司发布模型 1。该模型提供新的文本能力。"


def test_single_rebuild_item_does_not_normalize_three_sentence_brief_list():
    item = event(1)
    generated = generated_item(1, item.event_key, item.canonical_evidence.url)
    generated["brief"] = ["第一句。", "第二句。", "第三句。"]
    builder, _ = builder_with_responses([{"items": [generated]}])

    result = builder.build_batch([item], attempts={item.event_key: 1})[0]

    assert result.draft is None
    assert result.reason_code == "builder_item_malformed"


def test_sdk_timeout_message_reports_transport_failure():
    class APITimeoutError(Exception):
        pass

    item = event(1)
    builder, _ = builder_with_responses([APITimeoutError("Request timed out.")])

    result = builder.build_batch([item], attempts={item.event_key: 1})[0]

    assert result.draft is None
    assert result.reason_code == "content_llm_timeout"
    assert result.circuit_open is False
    assert builder.diagnostics["content_llm_timeout_count"] == 1


def test_three_consecutive_timeouts_open_circuit_and_skip_next_batch():
    events = [event(index, chinese=True) for index in range(1, 5)]
    builder, client = builder_with_responses(
        [TimeoutError("Request timed out.") for _event in events[:3]]
    )

    results = builder.build_batch(events, attempts={})

    assert len(client.chat.completions.calls) == 3
    assert [result.circuit_open for result in results] == [False, False, True, True]
    assert [result.reason_code for result in results] == [
        "content_llm_timeout",
        "content_llm_timeout",
        "content_llm_timeout",
        "content_llm_unavailable",
    ]
    assert builder.diagnostics["content_llm_timeout_count"] == 3
    assert builder.diagnostics["content_llm_circuit_open_count"] == 1


def test_successful_decoded_response_resets_consecutive_timeout_count():
    events = [event(index, chinese=True) for index in range(1, 6)]
    successful_item = generated_item(
        1,
        events[2].event_key,
        events[2].canonical_evidence.url,
    )
    successful_item["chinese_title"] = "示例公司发布模型 3"
    successful_item["brief"] = "示例公司发布模型 3。"
    builder, client = builder_with_responses(
        [
            TimeoutError("Request timed out."),
            TimeoutError("Request timed out."),
            {"items": [successful_item]},
            TimeoutError("Request timed out."),
            TimeoutError("Request timed out."),
        ]
    )

    results = builder.build_batch(events, attempts={})

    assert len(client.chat.completions.calls) == 5
    assert all(result.circuit_open is False for result in results)
    assert results[2].reason_code is None
    assert [result.reason_code for result in results[:2] + results[3:]] == [
        "content_llm_timeout",
        "content_llm_timeout",
        "content_llm_timeout",
        "content_llm_timeout",
    ]
    assert builder.diagnostics["content_llm_timeout_count"] == 4
    assert builder.diagnostics["content_llm_success_count"] == 1


def test_payment_required_opens_circuit_and_skips_later_batches():
    class PaymentRequiredError(Exception):
        status_code = 402

    events = [event(index, chinese=True) for index in range(1, 7)]
    builder, client = builder_with_responses(
        [PaymentRequiredError("Insufficient Balance")]
    )

    results = builder.build_batch(events, attempts={})

    assert len(client.chat.completions.calls) == 1
    assert all(result.circuit_open is True for result in results)
    assert all(result.draft is not None for result in results)
    assert all(result.reason_code == "content_llm_unavailable" for result in results)


def test_provider_502_opens_circuit_and_skips_later_batches():
    class BadGatewayError(Exception):
        status_code = 502

    events = [event(index, chinese=True) for index in range(1, 7)]
    builder, client = builder_with_responses([BadGatewayError("502 Bad Gateway")])

    results = builder.build_batch(events, attempts={})

    assert len(client.chat.completions.calls) == 1
    assert all(result.circuit_open is True for result in results)
    assert all(result.reason_code == "content_llm_unavailable" for result in results)


def test_nonrecoverable_error_opens_circuit_and_avoids_later_calls():
    chinese = event(1, chinese=True)
    english = event(2)
    builder, client = builder_with_responses([RuntimeError("401 invalid api key")])

    first = builder.build_batch([chinese], attempts={})[0]
    second = builder.build_batch([english], attempts={})[0]

    assert first.draft.content_origin == "source"
    assert first.circuit_open is True
    assert first.reason_code == "content_llm_unavailable"
    assert first.source_fallback_used is True
    assert second.reason_code == "content_llm_unavailable"
    assert second.circuit_open is True
    assert len(client.chat.completions.calls) == 1


def test_missing_llm_configuration_uses_source_or_translation_failure_without_call():
    builder, client = builder_with_responses([], api_key="")

    chinese, english = builder.build_batch(
        [event(1, chinese=True), event(2)],
        attempts={},
    )

    assert chinese.draft.content_origin == "source"
    assert chinese.reason_code == "content_llm_unavailable"
    assert chinese.source_fallback_used is True
    assert english.draft is None
    assert english.reason_code == "content_llm_unavailable"
    assert client.chat.completions.calls == []
