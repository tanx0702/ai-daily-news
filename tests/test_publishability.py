from src.briefing import publishability
from src.briefing.models import SourceEvidence
from src.briefing.publishability import (
    claim_supported_by_quote,
    source_anchored_title,
    validate_display_publishability,
    validate_source_publishability,
    validate_update_display_publishability,
    validate_update_source_publishability,
)


def source(title, evidence_text=None, **overrides):
    values = {
        "publisher_id": "publisher",
        "publisher_name": "OpenAI",
        "channel": "rss",
        "authority": "professional_media",
        "is_official": False,
        "official_identity_source": "",
        "source_title": title,
        "evidence_text": evidence_text or title,
        "url": "https://example.test/story",
        "published_at": "2026-08-14T00:00:00+00:00",
    }
    values.update(overrides)
    return SourceEvidence(**values)


def test_complete_title_only_news_event_is_publishable():
    result = validate_display_publishability(
        "OpenAI 发布 GPT-5.6 Ultrafast",
        "",
        source("OpenAI releases GPT-5.6 Ultrafast"),
    )

    assert result.accepted is True
    assert result.title_completeness == "complete"
    assert result.event_type == "release"


def test_ai_update_accepts_concrete_result_without_release_action():
    evidence = source(
        "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark",
        "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark.",
        content_type="ai_update",
    )

    result = validate_update_source_publishability(evidence)

    assert result.accepted is True
    assert result.event_type == "ai_update"


def test_ai_update_accepts_bound_capability_demo_without_metric():
    evidence = source(
        "H3 Max generates high-quality video faster than it can be watched",
        "H3 Max generates high-quality video faster than it can be watched.",
        content_type="ai_update",
    )

    assert validate_update_source_publishability(evidence).accepted is True
    assert validate_update_display_publishability(
        "H3 Max 生成高质量视频的速度快于观看速度",
        "",
        evidence,
    ).accepted is True


def test_ai_update_rejects_swapped_capability_in_bound_quote():
    evidence = source(
        "H3 Max generates high-quality video",
        content_type="ai_update",
    )

    result = validate_update_display_publishability(
        "H3 Max 生成高质量音频",
        "",
        evidence,
    )

    assert result.reason_codes == ("update_claim_not_source_bound",)


def test_content_source_publishability_dispatches_by_content_type():
    dispatcher = getattr(
        publishability,
        "validate_content_source_publishability",
        None,
    )
    assert callable(dispatcher)

    fact = source(
        "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark",
        content_type="fact_event",
    )
    update = source(
        "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark",
        content_type="ai_update",
    )
    opinion = source(
        "I think open models will win because they are easier to adapt",
        content_type="attributed_opinion",
        channel="x",
        opinion_author="Andrej Karpathy",
        opinion_eligible=True,
        original_post=True,
        context_complete=True,
    )

    assert dispatcher(fact).accepted is False
    assert dispatcher(update).accepted is True
    assert dispatcher(opinion).event_type == "attributed_opinion"

    for field in (
        "opinion_author",
        "opinion_eligible",
        "original_post",
        "context_complete",
    ):
        invalid_value = "" if field == "opinion_author" else False
        opinion_values = {
            "opinion_author": opinion.opinion_author,
            "opinion_eligible": opinion.opinion_eligible,
            "original_post": opinion.original_post,
            "context_complete": opinion.context_complete,
        }
        opinion_values[field] = invalid_value
        invalid = source(
            opinion.source_title,
            content_type="attributed_opinion",
            channel="x",
            **opinion_values,
        )

        assert dispatcher(invalid).reason_codes == ("opinion_author_not_allowed",)


def test_ai_update_rejects_vague_or_promotional_content():
    cases = (
        source(
            "Interesting AI trend",
            "Interesting AI trend",
            content_type="ai_update",
        ),
        source(
            "Join our Qwen3.8 workshop for a 20% discount",
            "Join our Qwen3.8 workshop for a 20% discount",
            content_type="ai_update",
        ),
    )

    for evidence in cases:
        assert validate_update_source_publishability(evidence).reason_codes == (
            "update_missing_concrete_detail",
        )


