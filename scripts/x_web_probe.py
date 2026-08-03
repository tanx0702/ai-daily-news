"""GitHub Actions 中使用的公开 X 网页采集探针。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import urlparse


ALLOWED_HOSTS = frozenset({"x.com", "www.x.com", "twitter.com", "www.twitter.com"})
ALLOWED_OPERATIONS = (
    "TweetResultByRestId",
    "UserTweets",
    "UserByScreenName",
    "SearchTimeline",
)
PUBLIC_TWEET_FIELDS = (
    "tweet_id",
    "text",
    "author",
    "created_at",
    "like_count",
    "repost_count",
    "reply_count",
    "quote_count",
)


def validate_target_url(value: str) -> str:
    """限制探针只打开公开 X 页面，避免工作流被当作通用浏览器使用。"""
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("仅支持公开 X 页面 URL")
    return normalized


def operation_name(url: str) -> str:
    """从允许的 GraphQL 响应地址识别操作名称。"""
    return next((name for name in ALLOWED_OPERATIONS if name in url), "")


def is_allowed_response_url(url: str) -> bool:
    """仅接收 X 域名内、且包含推文结构的响应。"""
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname in ALLOWED_HOSTS
        and bool(operation_name(url))
    )


def _walk(value: object) -> Iterator[Mapping[str, Any]]:
    """深度遍历 JSON 容器，兼容 X 响应中的多层 instructions 结构。"""
    if isinstance(value, Mapping):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _string(value: object) -> str:
    """把缺失或非字符串的公开字段稳定转换为空字符串。"""
    return value.strip() if isinstance(value, str) else ""


def _count(value: object) -> int:
    """把互动统计转换为非负整数，异常值不进入报告。"""
    if isinstance(value, bool):
        return 0
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _author_name(tweet: Mapping[str, Any]) -> str:
    """从推文核心用户结构中提取公开账号名。"""
    core = tweet.get("core")
    if not isinstance(core, Mapping):
        return ""
    user_results = core.get("user_results")
    if not isinstance(user_results, Mapping):
        return ""
    result = user_results.get("result")
    if not isinstance(result, Mapping):
        return ""
    legacy = result.get("legacy")
    return _string(legacy.get("screen_name")) if isinstance(legacy, Mapping) else ""


def extract_tweets(payload: object) -> list[dict[str, object]]:
    """提取完整公开推文记录，按推文 ID 去重并保留首次出现顺序。"""
    tweets: list[dict[str, object]] = []
    seen_ids: set[str] = set()

    for candidate in _walk(payload):
        legacy = candidate.get("legacy")
        if not isinstance(legacy, Mapping):
            continue

        tweet_id = _string(candidate.get("rest_id") or legacy.get("id_str"))
        text = _string(legacy.get("full_text"))
        if not tweet_id or not text or tweet_id in seen_ids:
            continue

        seen_ids.add(tweet_id)
        tweets.append(
            {
                "tweet_id": tweet_id,
                "text": text,
                "author": _author_name(candidate),
                "created_at": _string(legacy.get("created_at")),
                "like_count": _count(legacy.get("favorite_count")),
                "repost_count": _count(legacy.get("retweet_count")),
                "reply_count": _count(legacy.get("reply_count")),
                "quote_count": _count(legacy.get("quote_count")),
            }
        )

    return tweets


def _public_tweet(value: object) -> dict[str, object] | None:
    """复制字段白名单，确保原始响应对象不会写入诊断报告。"""
    if not isinstance(value, Mapping):
        return None
    tweet_id = _string(value.get("tweet_id"))
    text = _string(value.get("text"))
    if not tweet_id or not text:
        return None
    return {
        "tweet_id": tweet_id,
        "text": text,
        "author": _string(value.get("author")),
        "created_at": _string(value.get("created_at")),
        "like_count": _count(value.get("like_count")),
        "repost_count": _count(value.get("repost_count")),
        "reply_count": _count(value.get("reply_count")),
        "quote_count": _count(value.get("quote_count")),
    }


def _safe_error_code(value: object) -> str:
    """报告仅保留固定格式的错误代码，不写入异常原文。"""
    text = _string(value).lower()
    return text if text.replace("_", "").replace(":", "").isalnum() else "unexpected_error"


def build_report(
    target_url: str,
    captured: list[dict[str, object]],
    errors: list[str],
) -> dict[str, object]:
    """构建仅含公开推文和脱敏诊断字段的探针报告。"""
    tweets: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    operations: list[str] = []

    for response in captured:
        operation = response.get("operation")
        if operation in ALLOWED_OPERATIONS and operation not in operations:
            operations.append(operation)
        for item in response.get("tweets", []):
            tweet = _public_tweet(item)
            if tweet and tweet["tweet_id"] not in seen_ids:
                seen_ids.add(str(tweet["tweet_id"]))
                tweets.append(tweet)

    return {
        "schema_version": "x-web-probe-v1",
        "target_url": validate_target_url(target_url),
        "captured_operations": operations,
        "tweet_count": len(tweets),
        "tweets": tweets,
        "errors": [_safe_error_code(error) for error in errors],
    }


def probe_exit_code(report: dict[str, object]) -> int:
    """至少捕获一条公开推文时返回成功退出码。"""
    return 0 if _count(report.get("tweet_count")) >= 1 else 1
