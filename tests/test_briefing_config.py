import pytest

from src.briefing.config import BriefingConfig, InvalidBriefingConfiguration


def test_briefing_config_uses_approved_defaults():
    config = BriefingConfig.from_env({})

    assert config.min_items == 5
    assert config.max_items == 15
    assert config.candidate_pool_size == 45
    assert config.min_fact_items == 3
    assert config.max_opinion_items == 3
    assert config.max_x_items == 8
    assert config.target_x_items == 5
    assert config.x_feed_max_age_hours == 6
    assert config.builder_batch_size == 5
    assert config.news_hours == 36
    assert config.semantic_dedup_window_hours == 48
    assert config.semantic_dedup_max_llm_calls == 20
    assert config.semantic_dedup_timeout == 45
    assert config.skip_wechat_draft is False


def test_briefing_config_accepts_all_hard_boundaries():
    lower = BriefingConfig.from_env(
        {
            "DAILY_MIN_ITEMS": "5",
            "DAILY_TOP_N": "5",
            "DAILY_CANDIDATE_POOL_N": "5",
            "DAILY_X_MAX_ITEMS": "0",
            "X_FEED_MAX_AGE_HOURS": "1",
            "SKIP_WECHAT_DRAFT": "yes",
        }
    )
    upper = BriefingConfig.from_env(
        {
            "DAILY_MIN_ITEMS": "15",
            "DAILY_TOP_N": "15",
            "DAILY_CANDIDATE_POOL_N": "45",
            "DAILY_X_MAX_ITEMS": "8",
            "X_FEED_MAX_AGE_HOURS": "6",
            "SKIP_WECHAT_DRAFT": "0",
        }
    )

    assert (lower.min_items, lower.max_items, lower.max_x_items) == (5, 5, 0)
    assert lower.target_x_items == 0
    assert lower.skip_wechat_draft is True
    assert (upper.min_items, upper.max_items, upper.max_x_items) == (15, 15, 8)
    assert upper.target_x_items == 5
    assert upper.skip_wechat_draft is False


@pytest.mark.parametrize(
    "env",
    [
        {"DAILY_MIN_ITEMS": "4"},
        {"DAILY_MIN_ITEMS": "16"},
        {"DAILY_MIN_ITEMS": "10", "DAILY_TOP_N": "9"},
        {"DAILY_TOP_N": "16"},
        {"DAILY_CANDIDATE_POOL_N": "14", "DAILY_TOP_N": "15"},
        {"DAILY_X_MAX_ITEMS": "-1"},
        {"DAILY_X_MAX_ITEMS": "9"},
        {"DAILY_X_TARGET_ITEMS": "-1"},
        {"DAILY_X_MAX_ITEMS": "2", "DAILY_X_TARGET_ITEMS": "3"},
        {"DAILY_MIN_FACT_ITEMS": "2"},
        {"DAILY_MIN_FACT_ITEMS": "6", "DAILY_MIN_ITEMS": "5"},
        {"DAILY_MAX_OPINION_ITEMS": "-1"},
        {"DAILY_MAX_OPINION_ITEMS": "4"},
        {"X_FEED_MAX_AGE_HOURS": "0"},
        {"X_FEED_MAX_AGE_HOURS": "7"},
        {"DAILY_NEWS_HOURS": "0"},
        {"DAILY_MAX_ITEMS_PER_SOURCE": "-1"},
        {"DAILY_MAX_ITEMS_PER_TOPIC": "-1"},
        {"DAILY_MIN_PRIMARY_OR_RESEARCH": "-1"},
        {"DAILY_TOP_N": "fifteen"},
        {"SKIP_WECHAT_DRAFT": "sometimes"},
    ],
)
def test_briefing_config_rejects_invalid_values(env):
    with pytest.raises(InvalidBriefingConfiguration) as exc_info:
        BriefingConfig.from_env(env)

    assert exc_info.value.code == "invalid_configuration"


def test_invalid_preflight_stops_before_following_work():
    external_call_started = False

    with pytest.raises(InvalidBriefingConfiguration):
        BriefingConfig.from_env({"DAILY_TOP_N": "99"})
        external_call_started = True

    assert external_call_started is False


def test_semantic_dedup_timeout_inherits_quality_timeout():
    inherited = BriefingConfig.from_env({"QUALITY_GATE_TIMEOUT": "61"})
    overridden = BriefingConfig.from_env(
        {"QUALITY_GATE_TIMEOUT": "61", "SEMANTIC_DEDUP_TIMEOUT": "17"}
    )

    assert inherited.semantic_dedup_timeout == 61
    assert overridden.semantic_dedup_timeout == 17


@pytest.mark.parametrize(
    "env",
    [
        {"SEMANTIC_DEDUP_WINDOW_HOURS": "0"},
        {"SEMANTIC_DEDUP_MAX_LLM_CALLS": "-1"},
        {"SEMANTIC_DEDUP_MAX_LLM_CALLS": "101"},
        {"SEMANTIC_DEDUP_TIMEOUT": "0"},
    ],
)
def test_briefing_config_rejects_invalid_semantic_dedup_values(env):
    with pytest.raises(InvalidBriefingConfiguration):
        BriefingConfig.from_env(env)
