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
    max_items: int = 15
    candidate_pool_size: int = 45
    max_x_items: int = 5
    x_feed_max_age_hours: int = 6
    news_hours: int = 36
    builder_batch_size: int = 5
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
        config = cls(
            min_items=_integer(values, "DAILY_MIN_ITEMS", 5),
            max_items=_integer(values, "DAILY_TOP_N", 15),
            candidate_pool_size=_integer(values, "DAILY_CANDIDATE_POOL_N", 45),
            max_x_items=_integer(values, "DAILY_X_MAX_ITEMS", 5),
            x_feed_max_age_hours=_integer(values, "X_FEED_MAX_AGE_HOURS", 6),
            news_hours=_integer(values, "DAILY_NEWS_HOURS", 36),
            builder_batch_size=5,
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
        if not 5 <= self.min_items <= self.max_items <= 15:
            raise InvalidBriefingConfiguration(
                "expected 5 <= DAILY_MIN_ITEMS <= DAILY_TOP_N <= 15"
            )
        if self.candidate_pool_size < self.max_items:
            raise InvalidBriefingConfiguration(
                "DAILY_CANDIDATE_POOL_N must be at least DAILY_TOP_N"
            )
        if not 0 <= self.max_x_items <= 5:
            raise InvalidBriefingConfiguration(
                "DAILY_X_MAX_ITEMS must be between 0 and 5"
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
