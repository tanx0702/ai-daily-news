"""读取 GitHub 静态分支上的公开 X 信息源快照。"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Mapping
from urllib.parse import urlparse

import requests

from src.collectors import BaseCollector
from src.briefing.opinion import evaluate_opinion_candidate, x_content_rejection_reason
from src.text_utils import clean_display_text


LOGGER = logging.getLogger(__name__)
FEED_SCHEMA_VERSION = "x-feed-v1"
DEFAULT_MAX_AGE_HOURS = 6
PUBLIC_ID_PATTERN = re.compile(r"[0-9]{1,32}\Z")


class XFeedCollector(BaseCollector):
    """将 GitHub Runner 生成的公开 X 快照转换为统一日报候选。"""

    def __init__(
        self,
        feed_url: str,
        timeout: int = 30,
        max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
        now: datetime | None = None,
        source_registry: Mapping[str, Mapping[str, object]] | None = None,
        local_snapshot_path: str = "",
    ) -> None:
        super().__init__(timeout)
        self.feed_url = feed_url.strip()
        self.local_snapshot_path = local_snapshot_path.strip()
        self.max_age_hours = max(int(max_age_hours), 1)
        self.now = now
        self.source_registry = dict(
            _load_source_registry() if source_registry is None else source_registry
        )

    def fetch(self) -> list[dict]:
        """获取新鲜快照；网络或契约异常不影响其他采集器。"""
        payload = self._read_local_snapshot()
        if payload is None:
            payload = self._read_remote_snapshot()
        if payload is None:
            return []

        tweets = payload["tweets"]

        candidates: list[dict] = []
        seen_ids: set[str] = set()
        for tweet in tweets:
            candidate = _tweet_to_candidate(tweet, self.source_registry)
            if candidate is None or candidate["id"] in seen_ids:
                continue
            seen_ids.add(candidate["id"])
            candidates.append(candidate)
        LOGGER.info("X feed fetched %d valid candidates", len(candidates))
        return candidates

    def _read_local_snapshot(self) -> Mapping[str, object] | None:
        """Read a VPS-generated snapshot; invalid files leave HTTPS fallback available."""
        if not self.local_snapshot_path:
            return None
        try:
            with open(self.local_snapshot_path, "r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, ValueError) as exc:
            LOGGER.warning("Local X feed snapshot unavailable: %s", exc)
            return None
        validated = self._validate_payload(payload, "local")
        if validated is not None:
            LOGGER.info("Using local X feed snapshot: %s", self.local_snapshot_path)
        return validated

    def _read_remote_snapshot(self) -> Mapping[str, object] | None:
        """Read the existing GitHub snapshot as the rollback path."""
        if not _is_https_url(self.feed_url):
            LOGGER.warning("X feed URL is not a valid HTTPS URL")
            return None
        try:
            response = requests.get(
                self.feed_url,
                timeout=self.timeout,
                headers={"User-Agent": "AI-Daily-News-XFeed/1.0"},
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            LOGGER.warning("X feed fetch failed: %s", exc)
            return None
        return self._validate_payload(payload, "remote")

    def _validate_payload(self, payload: object, label: str) -> Mapping[str, object] | None:
        if not isinstance(payload, Mapping) or payload.get("schema_version") != FEED_SCHEMA_VERSION:
            LOGGER.warning("%s X feed schema is invalid", label.capitalize())
            return None
        if not _is_fresh_snapshot(payload.get("generated_at"), self._current_time(), self.max_age_hours):
            LOGGER.warning("%s X feed snapshot is stale or has an invalid generation time", label.capitalize())
            return None
        tweets = payload.get("tweets")
        if not isinstance(tweets, list):
            LOGGER.warning("%s X feed does not contain a tweet list", label.capitalize())
            return None
        return payload

    def _current_time(self) -> datetime:
        """返回统一 UTC 当前时间，便于在测试中固定快照时效。"""
        return (self.now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _load_source_registry() -> dict[str, Mapping[str, object]]:
    path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "config", "x_sources.json")
    )
    try:
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, ValueError) as exc:
        LOGGER.warning("X source registry is unavailable: %s", exc)
        return {}
    sources = payload.get("sources") if isinstance(payload, Mapping) else None
    if not isinstance(sources, list):
        return {}
    return {
        str(source.get("handle") or "").strip().lower(): source
        for source in sources
        if isinstance(source, Mapping) and str(source.get("handle") or "").strip()
    }


def _tweet_to_candidate(
    tweet: object,
    source_registry: Mapping[str, Mapping[str, object]],
) -> dict | None:
    """转换单条已脱敏公开推文，并拒绝不完整或非 X 链接记录。"""
    if not isinstance(tweet, Mapping):
        return None
    tweet_id = _public_id(tweet.get("tweet_id"))
    text = clean_display_text(str(tweet.get("text") or ""), collapse_whitespace=False)
    url = str(tweet.get("url") or "").strip()
    source_name = clean_display_text(str(tweet.get("source_name") or ""))
    source_handle = clean_display_text(str(tweet.get("source_handle") or "")).lstrip("@")
    author = clean_display_text(str(tweet.get("author") or ""))
    source_tier = str(tweet.get("source_tier") or "").strip()
    published_at = _parse_timestamp(tweet.get("created_at"))
    if (
        not tweet_id
        or not text
        or not source_name
        or not source_handle
        or not author
        or source_tier not in {"primary", "research", "media"}
        or published_at is None
        or not _is_x_status_url(url, tweet_id, source_handle)
    ):
        return None

    registry_source = source_registry.get(source_handle.lower())
    if registry_source:
        source_name = clean_display_text(str(registry_source.get("name") or source_name))
        source_tier = str(registry_source.get("tier") or source_tier).strip()
        official = bool(registry_source.get("official", False))
        official_source = "config/x_sources.json"
        opinion_eligible = bool(registry_source.get("opinion_eligible", False))
    else:
        official = False
        official_source = ""
        opinion_eligible = False

    title_text = " ".join(text.split())
    candidate = BaseCollector.make_candidate(
        id_=f"x-{tweet_id}",
        title=f"{source_name}: {title_text[:180]}"[:200],
        url=url,
        source=f"{source_name} (X)",
        source_type="x",
        published_at=published_at,
        published_source="x_feed",
        summary=text[:900],
        author=author,
        tags=["x", "official"] if official else ["x"],
    )
    candidate["source_tier"] = source_tier
    candidate["x_official"] = official
    candidate["x_handle"] = source_handle
    candidate["x_official_source"] = official_source
    candidate["x_tweet_id"] = tweet_id
    candidate["x_thread_id"] = _public_id(tweet.get("thread_id")) or tweet_id
    candidate["x_reply_to_id"] = _public_id(tweet.get("reply_to_id"))
    candidate["x_quoted_id"] = _public_id(tweet.get("quoted_id"))
    candidate["x_is_repost"] = bool(tweet.get("is_repost", False))
    candidate["x_context_complete"] = bool(tweet.get("context_complete", False))
    rejection_reason = x_content_rejection_reason(candidate)
    if rejection_reason:
        LOGGER.info(
            "Skipping X tweet %s from %s: %s",
            tweet_id,
            source_handle,
            rejection_reason,
        )
        return None
    candidate["opinion_eligible"] = opinion_eligible
    opinion = evaluate_opinion_candidate(candidate, registry_source)
    candidate["content_type"] = "attributed_opinion" if opinion.eligible else "fact_event"
    candidate["opinion_author"] = source_name if opinion.eligible else ""
    candidate["opinion_original_post"] = opinion.original_post
    candidate["opinion_context_complete"] = opinion.context_complete
    candidate["opinion_stance_type"] = opinion.stance_type
    candidate["opinion_reason_codes"] = list(opinion.reason_codes)
    return candidate


def _public_id(value: object) -> str:
    """限制来自远程快照的 ID 为有限 ASCII 数字。"""
    candidate = str(value or "").strip()
    return candidate if PUBLIC_ID_PATTERN.fullmatch(candidate) else ""


def _parse_timestamp(value: object) -> datetime | None:
    """解析快照中的 ISO 8601 UTC 时间。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(text, "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc)
        except ValueError:
            return None


def _is_fresh_snapshot(value: object, now: datetime, max_age_hours: int) -> bool:
    """快照必须在允许窗口内，避免旧 X 内容进入当天日报。"""
    generated_at = _parse_timestamp(value)
    if generated_at is None:
        return False
    age = now - generated_at
    # 允许短暂时钟偏差，但拒绝未来时间和超出发布周期的旧快照。
    return timedelta(minutes=-5) <= age <= timedelta(hours=max_age_hours)


def _is_https_url(value: str) -> bool:
    """只允许公开 HTTPS 快照地址。"""
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _is_x_status_url(value: str, tweet_id: str, source_handle: str = "") -> bool:
    """只接收与快照 ID 一致的公开 X 推文链接。"""
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {"x.com", "www.x.com"}:
        return False
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 3 or path_parts[-2:] != ["status", tweet_id]:
        return False
    return not source_handle or path_parts[0].lower() == source_handle.lower().lstrip("@")