def test_ai_update_display_requires_source_bound_subject_and_detail():
    evidence = source(
        "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark",
        "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark.",
        content_type="ai_update",
    )

    accepted = validate_update_display_publishability(
        "Qwen3.8-27B GGUF 在 Div-300 得分高出 10%",
        "",
        evidence,
    )
    invented = validate_update_display_publishability(
        "Qwen3.8-27B GGUF 在 Div-300 得分高出 20%",
        "",
        evidence,
    )
    inverted = validate_update_display_publishability(
        "Qwen3.8-27B GGUF 在 Div-300 得分低于 10%",
        "",
        evidence,
    )

    assert accepted.accepted is True
    assert invented.reason_codes == ("update_claim_not_source_bound",)
    assert inverted.reason_codes == ("update_claim_not_source_bound",)


def test_ai_update_subject_must_precede_relation_and_not_be_publisher_prefix():
    comparison_only = source(
        "Scores 10% higher than GPT-4 on MMLU benchmark",
        content_type="ai_update",
    )
    publisher_prefix = source(
        "VentureBeat: scores 10% higher on MMLU benchmark",
        content_type="ai_update",
        publisher_name="VentureBeat",
    )
    named_project = source(
        "ProjectNova scores 10% higher on MMLU benchmark",
        content_type="ai_update",
    )

    assert validate_update_source_publishability(comparison_only).reason_codes == (
        "update_missing_subject",
    )
    assert validate_update_source_publishability(publisher_prefix).reason_codes == (
        "update_missing_subject",
    )
    assert validate_update_source_publishability(named_project).accepted is True


def test_ai_update_requires_metric_or_named_detail_not_generic_technical_word():
    generic = source(
        "Qwen3.8 improves benchmark",
        content_type="ai_update",
    )
    named = source(
        "Qwen3.8 improves MMLU-Pro benchmark",
        content_type="ai_update",
    )

    assert validate_update_source_publishability(generic).reason_codes == (
        "update_missing_concrete_detail",
    )
    assert validate_update_source_publishability(named).accepted is True


def test_ai_update_unknown_metric_dimension_cannot_be_swapped():
    evidence = source(
        "Qwen3.8 improves accuracy by 10%",
        content_type="ai_update",
    )

    result = validate_update_display_publishability(
        "Qwen3.8 improves quality by 10%",
        "",
        evidence,
    )

    assert result.reason_codes == ("update_claim_not_source_bound",)


def test_ai_update_rejects_metric_direction_from_different_clauses():
    evidence = source(
        "Qwen3.8 improves accuracy by 10% but latency decreases by 20%",
        content_type="ai_update",
    )
    result = validate_update_display_publishability(
        "Qwen3.8 latency improves by 20%",
        "",
        evidence,
    )
    assert result.accepted is False


def test_ai_update_comparison_object_cannot_become_subject():
    evidence = source(
        "GPT-4 vs Qwen3.8-27B scores 10% higher on MMLU",
        content_type="ai_update",
    )
    result = validate_update_display_publishability(
        "GPT-4 scores 10% higher on MMLU",
        "",
        evidence,
    )
    assert result.accepted is False


def test_fact_publishability_does_not_adopt_ai_update_rules():
    evidence = source(
        "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark",
        content_type="fact_event",
    )

    assert validate_source_publishability(evidence).reason_codes == (
        "non_news_content",
    )


def test_vague_topic_titles_are_not_publishable():
    cases = (
        ("Mistral AI 战略", "Mistral AI strategy"),
        ("GPT-5.6 Sol Ultrafast 加速", "GPT-5.6 Sol Ultrafast"),
        ("Demis Hassabis 提及 3.7 Flash", "Demis Hassabis mentions 3.7 Flash"),
        ("公司发布模型", "A company releases a model"),
    )

    for title, source_title in cases:
        result = validate_display_publishability(title, "", source(source_title))
        assert result.accepted is False, title
        assert result.reason_codes, title


