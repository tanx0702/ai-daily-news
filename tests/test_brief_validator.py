import json
from datetime import datetime, timezone
from pathlib import Path

from src.briefing.config import BriefingConfig
from src.briefing.models import BuiltBrief, EvidenceBinding, MergedEvent, SourceEvidence
from src.briefing.publishability import source_anchored_title
from src.briefing.validator import BriefValidator, _cross_language_anchors
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


def update_event():
    return event(
        publisher_id="qwen-researcher",
        publisher_name="Qwen Researcher",
        channel="x",
        authority="research",
        is_official=False,
        official_identity_source="",
        source_title="Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark",
        evidence_text=(
            "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark."
        ),
        url="https://x.com/qwen_researcher/status/42",
        content_type="ai_update",
    )


def bound_update_draft(item):
    title = "Qwen3.8-27B GGUF 在 Div-300 得分高出 10%"
    return draft(
        item,
        chinese_title=title,
        brief="",
        evidence_bindings=(
            EvidenceBinding(
                title,
                item.canonical_evidence.source_title,
                item.canonical_evidence.url,
            ),
        ),
        content_type="ai_update",
    )


def invented_update_draft(item):
    title = "Qwen3.8-27B GGUF 在 Div-300 得分高出 20%"
    return draft(
        item,
        chinese_title=title,
        brief="",
        evidence_bindings=(
            EvidenceBinding(
                title,
                item.canonical_evidence.source_title,
                item.canonical_evidence.url,
            ),
        ),
        content_type="ai_update",
    )


def test_validator_accepts_bound_ai_update_but_rejects_invented_result():
    item = update_event()

    accepted = validator().validate(
        item,
        bound_update_draft(item),
        generation_attempt=1,
        now=NOW,
    )
    rejected = validator().validate(
        item,
        invented_update_draft(item),
        generation_attempt=1,
        now=NOW,
    )

    assert accepted.action == "accept"
    assert accepted.validated_item.content_type == "ai_update"
    assert rejected.reason_codes in {
        ("claim_quote_mismatch",),
        ("update_claim_not_source_bound",),
    }


def test_validator_accepts_source_bound_non_numeric_ai_update():
    item = event(
        publisher_id="h3",
        publisher_name="H3",
        content_type="ai_update",
        source_title="H3 Max generates video",
        evidence_text="H3 Max generates video.",
    )
    title = "H3 Max 生成视频"
    generated = draft(
        item,
        chinese_title=title,
        brief="",
        evidence_bindings=(
            EvidenceBinding(
                title,
                "H3 Max generates video.",
                item.canonical_evidence.url,
            ),
        ),
        content_type="ai_update",
    )

    result = validator().validate(
        item,
        generated,
        generation_attempt=1,
        now=NOW,
    )

    assert result.action == "accept"


def test_validator_rejects_swapped_non_numeric_capability():
    item = event(
        publisher_id="h3",
        publisher_name="H3",
        content_type="ai_update",
        source_title="H3 Max generates video",
        evidence_text="H3 Max generates video.",
    )
    title = "H3 Max 生成音频"
    generated = draft(
        item,
        chinese_title=title,
        brief="",
        evidence_bindings=(
            EvidenceBinding(
                title,
                "H3 Max generates video.",
                item.canonical_evidence.url,
            ),
        ),
        content_type="ai_update",
    )

    result = validator().validate(
        item,
        generated,
        generation_attempt=1,
        now=NOW,
    )

    assert result.action == "rebuild"
    assert "update_claim_not_source_bound" in result.reason_codes


def test_validator_rejects_demo_rewritten_as_formal_release():
    item = event(
        publisher_id="h3",
        publisher_name="H3",
        content_type="ai_update",
        source_title="H3 Max demonstrates video generation",
        evidence_text="H3 Max demonstrates video generation.",
    )
    title = "H3 Max 正式发布视频模型"
    generated = draft(
        item,
        chinese_title=title,
        brief="",
        evidence_bindings=(
            EvidenceBinding(
                title,
                "H3 Max demonstrates video generation.",
                item.canonical_evidence.url,
            ),
        ),
        content_type="ai_update",
    )

    result = validator().validate(
        item,
        generated,
        generation_attempt=1,
        now=NOW,
    )

    assert result.action == "rebuild"


