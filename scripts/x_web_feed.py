"""将已验证的公开 X 账号采集结果汇总为供日报读取的快照。"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts.x_web_probe import run_probe


LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = "x-feed-v1"
SOURCE_SCHEMA_VERSION = "x-sources-v1"
HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")
PUBLIC_ID_PATTERN = re.compile(r"[0-9]{1,32}\Z")


def load_x_sources(path: Path) -> list[dict[str, object]]:
    """加载仓库维护的公开 X 账号，并补全受限的公开主页 URL。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise ValueError("X 来源配置版本无效")

    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list):
        raise ValueError("X 来源配置缺少 sources 列表")

    sources: list[dict[str, object]] = []
    seen_handles: set[str] = set()
    for raw_source in raw_sources:
        if not isinstance(raw_source, Mapping):
            raise ValueError("X 来源配置包含非对象条目")
        name = str(raw_source.get("name") or "").strip()
        handle = str(raw_source.get("handle") or "").strip()
        tier = str(raw_source.get("tier") or "").strip()
        if not name or not HANDLE_PATTERN.fullmatch(handle) or tier not in {"primary", "research", "media"}:
            raise ValueError(f"X 来源配置无效: {name or handle or 'unknown'}")
        normalized_handle = handle.lower()
        if normalized_handle in seen_handles:
            raise ValueError(f"X 来源句柄重复: {handle}")
        seen_handles.add(normalized_handle)
        sources.append(
            {
                "name": name,
                "handle": handle,
                "tier": tier,
                "official": bool(raw_source.get("official", False)),
                "opinion_eligible": bool(raw_source.get("opinion_eligible", False)),
                "url": f"https://x.com/{handle}",
            }
        )
    return sources


def collect_x_feed(
    sources: list[Mapping[str, object]],
    work_dir: Path,
    *,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    """逐个采集公开账号，保留成功结果并记录不含原始数据的失败统计。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    tweets: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for source in sources:
        name = str(source.get("name") or "").strip()
        handle = str(source.get("handle") or "").strip()
        target_url = str(source.get("url") or f"https://x.com/{handle}").strip()
        probe_dir = work_dir / "probes" / handle.lower()
        try:
            exit_code = run_probe(target_url, probe_dir)
            report = _read_probe_report(probe_dir)
        except Exception:
            LOGGER.warning("X 来源 %s 采集失败", handle, exc_info=True)
            failures.append({"handle": handle, "reason": "probe_error"})
            continue

        raw_tweets = report.get("tweets") if isinstance(report, Mapping) else None
        if exit_code != 0 or not isinstance(raw_tweets, list):
            failures.append({"handle": handle, "reason": "no_public_tweets"})
            continue

        source_tweet_count = 0
        for raw_tweet in raw_tweets:
            tweet = _normalize_tweet(raw_tweet, source)
            if tweet is None or tweet["tweet_id"] in seen_ids:
                continue
            seen_ids.add(str(tweet["tweet_id"]))
            tweets.append(tweet)
            source_tweet_count += 1
        if source_tweet_count == 0:
            failures.append({"handle": handle, "reason": "no_valid_public_tweets"})

    tweets.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    timestamp = generated_at or datetime.now(timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_count": len(sources),
        "successful_source_count": len(sources) - len(failures),
        "failed_source_count": len(failures),
        "failures": failures,
        "tweet_count": len(tweets),
        "tweets": tweets,
    }


def write_x_feed(feed: Mapping[str, object], path: Path) -> Path:
    """写入单一 X 快照文件，供静态分支和 VPS 读取。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _read_probe_report(probe_dir: Path) -> Mapping[str, object]:
    """读取单账号 Probe 报告；缺失或损坏报告统一视为无结果。"""
    report_path = probe_dir / "probe-report.json"
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _normalize_tweet(
    raw_tweet: object,
    source: Mapping[str, object],
) -> dict[str, object] | None:
    """把 Probe 公共字段转换为日报快照契约，不携带浏览器或网络原始数据。"""
    if not isinstance(raw_tweet, Mapping):
        return None
    tweet_id = _public_id(raw_tweet.get("tweet_id"))
    text = str(raw_tweet.get("text") or "").strip()
    created_at = _normalize_created_at(raw_tweet.get("created_at"))
    handle = str(source.get("handle") or "").strip()
    if not tweet_id or not text or not created_at or not HANDLE_PATTERN.fullmatch(handle):
        return None
    return {
        "tweet_id": tweet_id,
        "text": text,
        "author": str(raw_tweet.get("author") or handle).strip() or handle,
        "created_at": created_at,
        "url": f"https://x.com/{handle}/status/{tweet_id}",
        "source_name": str(source.get("name") or handle).strip() or handle,
        "source_handle": handle,
        "source_tier": str(source.get("tier") or "media").strip() or "media",
        "official": bool(source.get("official", False)),
        "opinion_eligible": bool(source.get("opinion_eligible", False)),
        "thread_id": _public_id(raw_tweet.get("thread_id")) or tweet_id,
        "reply_to_id": _public_id(raw_tweet.get("reply_to_id")),
        "quoted_id": _public_id(raw_tweet.get("quoted_id")),
        "is_repost": bool(raw_tweet.get("is_repost", False)),
        "context_complete": bool(raw_tweet.get("context_complete", False)),
    }


def _public_id(value: object) -> str:
    """拒绝非 ASCII 或超长 ID，防止远程快照伪造线程关联。"""
    candidate = str(value or "").strip()
    return candidate if PUBLIC_ID_PATTERN.fullmatch(candidate) else ""


def _normalize_created_at(value: object) -> str:
    """将 X legacy/ISO 时间统一投影为 UTC ISO，供生产 collector 解析。"""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            return ""
    if parsed.tzinfo is None:
        return ""
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析批量 X 快照的来源、临时工作目录与输出路径。"""
    parser = argparse.ArgumentParser(description="生成公开 X 信息源快照")
    parser.add_argument("--sources", required=True, type=Path, help="X 来源配置 JSON")
    parser.add_argument("--work-dir", required=True, type=Path, help="单账号 Probe 临时目录")
    parser.add_argument("--output", required=True, type=Path, help="X 快照输出 JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """执行批量采集；全部账号没有公开推文时返回非零状态。"""
    args = parse_args(argv)
    try:
        sources = load_x_sources(args.sources)
        feed = collect_x_feed(sources, args.work_dir)
        write_x_feed(feed, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.error("生成 X 快照失败: %s", exc)
        return 2
    if int(feed["tweet_count"]) == 0:
        # X 是可选附加来源；空快照仍需发布，避免旧快照过期后继续污染生产输入。
        LOGGER.warning("X 来源本轮没有可验证推文，发布新鲜空快照并由生产采集跳过 X")
    return 0


if __name__ == "__main__":
    sys.exit(main())