def test_hn_metrics_and_tutorial_titles_are_not_news_events():
    metrics = source(
        "AI text watermarking",
        "AI text watermarking\nPoints: 6\n# Comments: 2",
        discovered_via="hacker_news",
    )
    tutorial = source(
        "How AI text watermarking works",
        discovered_via="hacker_news",
        evidence_quality="title_only",
    )

    assert validate_source_publishability(metrics).reason_codes == (
        "metadata_only_evidence",
    )
    assert validate_source_publishability(tutorial).reason_codes == (
        "non_news_content",
    )


def test_source_publishability_recognizes_common_factual_news_verbs():
    titles = (
        "Nvidia dramatically reduces amount of OpenAI infra financing it may guarantee",
        "OpenAI reportedly disbanded its preparedness team",
        "Sainsbury's pauses AI cameras after shopper ousted",
        "ChatGPT's Computer History tracks your clicks and keystrokes",
        "Anthropic revenue jumps 14x to more than $11.5B in second quarter",
        "Qwen3.8-Flash API is live on QwenCloud",
        "OpenAI is releasing a technical report",
    )

    for title in titles:
        result = validate_source_publishability(source(title))

        assert result.accepted is True, title


def test_publishability_accepts_cross_language_titles_with_anchored_details():
    cases = (
        (
            "Nvidia dramatically reduces amount of OpenAI infra financing it may guarantee",
            "Nvidia 降低 OpenAI",
        ),
        (
            "OpenAI reportedly disbanded its preparedness team",
            "OpenAI 解散 preparedness team",
        ),
        (
            "Sainsbury's pauses AI cameras after shopper ousted",
            "Sainsbury's 暂停 cameras",
        ),
        (
            "ChatGPT's Computer History tracks your clicks and keystrokes",
            "ChatGPT's Computer History 追踪 clicks",
        ),
        (
            "ChatGPT's Computer History tracks your clicks and keystrokes",
            "ChatGPT 的 Computer History 跟踪 clicks and keystrokes",
        ),
        (
            "Anthropic revenue jumps 14x to more than $11.5B in second quarter",
            "Anthropic 增长 14x",
        ),
    )

    for source_title, display_title in cases:
        result = validate_display_publishability(
            display_title,
            "",
            source(source_title),
        )

        assert result.accepted is True, display_title


def test_publishability_accepts_reduces_and_chatgpt_with_anchored_titles():
    cases = (
        (
            "Nvidia dramatically reduces amount of OpenAI infra financing it may guarantee",
            "Nvidia 减少 OpenAI",
        ),
        (
            "ChatGPT’s Computer History tracks your clicks and keystrokes",
            "ChatGPT 的 Computer History 追踪 clicks and keystrokes",
        ),
    )

    for source_title, display_title in cases:
        result = validate_display_publishability(
            display_title,
            "",
            source(source_title),
        )

        assert result.accepted is True, display_title


def test_source_anchored_title_requires_subject_and_detail_anchor():
    supported = source(
        "Nvidia dramatically reduces amount of OpenAI infra financing it may guarantee"
    )
    title = source_anchored_title(supported)

    assert title == "Nvidia 减少 OpenAI"
    assert validate_display_publishability(title, "", supported).accepted is True
    assert source_anchored_title(source("Nvidia reduces it")) is None


def test_source_anchored_title_keeps_single_protected_product_token():
    cases = (
        ("Cursor launches Origin code hosting platform", "Cursor 发布 Origin"),
        ("OpenAI acquires Mac Minis, Mac Studios for AI training", "OpenAI 收购 Mac"),
    )

    for source_title, expected in cases:
        supported = source(source_title)
        title = source_anchored_title(supported)

        assert title == expected
        assert validate_display_publishability(title, "", supported).accepted is True


