"""Strict preflight configuration for the production fact brief pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


class InvalidBriefingConfiguration(ValueError):
    """Raised before external work when briefing configuration is invalid."""

    code = "invalid_configuration"


def _integer(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise InvalidBriefingConfiguration(f"{name} must be an integer") from exc


def _boolean(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise InvalidBriefingConfiguration(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class BriefingConfig:
    """Validated production settings used by every briefing component."""

    min_items: int = 5
    max_items: int = 20
    min_fact_items: int = 2
    max_opinion_items: int = 8
    target_opinion_items: int = 5
    max_update_items: int = 8
    target_update_items: int = 5
    candidate_pool_size: int = 60
    max_x_items: int = 8
    target_x_items: int = 5
    x_feed_max_age_hours: int = 6
    news_hours: int = 36
    builder_batch_size: int = 1
    max_items_per_source: int = 2
    max_items_per_topic: int = 2
    min_primary_or_research: int = 2
    semantic_dedup_window_hours: int = 48
    semantic_dedup_max_llm_calls: int = 20
    semantic_dedup_timeout: int = 45
    skip_wechat_draft: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BriefingConfig":
        values = os.environ if env is None else env
        max_x_items = _integer(values, "DAILY_X_MAX_ITEMS", 8)
        max_opinion_items = _integer(values, "DAILY_MAX_OPINION_ITEMS", 8)
        max_update_items = _integer(values, "DAILY_MAX_UPDATE_ITEMS", 8)
        config = cls(
            min_items=_integer(values, "DAILY_MIN_ITEMS", 5),
            max_items=_integer(values, "DAILY_TOP_N", 20),
            min_fact_items=_integer(values, "DAILY_MIN_FACT_ITEMS", 2),
            max_opinion_items=max_opinion_items,
            target_opinion_items=_integer(
                values,
                "DAILY_TARGET_OPINION_ITEMS",
                min(5, max_opinion_items),
            ),
            max_update_items=max_update_items,
            target_update_items=_integer(
                values,
                "DAILY_TARGET_UPDATE_ITEMS",
                min(5, max_update_items),
            ),
            candidate_pool_size=_integer(values, "DAILY_CANDIDATE_POOL_N", 60),
            max_x_items=max_x_items,
            target_x_items=_integer(
                values,
                "DAILY_X_TARGET_ITEMS",
                min(5, max_x_items),
            ),
            x_feed_max_age_hours=_integer(values, "X_FEED_MAX_AGE_HOURS", 6),
            news_hours=_integer(values, "DAILY_NEWS_HOURS", 36),
            builder_batch_size=1,
            max_items_per_source=_integer(values, "DAILY_MAX_ITEMS_PER_SOURCE", 2),
            max_items_per_topic=_integer(values, "DAILY_MAX_ITEMS_PER_TOPIC", 2),
            min_primary_or_research=_integer(
                values,
                "DAILY_MIN_PRIMARY_OR_RESEARCH",
                2,
            ),
            semantic_dedup_window_hours=_integer(
                values,
                "SEMANTIC_DEDUP_WINDOW_HOURS",
                48,
            ),
            semantic_dedup_max_llm_calls=_integer(
                values,
                "SEMANTIC_DEDUP_MAX_LLM_CALLS",
                20,
            ),
            semantic_dedup_timeout=_integer(
                values,
                "SEMANTIC_DEDUP_TIMEOUT",
                _integer(values, "QUALITY_GATE_TIMEOUT", 45),
            ),
            skip_wechat_draft=_boolean(values, "SKIP_WECHAT_DRAFT", False),
        )
        config._validate()
        return config

    def _validate(self) -> None:
        if not 5 <= self.min_items <= self.max_items <= 20:
            raise InvalidBriefingConfiguration(
                "expected 5 <= DAILY_MIN_ITEMS <= DAILY_TOP_N <= 20"
            )
        if self.candidate_pool_size < self.max_items:
            raise InvalidBriefingConfiguration(
                "DAILY_CANDIDATE_POOL_N must be at least DAILY_TOP_N"
            )
        if not 2 <= self.min_fact_items <= self.min_items:
            raise InvalidBriefingConfiguration(
                "DAILY_MIN_FACT_ITEMS must be between 2 and DAILY_MIN_ITEMS"
            )
        if not 0 <= self.target_update_items <= self.max_update_items <= 8:
            raise InvalidBriefingConfiguration(
                "expected 0 <= DAILY_TARGET_UPDATE_ITEMS <= DAILY_MAX_UPDATE_ITEMS <= 8"
            )
        if not 0 <= self.target_opinion_items <= self.max_opinion_items <= 8:
            raise InvalidBriefingConfiguration(
                "expected 0 <= DAILY_TARGET_OPINION_ITEMS <= DAILY_MAX_OPINION_ITEMS <= 8"
            )
        if not 0 <= self.max_x_items <= 8:
            raise InvalidBriefingConfiguration(
                "DAILY_X_MAX_ITEMS must be between 0 and 8"
            )
        if not 0 <= self.target_x_items <= self.max_x_items:
            raise InvalidBriefingConfiguration(
                "DAILY_X_TARGET_ITEMS must be between 0 and DAILY_X_MAX_ITEMS"
            )
        if not 0 < self.x_feed_max_age_hours <= 6:
            raise InvalidBriefingConfiguration(
                "X_FEED_MAX_AGE_HOURS must be greater than 0 and at most 6"
            )
        if self.news_hours <= 0:
            raise InvalidBriefingConfiguration("DAILY_NEWS_HOURS must be positive")
        if self.semantic_dedup_window_hours <= 0:
            raise InvalidBriefingConfiguration(
                "SEMANTIC_DEDUP_WINDOW_HOURS must be positive"
            )
        if not 0 <= self.semantic_dedup_max_llm_calls <= 100:
            raise InvalidBriefingConfiguration(
                "SEMANTIC_DEDUP_MAX_LLM_CALLS must be between 0 and 100"
            )
        if self.semantic_dedup_timeout <= 0:
            raise InvalidBriefingConfiguration(
                "SEMANTIC_DEDUP_TIMEOUT must be positive"
            )
        preference_values = {
            "DAILY_MAX_ITEMS_PER_SOURCE": self.max_items_per_source,
            "DAILY_MAX_ITEMS_PER_TOPIC": self.max_items_per_topic,
            "DAILY_MIN_PRIMARY_OR_RESEARCH": self.min_primary_or_research,
        }
        for name, value in preference_values.items():
            if value < 0:
                raise InvalidBriefingConfiguration(f"{name} must not be negative")
