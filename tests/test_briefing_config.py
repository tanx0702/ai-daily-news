import pytest

from src.briefing.config import BriefingConfig, InvalidBriefingConfiguration


def test_briefing_config_uses_approved_defaults():
    config = BriefingConfig.from_env({})

    assert config.min_items == 5
    assert config.max_items == 15
    assert config.candidate_pool_size == 45
    assert config.max_x_items == 5
    assert config.x_feed_max_age_hours == 6
    assert config.builder_batch_size == 5
    assert config.news_hours == 36
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
            "DAILY_X_MAX_ITEMS": "5",
            "X_FEED_MAX_AGE_HOURS": "6",
            "SKIP_WECHAT_DRAFT": "0",
        }
    )

    assert (lower.min_items, lower.max_items, lower.max_x_items) == (5, 5, 0)
    assert lower.skip_wechat_draft is True
    assert (upper.min_items, upper.max_items, upper.max_x_items) == (15, 15, 5)
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
        {"DAILY_X_MAX_ITEMS": "6"},
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