def test_source_anchored_title_keeps_explicit_ai_agent_and_duration():
    supported = source(
        "AI Accelerator Designed, Verified, and Deployed from Scratch in 2 Weeks by AI"
    )

    title = source_anchored_title(supported)

    assert title == "AI 在 2 周内完成部署"
    assert validate_display_publishability(title, "", supported).accepted is True


def test_source_anchored_title_keeps_developer_llm_use_and_departure():
    supported = source(
        "Debian developer resigns after corporate LLM use without disclosure wins vote"
    )

    title = source_anchored_title(supported)

    assert title == "Debian 开发者在 LLM 使用后辞职"
    assert validate_display_publishability(title, "", supported).accepted is True


def test_source_anchored_title_rejects_generic_english_detail_words():
    assert source_anchored_title(
        source("ChatGPT’s Computer History tracks your clicks and keystrokes")
    ) is None
    assert source_anchored_title(
        source("Sainsbury's pauses AI cameras after shopper ousted")
    ) is None
    assert source_anchored_title(
        source("Vim Classic launches its first AI-powered repository")
    ) is None


def test_source_anchored_title_rejects_generic_concept_acronym_as_product():
    # "AGI" here is a general concept inside "the AGI debate", not a released
    # product; the verb's object is the common noun "institute".
    assert source_anchored_title(
        source("Google DeepMind launches institute to widen the AGI debate")
    ) is None


def test_source_anchored_title_rejects_partnership_companion_as_object():
    # "Hugging Face" is introduced by "with" as a partner, not a released product.
    assert source_anchored_title(
        source(
            "Base Labs launches an open-weight AI safety partnership "
            "with Hugging Face and Goodfire"
        )
    ) is None


def test_source_anchored_title_uses_x_handle_as_detail_anchor():
    supported = source("Google AI: Upgrades coming to @FlowbyGoogle")

    title = source_anchored_title(supported)

    assert title == "Google 升级 @FlowbyGoogle"
    assert validate_display_publishability(title, "", supported).accepted is True


def test_source_anchored_title_recognizes_hugging_face_as_organization():
    acquired = source(
        "Report: Nvidia to acquire AI model repository Hugging Face for $13 billion"
    )
    report = source("OpenAI releases its official report on the Hugging Face breach")

    assert source_anchored_title(acquired) == "Nvidia 收购 Hugging Face"
    assert source_anchored_title(report) == "OpenAI 发布 Hugging Face"


def test_progressive_action_forms_are_recognized():
    """-ing forms were missing, so official X announcements using them were dropped.

    "We're partnering with Accenture" framed no action and was rejected as
    non_news_content; the progressive form must be an asserted action.
    """
    cases = {
        "Anthropic: We're partnering with Accenture on independent evaluation": "partnership",
        "OpenAI: We're collaborating with Broad Institute on genomics": "partnership",
        "OpenAI: We're rolling out new agent capabilities to enterprises": "release",
        "Meta: We're deploying new data center capacity in Texas": "infrastructure",
        "OpenAI: We're hiring for the safety team": "joining",
    }

    for title, expected in cases.items():
        actions = publishability.asserted_action_types(title)
        assert expected in actions, f"{title!r} -> {sorted(actions)}"


def test_progressive_forms_do_not_admit_x_noise():
    """Widening to -ing forms must not admit promotional or conversational posts."""
    for title in (
        "Cohere: All out for ALL-IN @AstonMartinF1 and @MagnusCarlsen were in",
        "Nathan Benaich: those nscale numbers",
        "Cohere: Well, he's not wrong @aidangomez",
    ):
        result = validate_source_publishability(source(title))
        assert result.accepted is False, f"{title!r} unexpectedly accepted"


def test_source_anchored_title_supports_live_api_on_qwencloud():
    supported = source(
        "Qwen: Qwen3.8-Flash API is live on QwenCloud. "
        "262K native context, extensible to 1M."
    )

    assert source_anchored_title(supported) == "Qwen3.8-Flash 上线 QwenCloud"


