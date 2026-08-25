"""Generate a temporary authenticated X snapshot outside the daily pipeline."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import os
import re
import sys
from contextlib import aclosing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

if __package__:
    from scripts.twscrape_xclid_compat import install_twscrape_xclid_compat
else:
    from twscrape_xclid_compat import install_twscrape_xclid_compat


LOGGER = logging.getLogger(__name__)
FEED_SCHEMA_VERSION = "x-feed-v1"
SOURCE_SCHEMA_VERSION = "x-sources-v1"
ID_PATTERN = re.compile(r"[0-9]{1,32}\Z")


class TimelineClient(Protocol):
    async def user_by_login(self, handle: str) -> Any: ...

    def user_tweets(self, user_id: object, limit: int) -> Any: ...


async def collect_authenticated_feed(
    client: TimelineClient,
    sources: Sequence[Mapping[str, object]],
    *,
    per_source_limit: int,
    timeout_seconds: int,
    now: datetime | None = None,
) -> dict[str, object]:
    """Collect whitelisted timelines and degrade one source at a time."""
    if per_source_limit <= 0:
        raise ValueError("per_source_limit must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    tweets: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for source in sources:
        handle = str(source.get("handle") or "").strip()
        try:
            user = await asyncio.wait_for(
                client.user_by_login(handle), timeout=timeout_seconds
            )
            user_id = _field(user, "id")
            if not _numeric_id(user_id):
                raise ValueError("source user has no numeric id")
            raw_tweets = await asyncio.wait_for(
                _read_tweets(client.user_tweets(user_id, limit=per_source_limit), per_source_limit),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            reason = _reason_code(exc)
            LOGGER.warning("Authenticated X source %s failed: %s", handle, reason)
            failures.append({"handle": handle, "reason": reason})
            continue

        valid_count = 0
        for raw_tweet in raw_tweets:
            tweet = _to_snapshot_tweet(raw_tweet, source)
            if tweet is None or tweet["tweet_id"] in seen_ids:
                continue
            seen_ids.add(str(tweet["tweet_id"]))
            tweets.append(tweet)
            valid_count += 1
        if not valid_count:
            failures.append({"handle": handle, "reason": "no_valid_tweets"})

    tweets.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema_version": FEED_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "source_count": len(sources),
        "successful_source_count": len(sources) - len(failures),
        "failed_source_count": len(failures),
        "failures": failures,
        "tweet_count": len(tweets),
        "tweets": tweets,
    }


async def _read_tweets(stream: Any, limit: int) -> list[Any]:
    """Consume both twscrape async generators and test doubles, with a hard cap."""
    if inspect.isawaitable(stream):
        stream = await stream
    if hasattr(stream, "__aiter__"):
        values: list[Any] = []
        async with aclosing(stream) as iterator:
            async for value in iterator:
                values.append(value)
                if len(values) >= limit:
                    break
        return values
    return list(stream or [])[:limit]


def write_authenticated_feed(feed: Mapping[str, object], output_path: Path) -> Path:
    """Atomically replace the public-data-only snapshot."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(feed, file, ensure_ascii=False, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    temporary_path.replace(output_path)
    return output_path


def load_sources(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise ValueError("X source configuration schema is invalid")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list):
        raise ValueError("X source configuration has no sources list")
    sources: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_source in raw_sources:
        if not isinstance(raw_source, Mapping):
            raise ValueError("X source configuration contains a non-object")
        handle = str(raw_source.get("handle") or "").strip()
        tier = str(raw_source.get("tier") or "").strip()
        name = str(raw_source.get("name") or "").strip()
        if not handle or handle.lower() in seen or tier not in {"primary", "research", "media"}:
            raise ValueError(f"Invalid or duplicate X source: {handle or name}")
        seen.add(handle.lower())
        sources.append(
            {
                "name": name,
                "handle": handle,
                "tier": tier,
                "official": bool(raw_source.get("official", False)),
                "opinion_eligible": bool(raw_source.get("opinion_eligible", False)),
            }
        )
    return sources


def _to_snapshot_tweet(raw_tweet: Any, source: Mapping[str, object]) -> dict[str, object] | None:
    tweet_id = _numeric_id(_field(raw_tweet, "id"))
    text = str(_field(raw_tweet, "rawContent") or _field(raw_tweet, "text") or "").strip()
    created_at = _normalize_date(_field(raw_tweet, "date"))
    user = _field(raw_tweet, "user")
    author = str(_field(user, "username") or _field(user, "name") or source.get("handle") or "").strip()
    handle = str(source.get("handle") or "").strip()
    if not tweet_id or not text or not created_at or not author or not handle:
        return None

    quoted = _field(raw_tweet, "quotedTweet")
    retweeted = _field(raw_tweet, "retweetedTweet")
    return {
        "tweet_id": tweet_id,
        "text": text,
        "author": author,
        "created_at": created_at,
        "url": f"https://x.com/{handle}/status/{tweet_id}",
        "source_name": str(source.get("name") or handle).strip() or handle,
        "source_handle": handle,
        "source_tier": str(source.get("tier") or "media").strip() or "media",
        "official": bool(source.get("official", False)),
        "opinion_eligible": bool(source.get("opinion_eligible", False)),
        "thread_id": _numeric_id(_field(raw_tweet, "conversationId")) or tweet_id,
        "reply_to_id": _numeric_id(_field(raw_tweet, "inReplyToTweetId")),
        "quoted_id": _numeric_id(_field(quoted, "id")),
        "is_repost": bool(retweeted),
        "context_complete": not bool(_numeric_id(_field(raw_tweet, "inReplyToTweetId"))),
    }


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _numeric_id(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if ID_PATTERN.fullmatch(candidate) else ""


def _normalize_date(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if parsed.tzinfo is None:
        return ""
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reason_code(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if "429" in text or "rate limit" in text or "rate-limit" in text:
        return "rate_limited"
    if "401" in text or "403" in text or "unauthorized" in text or "forbidden" in text:
        return "auth_failed"
    if isinstance(exc, ValueError):
        return "invalid_response"
    if "network" in text or "connect" in text:
        return "network_error"
    return "unexpected_error"


def _build_twscrape_client(database: str, timeout_seconds: int) -> TimelineClient:
    from twscrape import API

    install_twscrape_xclid_compat()
    return API(
        database,
        proxy=os.environ.get("TWS_PROXY") or None,
        raise_when_no_account=True,
        wait_timeout=timeout_seconds,
        wait_interval=1,
    )


async def _run(args: argparse.Namespace) -> int:
    client = _build_twscrape_client(args.db, args.timeout_seconds)
    sources = load_sources(args.sources)
    feed = await collect_authenticated_feed(
        client,
        sources,
        per_source_limit=args.per_source_limit,
        timeout_seconds=args.timeout_seconds,
    )
    write_authenticated_feed(feed, args.output)
    LOGGER.info(
        "Authenticated X snapshot written: tweets=%s successful_sources=%s failed_sources=%s",
        feed["tweet_count"],
        feed["successful_source_count"],
        feed["failed_source_count"],
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a temporary authenticated X snapshot")
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--per-source-limit", type=int, default=12)
    parser.add_argument("--timeout-seconds", type=int, default=45)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.error("Authenticated X snapshot failed: %s", type(exc).__name__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