def test_validator_rejects_draft_content_type_override():
    item = event(
        publisher_id="anthropic",
        publisher_name="Anthropic",
        source_title="Anthropic revenue jumps 14x in second quarter",
        evidence_text="Anthropic revenue jumps 14x in second quarter.",
    )
    title = "Anthropic 得分增长 14x"
    generated = draft(
        item,
        chinese_title=title,
        brief="",
        evidence_bindings=(
            EvidenceBinding(
                title,
                item.canonical_evidence.source_title,
                item.canonical_evidence.url,
            ),
        ),
        content_type="ai_update",
    )

    result = validator().validate(item, generated, generation_attempt=1, now=NOW)

    assert result.action == "reject"
    assert result.reason_codes == ("invalid_builder_response",)


def test_ai_update_brief_cannot_turn_release_notes_into_formal_release():
    item = event(
        publisher_id="qwen",
        publisher_name="Qwen",
        source_title="Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark",
        evidence_text=(
            "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark. "
            "Qwen3.8-27B GGUF release notes are available."
        ),
        content_type="ai_update",
    )
    title = "Qwen3.8-27B GGUF 在 Div-300 得分高出 10%"
    invented_release = "Qwen3.8-27B GGUF 正式发布 release notes"
    generated = draft(
        item,
        chinese_title=title,
        brief=f"{invented_release}。",
        evidence_bindings=(
            EvidenceBinding(
                title,
                item.canonical_evidence.source_title,
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                invented_release,
                "Qwen3.8-27B GGUF release notes are available",
                item.canonical_evidence.url,
            ),
        ),
        content_type="ai_update",
    )

    result = validator().validate(item, generated, generation_attempt=1, now=NOW)

    assert result.action == "rebuild"
    assert result.reason_codes == ("update_claim_not_source_bound",)


def test_ai_update_claim_must_match_its_own_binding_quote():
    item = event(
        publisher_id="qwen",
        publisher_name="Qwen",
        source_title="Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark",
        evidence_text=(
            "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark. "
            "Qwen3.8-27B GGUF release notes are available."
        ),
        content_type="ai_update",
    )
    title = "Qwen3.8-27B GGUF 在 Div-300 得分高出 10%"
    brief_claim = "Qwen3.8-27B GGUF 的 Div-300 得分高出 10%"
    generated = draft(
        item,
        chinese_title=title,
        brief=f"{brief_claim}。",
        evidence_bindings=(
            EvidenceBinding(
                title,
                item.canonical_evidence.source_title,
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                brief_claim,
                "Qwen3.8-27B GGUF release notes are available",
                item.canonical_evidence.url,
            ),
        ),
        content_type="ai_update",
    )

    result = validator().validate(item, generated, generation_attempt=1, now=NOW)

    assert result.action == "rebuild"
    assert result.reason_codes == ("update_claim_not_source_bound",)


def test_ai_update_metric_and_direction_must_match_each_binding_quote():
    item = event(
        publisher_id="qwen",
        publisher_name="Qwen",
        source_title=(
            "Qwen3.8-27B has 10% lower latency on Div-300 benchmark"
        ),
        evidence_text=(
            "Qwen3.8-27B has 10% lower latency on Div-300 benchmark."
        ),
        content_type="ai_update",
    )
    title = "Qwen3.8-27B 在 Div-300 延迟降低 10%"
    invented_score = "Qwen3.8-27B 在 Div-300 得分低于 10%"
    valid_title_only = draft(
        item,
        chinese_title=title,
        brief="",
        evidence_bindings=(
            EvidenceBinding(
                title,
                item.canonical_evidence.source_title,
                item.canonical_evidence.url,
            ),
        ),
        content_type="ai_update",
    )
    generated = draft(
        item,
        chinese_title=title,
        brief=f"{invented_score}。",
        evidence_bindings=(
            EvidenceBinding(
                title,
                item.canonical_evidence.source_title,
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                invented_score,
                item.canonical_evidence.source_title,
                item.canonical_evidence.url,
            ),
        ),
        content_type="ai_update",
    )

    accepted = validator().validate(
        item,
        valid_title_only,
        generation_attempt=1,
        now=NOW,
    )
    result = validator().validate(item, generated, generation_attempt=1, now=NOW)

    assert accepted.action == "accept"
    assert result.action == "rebuild"
    assert result.reason_codes == ("update_claim_not_source_bound",)