def test_bare_discovery_verb_is_not_a_security_action():
    """"发现" must only frame a security event when bound to a security object.

    Regression: listing bare 发现 in the security group made ordinary narration
    ("体验完 Step 5 Preview，我发现阶跃…") frame a security action, which broke
    duplicate detection: two reports of one release then had no shared action and
    stayed "distinct", each taking a briefing slot.
    """
    for narration in (
        "体验完 Step 5 Preview，我发现阶跃重新坐上国产大模型主桌",
        "我发现这个功能很好用",
    ):
        assert "security" not in publishability.asserted_action_types(narration), narration

    # The disclosure sense stays recognised when bound to a security object.
    for disclosure in (
        "研究员发现漏洞，影响百万设备",
        "某公司发现并修复了严重漏洞",
    ):
        assert "security" in publishability.asserted_action_types(disclosure), disclosure


def test_source_anchored_title_supports_concrete_ai_news_actions():
    cases = (
        (
            "Anthropic gets its first court win over the Pentagon's supply-chain risk label",
            "Anthropic 法院裁决 Pentagon",
        ),
        (
            "Meta executive leaves for OpenAI as the social media giant faces scrutiny",
            "Meta 离职 OpenAI",
        ),
        (
            "Qwen: Qwen3.8-Flash is now available in OpenCode Go 125B/6B",
            "Qwen3.8-Flash 可用 OpenCode Go",
        ),
        (
            "Anthropic releases Claude automated evaluator",
            "Anthropic 发布 Claude",
        ),
        (
            "Google DeepMind: We're rolling out Gemini Omni 1.1 Flash for video generation",
            "Google DeepMind 上线 Gemini Omni 1.1 Flash",
        ),
    )

    for source_title, expected in cases:
        assert source_anchored_title(source(source_title)) == expected


def test_model_anchor_handles_long_source_titles_without_backtracking():
    source_title = (
        "Google DeepMind: We're rolling out Gemini Omni 1.1 Flash "
        + "for production video generation. " * 200
    )

    assert source_anchored_title(source(source_title)) == (
        "Google DeepMind 上线 Gemini Omni 1.1 Flash"
    )


def test_display_publishability_accepts_available_translated_as_ke_yong():
    supported = source(
        "Qwen3.8-Flash is now available in OpenCode Go 125B/6B · 1M context · multimodal"
    )

    result = validate_display_publishability(
        "Qwen3.8-Flash 现已在 OpenCode Go 中可用",
        "",
        supported,
    )

    assert result.accepted is True


def test_claim_cannot_compose_subject_action_and_object_across_sentences():
    quote = "Mistral office research. OpenAI releases GPT-5.6."

    assert claim_supported_by_quote("Mistral 发布 GPT-5.6", quote) is False
    assert claim_supported_by_quote("OpenAI 发布 GPT-5.6", quote) is True


def test_action_and_model_must_be_supported_by_same_binding_quote():
    result = validate_display_publishability(
        "OpenAI 升级 GPT-5.7",
        "",
        source("OpenAI releases GPT-5.6"),
    )

    assert result.accepted is False
    assert result.reason_codes[0] in {
        "title_action_not_source_bound",
        "title_claim_not_source_bound",
    }


def test_ai_update_rejects_unregistered_metric_dimension_swap():
    source_evidence = source(
        "Qwen3.8 improves accuracy by 10%",
    )
    assert validate_update_display_publishability(
        "Qwen3.8 improves quality by 10%",
        "",
        source_evidence,
    ).accepted is False


# --- Task 1: explicit sue/lawsuit action recognition -------------------------

_SUE_TITLES = (
    "Seattle Times and Newsday are the latest publications to sue OpenAI and Microsoft",
    "Two more news organizations are suing OpenAI and Microsoft over the supposed use of their journalism to train AI.",
    "Seattle Times and Newsday sue OpenAI and Microsoft for infringement",
    "The Seattle Times and Newsday are just the latest plaintiffs to take OpenAI to court, alleging copyright infringement.",
)


def test_explicit_sue_action_is_recognized():
    for title in _SUE_TITLES:
        actions = publishability.asserted_action_types(title)
        assert actions, f"no action recognized for: {title}"
        assert "litigation" in actions, f"{sorted(actions)} for: {title}"


def test_sue_action_is_recognized_on_source_publishability():
    for title in _SUE_TITLES:
        result = validate_source_publishability(
            source(title, publisher_name="Seattle Times", publisher_id="seattletimes-com")
        )
        assert result.accepted is True, f"{result.reason_codes} for: {title}"


def test_planned_or_negated_sue_actions_are_not_asserted():
    for title in (
        "OpenAI may sue Microsoft over licensing",
        "The company is considering suing its former partner",
        "OpenAI did not sue Microsoft",
        "OpenAI would not sue Microsoft",
    ):
        assert "litigation" not in publishability.asserted_action_types(title), title


def test_sue_title_must_bind_action_and_subject_to_source_quote():
    result = validate_display_publishability(
        "OpenAI 起诉 Microsoft",
        "",
        source("OpenAI releases GPT-5.6", publisher_name="Seattle Times"),
    )

    assert result.accepted is False


# --- Task 3: source fallback must not fabricate a subject from prose ---------

_CONTAMINATED_X_TITLE = (
    "Nando de Freitas: Today we're releasing data on models accelerating research "
    "at OpenAI. Recursive self-improvement could be the most important contributor "
    "to AI capabilities over the next few years,"
)


def test_source_fallback_rejects_prose_subject_contamination():
    result = source_anchored_title(source(
        _CONTAMINATED_X_TITLE,
        publisher_name="Nando de Freitas",
        publisher_id="nandodefreitas",
        channel="x",
        authority="research",
    ))

    assert result is None, result


def test_source_fallback_never_emits_residual_english_prose():
    contaminated = (
        "Nando de Freitas: Today we're releasing data on models accelerating research at OpenAI.",
        "The team said they are shipping a new model at Meta today.",
        "In a thread about agents, the author describes releasing tools at Google.",
    )

    for title in contaminated:
        result = source_anchored_title(source(title, publisher_name="Nando de Freitas"))
        assert result is None, f"{title!r} -> {result!r}"
        if result:
            assert "Today" not in result
            assert "we're" not in result


def test_source_fallback_still_accepts_registered_anchors():
    cases = (
        "OpenAI releases GPT-5.6 Ultrafast",
        "Anthropic launches Claude 4.5 Opus",
        "NVIDIA releases CUDA 12.8",
    )

    for title in cases:
        result = source_anchored_title(source(title, publisher_name="OpenAI"))
        assert result, f"legitimate anchor lost for: {title}"
        assert "发布" in result or "上线" in result

# --- A: non_news_content must expose a discriminating private sub-reason ----


def test_non_news_rejection_distinguishes_instructional_from_no_action():
    """The two non_news_content exits must be distinguishable downstream."""
    instructional = source(
        "How AI text watermarking works",
        discovered_via="hacker_news",
        evidence_quality="title_only",
    )
    no_action = source("Mistral AI strategy")

    instructional_result = validate_source_publishability(instructional)
    no_action_result = validate_source_publishability(no_action)

    # Top-level contract must not change.
    assert instructional_result.reason_codes == ("non_news_content",)
    assert no_action_result.reason_codes == ("non_news_content",)
    # But the two must be tellable apart.
    assert instructional_result.rejection_detail == "instructional_content"
    assert no_action_result.rejection_detail == "no_asserted_action"
    assert instructional_result.rejection_detail != no_action_result.rejection_detail