def test_validator_accepts_quotes_after_html_entity_and_whitespace_normalization():
    item = event()

    result = validator().validate(item, draft(item), generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert result.validation_mode == "rules_only"
    assert result.validated_item.chinese_title == "OpenAI 发布 Model 5"
    assert result.validated_item.brief == "该模型提供文本 API。"
    assert result.validated_item.brief_mode == "expanded"
    assert result.validated_item.brief_reason == "brief_restates_title"


def test_validator_accepts_title_only_when_summary_is_empty():
    item = event(evidence_text="OpenAI 发布 Model 5")
    generated = draft(
        item,
        brief="",
        evidence_bindings=(
            EvidenceBinding(
                "OpenAI 发布 Model 5",
                "OpenAI 发布 Model 5",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, generated, generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert result.validated_item.brief == ""
    assert result.validated_item.brief_mode == "title_only"
    assert result.validated_item.brief_reason == "brief_empty"


def test_opinion_validator_requires_explicit_author_attribution():
    item = event(
        publisher_id="karpathy",
        publisher_name="Andrej Karpathy",
        channel="x",
        authority="research",
        is_official=False,
        official_identity_source="",
        source_title="I think open models will win",
        evidence_text="I think open models will win because they are easier to adapt.",
        url="https://x.com/karpathy/status/42",
        content_type="attributed_opinion",
        opinion_author="Andrej Karpathy",
        opinion_eligible=True,
        original_post=True,
        context_complete=True,
        stance_type="opinion",
    )
    generated = draft(
        item,
        chinese_title="开放模型将赢得竞争",
        brief="",
        evidence_bindings=(
            EvidenceBinding(
                "开放模型将赢得竞争",
                "I think open models will win",
                item.canonical_evidence.url,
            ),
        ),
        content_type="attributed_opinion",
        opinion_author="Andrej Karpathy",
    )

    result = validator().validate(item, generated, generation_attempt=1, now=NOW)

    assert result.action == "rebuild"
    assert result.reason_codes == ("opinion_attribution_missing",)


def test_opinion_source_requires_a_registered_author_before_display_checks():
    item = event(
        publisher_id="karpathy",
        publisher_name="Andrej Karpathy",
        channel="x",
        authority="research",
        is_official=False,
        official_identity_source="",
        source_title="I think open models will win",
        evidence_text="I think open models will win because they are easier to adapt.",
        url="https://x.com/karpathy/status/42",
        content_type="attributed_opinion",
        opinion_author="",
        opinion_eligible=True,
        original_post=True,
        context_complete=True,
        stance_type="opinion",
    )
    title = "Andrej Karpathy 称开放模型将赢得竞争"
    generated = draft(
        item,
        chinese_title=title,
        brief="",
        evidence_bindings=(
            EvidenceBinding(
                title,
                item.canonical_evidence.source_title,
                item.canonical_evidence.url,
            ),
        ),
        content_type="attributed_opinion",
        opinion_author="Andrej Karpathy",
    )

    result = validator().validate(item, generated, generation_attempt=1, now=NOW)

    assert result.action == "reject"
    assert result.reason_codes == ("opinion_author_not_allowed",)


def test_validator_accepts_deterministic_cross_language_source_fallback():
    item = event(
        publisher_id="reuters-com",
        publisher_name="Reuters",
        authority="professional_media",
        is_official=False,
        official_identity_source="",
        source_title=(
            "Nvidia dramatically reduces amount of OpenAI infra financing it may guarantee"
        ),
        evidence_text=(
            "Nvidia dramatically reduces amount of OpenAI infra financing it may guarantee"
        ),
    )
    title = source_anchored_title(item.canonical_evidence)
    generated = draft(
        item,
        chinese_title=title,
        brief="",
        evidence_bindings=(
            EvidenceBinding(title, item.canonical_evidence.source_title, item.canonical_evidence.url),
        ),
        content_origin="source",
    )

    result = validator().validate(item, generated, generation_attempt=2, now=NOW)

    assert result.action == "accept"
    assert result.validation_mode == "rules_only"
    assert result.validated_item.chinese_title == "Nvidia 减少 OpenAI"
    assert result.validated_item.brief_mode == "title_only"


def test_validator_rejects_source_fallback_with_generic_english_detail():
    item = event(
        publisher_id="theverge-com",
        publisher_name="The Verge",
        authority="professional_media",
        is_official=False,
        official_identity_source="",
        source_title="ChatGPT’s Computer History tracks your clicks and keystrokes",
        evidence_text="ChatGPT’s Computer History tracks your clicks and keystrokes",
    )
    title = "ChatGPT 追踪 clicks"
    generated = draft(
        item,
        chinese_title=title,
        brief="",
        evidence_bindings=(
            EvidenceBinding(title, item.canonical_evidence.source_title, item.canonical_evidence.url),
        ),
        content_origin="source",
    )

    result = validator().validate(item, generated, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert result.reason_codes == ("translation_failed",)


def test_validator_rejects_source_fallback_with_generic_english_object():
    item = event(
        publisher_id="bbc-co-uk",
        publisher_name="BBC",
        authority="professional_media",
        is_official=False,
        official_identity_source="",
        source_title="Sainsbury's pauses AI cameras after shopper ousted",
        evidence_text="Sainsbury's pauses AI cameras after shopper ousted",
    )
    title = "Sainsbury's 暂停 cameras"
    generated = draft(
        item,
        chinese_title=title,
        brief="",
        evidence_bindings=(
            EvidenceBinding(title, item.canonical_evidence.source_title, item.canonical_evidence.url),
        ),
        content_origin="source",
    )

    result = validator().validate(item, generated, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert result.reason_codes == ("translation_failed",)


def test_validator_rebuilds_vague_title_once_then_rejects():
    item = event(
        source_title="Mistral 发布 1GW AI 数据中心计划",
        evidence_text="Mistral 发布 1GW AI 数据中心计划。",
        publisher_id="mistral",
        publisher_name="Mistral AI",
    )
    vague = draft(
        item,
        chinese_title="Mistral AI 战略",
        brief="",
        evidence_bindings=(EvidenceBinding(
            "Mistral AI 战略",
            "Mistral 发布 1GW AI 数据中心计划",
            item.canonical_evidence.url,
        ),),
    )

    first = validator().validate(item, vague, generation_attempt=1, now=NOW)
    second = validator().validate(item, vague, generation_attempt=2, now=NOW)

    assert first.action == "rebuild"
    assert first.reason_codes == ("title_missing_event_action",)
    assert second.action == "reject"
    assert second.reason_codes == first.reason_codes


def test_validator_permanently_rejects_non_news_source_before_rebuilding_title():
    item = event(
        source_title="AI text watermarking guide",
        evidence_text="How AI text watermarking works.",
    )
    fabricated = draft(
        item,
        chinese_title="OpenAI 发布 GPT-5.6",
        brief="",
        evidence_bindings=(EvidenceBinding(
            "OpenAI 发布 GPT-5.6",
            "How AI text watermarking works",
            item.canonical_evidence.url,
        ),),
    )

    result = validator().validate(item, fabricated, generation_attempt=1, now=NOW)

    assert result.action == "reject"
    assert result.reason_codes == ("non_news_content",)


def test_validator_rejects_claim_composed_from_separate_source_sentences():
    item = event(
        source_title="Mistral releases Aster",
        evidence_text="Mistral office research. OpenAI releases GPT-5.6.",
        publisher_id="media",
        publisher_name="Example Media",
        authority="professional_media",
        is_official=False,
        official_identity_source="",
    )
    composed = draft(
        item,
        chinese_title="Mistral 发布 GPT-5.6",
        brief="",
        evidence_bindings=(EvidenceBinding(
            "Mistral 发布 GPT-5.6",
            "Mistral office research. OpenAI releases GPT-5.6.",
            item.canonical_evidence.url,
        ),),
    )

    result = validator().validate(item, composed, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert result.reason_codes == ("title_claim_not_source_bound",)


def test_validator_removes_all_title_restatement_sentences_without_rebuild():
    item = event(evidence_text="OpenAI 发布 Model 5。")
    generated = draft(
        item,
        brief="OpenAI 发布 Model 5。",
        evidence_bindings=(
            EvidenceBinding(
                "OpenAI 发布 Model 5",
                "OpenAI 发布 Model 5",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "OpenAI 发布 Model 5",
                "Model 5",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, generated, generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert result.validated_item.brief == ""
    assert result.validated_item.brief_mode == "title_only"
    assert result.validated_item.brief_reason == "brief_restates_title"


def test_validator_removes_title_restatement_even_when_quote_includes_body_text():
    item = event()
    generated = draft(
        item,
        brief="OpenAI 发布 Model 5。",
        evidence_bindings=(
            EvidenceBinding(
                "OpenAI 发布 Model 5",
                "OpenAI 发布 Model 5。该模型提供文本 API。",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, generated, generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert result.validated_item.brief == ""
    assert result.validated_item.brief_mode == "title_only"
    assert result.validated_item.brief_reason == "brief_restates_title"


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
            EvidenceBinding(
                "该模型提供文本 API",
                "该模型提供文本 API",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, unsupported, generation_attempt=1, now=NOW)

    assert result.action == "rebuild"
    assert result.reason_codes == ("quote_not_found",)
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
    assert result.reason_codes == ("action_not_supported",)


def test_validator_accepts_chinese_claim_bound_to_english_quote_with_matching_action():
    item = event(
        source_title="Acme AI launches Model-X",
        evidence_text="Acme AI launches Model-X in 2026.",
    )
    translated = draft(
        item,
        chinese_title="Acme AI \u53d1\u5e03 Model-X",
        brief="Model-X \u4e8e 2026 \u5e74\u53d1\u5e03\u3002",
        evidence_bindings=(
            EvidenceBinding(
                "Acme AI \u53d1\u5e03 Model-X",
                "Acme AI launches Model-X",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "Model-X \u4e8e 2026 \u5e74\u53d1\u5e03",
                "Acme AI launches Model-X in 2026.",
                item.canonical_evidence.url,
            ),
        ),
    )

    instance, client = quality_validator({"items": [review_item()]})

    result = instance.validate(item, translated, generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert result.validation_mode == "rules_and_llm"
    assert len(client.calls) == 1


def test_validator_does_not_degrade_unanchored_cross_language_claim_after_quality_timeout():
    item = event(
        source_title="Company launches a model",
        evidence_text="Company launches a model in 2026.",
    )
    translated = draft(
        item,
        chinese_title="公司发布模型",
        brief="该模型于 2026 年发布。",
        evidence_bindings=(
            EvidenceBinding(
                "公司发布模型",
                "Company launches a model",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "该模型于 2026 年发布",
                "Company launches a model in 2026.",
                item.canonical_evidence.url,
            ),
        ),
    )
    instance, _ = quality_validator(TimeoutError("quality timeout"))

    result = instance.validate(item, translated, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert result.validation_mode == "rules_only"
    assert result.reason_codes == ("source_missing_subject",)


def test_validator_does_not_degrade_same_entity_with_different_translated_purpose():
    item = event(
        source_title="Model-X released for weather forecasting",
        evidence_text="Model-X was released for weather forecasting.",
    )
    mistranslated = draft(
        item,
        chinese_title="Model-X 已发布",
        brief="Model-X 用于治疗癌症。",
        evidence_bindings=(
            EvidenceBinding(
                "Model-X 已发布",
                "Model-X was released",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "Model-X 用于治疗癌症",
                "Model-X was released for weather forecasting.",
                item.canonical_evidence.url,
            ),
        ),
    )
    instance, _ = quality_validator(TimeoutError("quality timeout"))

    result = instance.validate(item, mistranslated, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert result.validation_mode == "rules_only"
    assert result.reason_codes == ("title_missing_event_detail",)


def test_validator_does_not_degrade_claim_with_fabricated_latin_entity():
    item = event(
        source_title="Acme launches Model-X",
        evidence_text="Acme launches Model-X in 2026.",
    )
    fabricated = draft(
        item,
        chinese_title="FakeCo 发布 Model-X",
        brief="Model-X 于 2026 年发布。",
        evidence_bindings=(
            EvidenceBinding(
                "FakeCo 发布 Model-X",
                "Acme launches Model-X",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "Model-X 于 2026 年发布",
                "Acme launches Model-X in 2026.",
                item.canonical_evidence.url,
            ),
        ),
    )

    instance, client = quality_validator({"items": [review_item()]})

    result = instance.validate(item, fabricated, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert result.reason_codes == ("title_claim_not_source_bound",)
    assert client.calls == []


def test_validator_rejects_cross_language_binding_with_unmatched_action_quote():
    item = event(
        source_title="Acme launches Model-5",
        evidence_text="Acme launches Model-5 in 2026. The weather is sunny.",
    )
    unrelated = draft(
        item,
        chinese_title="Acme \u53d1\u5e03 Model-5",
        brief="Model-5 \u4e8e 2026 \u5e74\u53d1\u5e03\u3002",
        evidence_bindings=(
            EvidenceBinding(
                "Acme \u53d1\u5e03 Model-5",
                "The weather is sunny.",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "Model-5 \u4e8e 2026 \u5e74\u53d1\u5e03",
                "Acme launches Model-5 in 2026.",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, unrelated, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert result.reason_codes == ("action_not_supported",)


def test_validator_rebuilds_cross_language_claim_with_only_action_and_year_when_quality_is_unavailable():
    item = event(
        source_title="Platform launches a hiking service",
        evidence_text="Platform launches a hiking service in 2026.",
    )
    unsupported = draft(
        item,
        chinese_title="\u516c\u53f8\u53d1\u5e03\u6a21\u578b",
        brief="\u8be5\u6a21\u578b\u4e8e 2026 \u5e74\u53d1\u5e03\u3002",
        evidence_bindings=(
            EvidenceBinding(
                "\u516c\u53f8\u53d1\u5e03\u6a21\u578b",
                "Platform launches a hiking service",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "\u8be5\u6a21\u578b\u4e8e 2026 \u5e74\u53d1\u5e03",
                "Platform launches a hiking service in 2026.",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, unsupported, generation_attempt=1, now=NOW)

    assert result.action == "rebuild"
    assert result.validation_mode == "rules_only"
    assert result.reason_codes == ("title_missing_subject",)


def test_validator_accepts_anchored_cross_language_claim_when_quality_is_unavailable():
    item = event(
        source_title="Acme launches Model-X",
        evidence_text="Acme launches Model-X in 2026.",
    )
    translated = draft(
        item,
        chinese_title="Acme 发布 Model-X",
        brief="Model-X 于 2026 年发布。",
        evidence_bindings=(
            EvidenceBinding(
                "Acme 发布 Model-X",
                "Acme launches Model-X",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "Model-X 于 2026 年发布",
                "Acme launches Model-X in 2026.",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, translated, generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert result.validation_mode == "rules_only"
    assert result.reason_codes == ("quality_llm_unavailable", "rules_only_used")


def test_cross_language_anchors_ignore_terminal_punctuation():
    claim = _cross_language_anchors("MiniMax H3 将在 Magnific SF office 举办活动")
    quote = _cross_language_anchors("MiniMax H3 at the Magnific SF office.")

    assert claim <= quote
    assert "office." not in quote


def test_validator_accepts_cross_language_claim_when_quality_review_times_out():
    item = event(
        source_title="Acme launches Model-X",
        evidence_text="Acme launches Model-X in 2026.",
    )
    translated = draft(
        item,
        chinese_title="Acme 发布 Model-X",
        brief="Model-X 于 2026 年发布。",
        evidence_bindings=(
            EvidenceBinding(
                "Acme 发布 Model-X",
                "Acme launches Model-X",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "Model-X 于 2026 年发布",
                "Acme launches Model-X in 2026.",
                item.canonical_evidence.url,
            ),
        ),
    )
    instance, _ = quality_validator(TimeoutError("quality timeout"))

    result = instance.validate(item, translated, generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert result.validation_mode == "rules_only"
    assert result.reason_codes == ("quality_llm_unavailable", "rules_only_used")


def test_validator_accepts_cross_language_claim_after_rebuild_when_quality_review_is_unavailable():
    item = event(
        source_title="Acme launches Model-X",
        evidence_text="Acme launches Model-X in 2026.",
    )
    translated = draft(
        item,
        chinese_title="Acme 发布 Model-X",
        brief="Model-X 于 2026 年发布。",
        evidence_bindings=(
            EvidenceBinding(
                "Acme 发布 Model-X",
                "Acme launches Model-X",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "Model-X 于 2026 年发布",
                "Acme launches Model-X in 2026.",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, translated, generation_attempt=2, now=NOW)

    assert result.action == "accept"
    assert result.validation_mode == "rules_only"
    assert result.reason_codes == ("quality_llm_unavailable", "rules_only_used")


def test_validator_accepts_cross_language_claim_when_quality_circuit_is_open():
    instance, client = quality_validator(TimeoutError("quality timeout"))
    item = event(
        source_title="Acme launches Model-X",
        evidence_text="Acme launches Model-X in 2026.",
    )
    translated = draft(
        item,
        chinese_title="Acme 发布 Model-X",
        brief="Model-X 于 2026 年发布。",
        evidence_bindings=(
            EvidenceBinding(
                "Acme 发布 Model-X",
                "Acme launches Model-X",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "Model-X 于 2026 年发布",
                "Acme launches Model-X in 2026.",
                item.canonical_evidence.url,
            ),
        ),
    )

    instance.validate(event(), draft(), generation_attempt=1, now=NOW)
    result = instance.validate(item, translated, generation_attempt=2, now=NOW)

    assert result.action == "accept"
    assert result.validation_mode == "rules_only"
    assert result.reason_codes == ("quality_llm_unavailable", "rules_only_used")
    assert len(client.calls) == 1


def test_validator_accepts_cross_language_claim_when_quality_response_is_invalid():
    item = event(
        source_title="Acme launches Model-X",
        evidence_text="Acme launches Model-X in 2026.",
    )
    translated = draft(
        item,
        chinese_title="Acme 发布 Model-X",
        brief="Model-X 于 2026 年发布。",
        evidence_bindings=(
            EvidenceBinding(
                "Acme 发布 Model-X",
                "Acme launches Model-X",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "Model-X 于 2026 年发布",
                "Acme launches Model-X in 2026.",
                item.canonical_evidence.url,
            ),
        ),
    )
    first_validator, _ = quality_validator({"items": []})
    second_validator, _ = quality_validator({"items": []})

    first = first_validator.validate(item, translated, generation_attempt=1, now=NOW)
    second = second_validator.validate(item, translated, generation_attempt=2, now=NOW)

    assert first.action == second.action == "accept"
    assert first.validation_mode == second.validation_mode == "rules_only"
    assert first.reason_codes == second.reason_codes == (
        "quality_llm_invalid_response",
        "rules_only_used",
    )


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
    assert result.reason_codes == ("missing_target_binding",)


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


def test_validator_rebuilds_titles_with_untranslated_english_prose():
    titles = (
        "Cognition CEO denies report that SpaceX tried to 收购 startup",
        "Scaling Law 不仅是 parameters 的 Scaling",
        "NVIDIA cuOpt 是 Hans Mittelmann benchmarks 上三个 optimization "
        "problem classes 中最快的 open source solver",
        'Claude Code 新增 "concise" output style setting',
        "Cursor capitalizes on GitHub frustration 发布 rival",
    )
    source_title = "Example releases an AI update"

    for title in titles:
        item = event(
            publisher_id="example-media",
            publisher_name="Example Media",
            authority="professional_media",
            is_official=False,
            official_identity_source="",
            source_title=source_title,
            evidence_text=source_title,
        )
        generated = draft(
            item,
            chinese_title=title,
            brief="",
            evidence_bindings=(
                EvidenceBinding(title, source_title, item.canonical_evidence.url),
            ),
        )

        result = validator().validate(item, generated, generation_attempt=1, now=NOW)

        assert result.action == "rebuild", title
        assert result.reason_codes == ("translation_failed",), title


def test_validator_allows_product_names_models_and_units_in_chinese_title():
    title = "PyTorch 内置 GELU 将 LLM 训练速度提升至 25,000 tokens/second"
    item = event(
        source_title=title,
        evidence_text=title,
    )
    generated = draft(
        item,
        chinese_title=title,
        brief="",
        evidence_bindings=(
            EvidenceBinding(
                title,
                item.canonical_evidence.source_title,
                item.canonical_evidence.url,
            ),
        ),
    )

    instance, _ = quality_validator({"items": [review_item()]})
    result = instance.validate(item, generated, generation_attempt=1, now=NOW)

    assert result.action == "accept", result.reason_codes


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
    assert result.reason_codes == ("quote_not_found",)


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
    assert result.reason_codes == ("title_claim_not_source_bound",)


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
    assert result.reason_codes == ("title_action_not_source_bound",)


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
        source_title="OpenAI 发布 Model 5",
        evidence_text="某研究者分享：OpenAI 发布 Model 5。",
        url="https://x.com/researcher/status/42",
    )
    attributed = draft(
        item,
        chinese_title="OpenAI 发布 Model 5",
        brief="该研究者称 OpenAI 发布 Model 5。",
        evidence_bindings=(
            EvidenceBinding(
                "OpenAI 发布 Model 5",
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


def test_validator_rejects_explicit_editorial_commentary_structure():
    item = event()
    commentary = draft(
        item,
        chinese_title="值得关注的是，OpenAI 发布 Model 5",
        brief="这意味着行业将发生变化。",
        evidence_bindings=(
            EvidenceBinding(
                "值得关注的是，OpenAI 发布 Model 5",
                "OpenAI 发布 Model 5",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "这意味着行业将发生变化",
                "OpenAI 发布 Model 5",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, commentary, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert result.reason_codes == ("unsupported_commentary",)


def test_validator_accepts_deidentified_2026_08_11_false_positive_cases():
    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "briefing"
        / "2026-08-11-false-positive-cases.json"
    )
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))

    for case in cases:
        item = event(**case["source"])
        generated = draft(
            item,
            chinese_title=case["chinese_title"],
            brief=case["brief"],
            evidence_bindings=tuple(
                EvidenceBinding(
                    binding["claim"],
                    binding["source_quote"],
                    item.canonical_evidence.url,
                )
                for binding in case["evidence_bindings"]
            ),
        )
        instance, _ = quality_validator({"items": [review_item()]})

        result = instance.validate(
            item,
            generated,
            generation_attempt=1,
            now=datetime(2026, 8, 11, 12, tzinfo=timezone.utc),
        )

        assert result.action == "accept", case["name"]


def test_validator_rejects_generic_subject_even_when_introduction_is_factual():
    item = event(
        source_title="机构介绍领先模型",
        evidence_text="机构介绍领先模型。帖子包含一条建议配置。",
    )
    factual = draft(
        item,
        chinese_title="机构介绍领先模型",
        brief="帖子包含一条建议配置。",
        evidence_bindings=(
            EvidenceBinding(
                "机构介绍领先模型",
                "机构介绍领先模型",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "帖子包含一条建议配置",
                "帖子包含一条建议配置",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, factual, generation_attempt=1, now=NOW)

    assert result.action == "reject"
    assert result.reason_codes == ("source_missing_subject",)


def test_validator_rejects_unrelated_cross_language_claim_when_quality_is_unavailable():
    item = event(
        source_title="The weather is sunny",
        evidence_text="The weather is sunny.",
    )
    unrelated = draft(
        item,
        chinese_title="公司与模型合作",
        brief="该产品用于治疗癌症。",
        evidence_bindings=(
            EvidenceBinding(
                "公司与模型合作",
                "The weather is sunny.",
                item.canonical_evidence.url,
            ),
            EvidenceBinding(
                "该产品用于治疗癌症",
                "The weather is sunny.",
                item.canonical_evidence.url,
            ),
        ),
    )

    result = validator().validate(item, unrelated, generation_attempt=2, now=NOW)

    assert result.action == "reject"
    assert result.reason_codes == ("non_news_content",)


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


def quality_validator(response, *, model="quality-model"):
    client = FakeClient(response)
    instance = validator(
        quality_config=LLMConfig(
            api_key="quality-key",
            model=model,
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


def test_quality_validator_disables_sdk_retries():
    captured = {}
    client = FakeClient({"items": [review_item()]})
    instance = validator(
        quality_config=LLMConfig(
            api_key="quality-key",
            model="quality-model",
            base_url="https://quality.example/v1",
        ),
        client_factory=lambda **kwargs: (captured.update(kwargs) or client),
    )

    result = instance.validate(event(), draft(), generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert captured["max_retries"] == 0


def test_valid_quality_review_marks_rules_and_llm_without_changing_content():
    instance, _ = quality_validator({"items": [review_item()]})
    original = draft()

    result = instance.validate(event(), original, generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert result.validation_mode == "rules_and_llm"
    assert result.validated_item.chinese_title == original.chinese_title
    assert result.validated_item.brief == "该模型提供文本 API。"


def test_quality_validator_uses_low_reasoning_effort_for_glm_5_3_flash():
    instance, client = quality_validator(
        {"items": [review_item()]},
        model="glm-5.3-flash",
    )

    instance.validate(event(), draft(), generation_attempt=1, now=NOW)

    assert client.calls[0]["extra_body"] == {"reasoning_effort": "low"}


def test_quality_review_request_explicitly_mentions_json_for_json_object_mode():
    instance, client = quality_validator({"items": [review_item()]})

    result = instance.validate(event(), draft(), generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert client.calls[0]["response_format"] == {"type": "json_object"}
    assert "json" in client.calls[0]["messages"][0]["content"].lower()


def test_quality_issue_rebuilds_once_then_rejects():
    response = {"items": [review_item("rebuild", ["unsupported_claim"])]}
    first_validator, _ = quality_validator(response)
    second_validator, _ = quality_validator(response)

    first = first_validator.validate(event(), draft(), generation_attempt=1, now=NOW)
    second = second_validator.validate(event(), draft(), generation_attempt=2, now=NOW)

    assert first.action == "rebuild"
    assert first.rebuild_request.generation_attempt == 2
    assert second.action == "reject"
    assert first.reason_codes == second.reason_codes == ("semantic_review_rejected",)


def test_quality_timeout_degrades_to_rules_only_acceptance():
    instance, _ = quality_validator(TimeoutError("quality timeout"))

    result = instance.validate(event(), draft(), generation_attempt=1, now=NOW)

    assert result.action == "accept"
    assert result.validation_mode == "rules_only"
    assert "quality_llm_unavailable" in result.reason_codes
    assert "rules_only_used" in result.reason_codes
    assert instance.diagnostics["quality_llm_timeout_count"] == 1


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
    assert instance.diagnostics["quality_llm_invalid_response_count"] == 1


def test_quality_validator_records_success_unavailable_and_circuit_diagnostics():
    reviewed, _ = quality_validator({"items": [review_item()]})
    reviewed.validate(event(), draft(), generation_attempt=1, now=NOW)
    assert reviewed.diagnostics["quality_llm_success_count"] == 1

    unavailable = validator()
    unavailable.validate(event(), draft(), generation_attempt=1, now=NOW)
    assert unavailable.diagnostics["quality_llm_unavailable_count"] == 1

    circuit, _ = quality_validator(TimeoutError("quality timeout"))
    circuit.validate(event(), draft(), generation_attempt=1, now=NOW)
    circuit.validate(event(), draft(), generation_attempt=1, now=NOW)
    assert circuit.diagnostics["quality_llm_timeout_count"] == 1
    assert circuit.diagnostics["quality_llm_circuit_open_count"] == 1


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