def test_newly_asserted_news_actions_are_recognized():
    """Real news actions previously dropped as no_asserted_action."""
    cases = (
        ("Hackers Used Anthropic's Claude to Break into OpenAI", "security"),
        ("Microsoft, OpenAI lose fight to hide internal docs admitting scraping is theft", "policy"),
        ("Claude Code relaunches Projects to manage multiple AI agents in the cloud", "release"),
        ('Covert uploads and megalomania: OpenAI details new "misaligned" agent incidents', "security"),
        ("Anthropic says Claude now leads a quarter of work building its next AI models", "result"),
        ("Resy suspends VC for using AI agents to book reservations", "policy"),
        ("Docs in AI copyright suit reveal startling admission by Microsoft exec", "policy"),
    )

    for title, expected_action in cases:
        result = validate_source_publishability(source(title))

        assert result.accepted is True, f"{title!r} -> {result.reason_codes}"
        assert result.event_type == expected_action, title


def test_chinese_product_rewrite_is_an_asserted_release():
    result = validate_source_publishability(
        source("刚刚，Claude Code大重构！内部3万Agent管理技术免费开放")
    )

    assert result.accepted is True
    assert result.event_type == "release"


def test_partnership_and_opinion_titles_stay_rejected():
    """Widening the action vocabulary must not admit prose or non-events."""
    for title in (
        "PrismML hopes its tiny LLM will change how we all use AI",
        "Is the AI safety debate about safety or control?",
        "Helping older adults use AI in everyday life",
        "How to Write with an LLM",
        "AI safety is mostly a sex cult",
        "Your Agent Aced the Task. Will It Do It Again?",
        "Mistral AI strategy",
    ):
        result = validate_source_publishability(source(title))
        assert result.accepted is False, f"{title!r} unexpectedly accepted"


def test_literal_subject_drops_trailing_time_adverb():
    """A fallback subject must not keep a trailing adverb as a prose fragment."""
    result = validate_source_publishability(
        source("Artificial intelligence now beats some of the best human forecasters")
    )

    assert result.accepted is True
    assert all("now" not in anchor for anchor in result.subject_anchors)


def test_non_news_sub_reason_is_empty_for_accepted_and_other_rejections():
    accepted = validate_source_publishability(
        source("OpenAI releases GPT-5.6 Ultrafast")
    )
    assert accepted.accepted is True
    assert accepted.rejection_detail == ""

    # A non-``non_news_content`` rejection must not carry the sub-reason.
    other = validate_source_publishability(
        source(
            "AI text watermarking",
            "AI text watermarking\nPoints: 6\n# Comments: 2",
            discovered_via="hacker_news",
        )
    )
    assert other.accepted is False
    assert other.reason_codes == ("metadata_only_evidence",)
    assert other.rejection_detail == ""


def test_instructional_sub_reason_covers_every_non_news_pattern_class():
    """Each documented non-news pattern class maps to the instructional detail."""
    cases = (
        "How AI text watermarking works",
        "A guide to prompt engineering",
        "An AI agents tutorial",
        "OpenAI mentions GPT-5.6 Flash",
        "AI 工作原理",
        "模型使用指南",
        "部署教程",
        "AI 提及 GPT",
        "Mistral AI 战略",
        "AI 趋势",
    )

    for title in cases:
        result = validate_source_publishability(source(title))

        assert result.accepted is False, title
        assert result.reason_codes == ("non_news_content",), title
        assert result.rejection_detail == "instructional_content", title


def test_no_action_sub_reason_covers_topic_style_headlines():
    """Topic/announcement-style headlines have no asserted action."""
    cases = (
        "Anthropic merges Claude chat and Cowork in one interface",
        "Claude comes for Gemini with its own take on Docs and Slides",
        "Nvidia's Jensen Huang says AI regulation is unnecessary",
        "AI is the next problem for the internet",
    )

    for title in cases:
        result = validate_source_publishability(source(title))

        assert result.accepted is False, title
        assert result.rejection_detail == "no_asserted_action", title
