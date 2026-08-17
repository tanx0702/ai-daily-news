"""GitHub Actions 中使用的公开 X 网页采集探针。"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


LOGGER = logging.getLogger(__name__)
PUBLIC_PAGE_HOSTS = frozenset({"x.com", "www.x.com", "twitter.com", "www.twitter.com"})
# X 网页前端会把公开 GraphQL 响应发往 api.x.com，页面入口仍限制为网页域名。
ALLOWED_RESPONSE_HOSTS = PUBLIC_PAGE_HOSTS | frozenset({"api.x.com"})
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
    "thread_id",
    "reply_to_id",
    "quoted_id",
    "like_count",
    "repost_count",
    "reply_count",
    "quote_count",
)
# 兼容当前 X 页面 cellInnerDiv/role=article 卡片，以及旧版 data-testid/Schema.org 标记。
# 页面改版后卡片仍包含规范 /status/ 链接，评估器会继续校验正文和状态 ID。
DOM_TWEET_SELECTOR = (
    "[data-testid='cellInnerDiv'], "
    "article:has(a[href*='/status/']), "
    "article[data-testid='tweet'], "
    "[role='article'], "
    "article[data-tweet-id][itemtype='https://schema.org/SocialMediaPosting'], "
    "a[href*='/status/']"
)
STATUS_ID_PATTERN = re.compile(r"/status/(\d+)(?:[/?#]|$)")
PUBLIC_ID_PATTERN = re.compile(r"[0-9]{1,32}\Z")
# 浏览器侧只读取页面已渲染卡片的公开字段，不保存 HTML、脚本或网络请求内容。
DOM_CARD_EVALUATOR = r"""
(cards) => cards.map((card) => {
  const isStatusLink = card.matches && card.matches("a[href*='/status/']");
  const contentRoot = (() => {
    if (!isStatusLink) return card;
    let current = card.parentElement;
    for (let depth = 0; current && depth < 12; depth += 1) {
      if (current.querySelector("[data-testid='tweetText'], div[lang]")) return current;
      current = current.parentElement;
    }
    return card.parentElement || card;
  })();
  const readMeta = (property) => {
    const node = contentRoot.querySelector("meta[itemprop='" + property + "']");
    return node ? (node.getAttribute("content") || "").trim() : "";
  };
  const statusLinks = isStatusLink
    ? [card]
    : Array.from(card.querySelectorAll("a[href*='/status/']"));
  const statusLink = statusLinks
    .find((link) => /\/status\/\d+(?:[/?#]|$)/.test(link.href));
  const statusUrl = readMeta("url") || (statusLink ? statusLink.href : "");
  const text = readMeta("articleBody") || Array.from(contentRoot.querySelectorAll(
    "[data-testid='tweetText'], div[lang]"
  ))
    .map((node) => node.innerText.trim())
    .filter(Boolean)
    .join("\n");
  const authorMeta = contentRoot.querySelector("[itemprop='author'] meta[itemprop='alternateName']");
  const userName = contentRoot.querySelector("[data-testid='User-Name']");
  const authorLink = userName
    ? Array.from(userName.querySelectorAll("a[href]")).find((link) => {
        const parts = new URL(link.href).pathname.split("/").filter(Boolean);
        return parts.length === 1;
    })
    : null;
  const statusParts = statusUrl
    ? new URL(statusUrl).pathname.split("/").filter(Boolean)
    : [];
  const statusIndex = statusParts.indexOf("status");
  const authorFromStatus = statusIndex > 0 && statusParts[statusIndex - 1] !== "i"
    ? statusParts[statusIndex - 1]
    : "";
  const author = authorMeta
    ? (authorMeta.getAttribute("content") || "").trim()
    : authorLink
    ? new URL(authorLink.href).pathname.split("/").filter(Boolean)[0] || ""
    : authorFromStatus;
  const timestamp = statusLink ? statusLink.querySelector("time") : null;
  return {
    status_url: statusUrl,
    text,
    author,
    created_at: readMeta("datePublished")
      || (timestamp ? timestamp.getAttribute("datetime") || "" : ""),
  };
})
"""


def validate_target_url(value: str) -> str:
    """限制探针只打开公开 X 页面，避免工作流被当作通用浏览器使用。"""
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or parsed.hostname not in PUBLIC_PAGE_HOSTS:
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
        and parsed.hostname in ALLOWED_RESPONSE_HOSTS
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


def _public_id(value: object) -> str:
    """只保留受限数字 ID，避免不可信关系字段进入后续聚类。"""
    candidate = _string(value)
    return candidate if PUBLIC_ID_PATTERN.fullmatch(candidate) else ""


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

        tweet_id = _public_id(candidate.get("rest_id") or legacy.get("id_str"))
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
                "thread_id": _public_id(legacy.get("conversation_id_str")) or tweet_id,
                "reply_to_id": _public_id(legacy.get("in_reply_to_status_id_str")),
                "quoted_id": _public_id(legacy.get("quoted_status_id_str")),
                "like_count": _count(legacy.get("favorite_count")),
                "repost_count": _count(legacy.get("retweet_count")),
                "reply_count": _count(legacy.get("reply_count")),
                "quote_count": _count(legacy.get("quote_count")),
            }
        )

    return tweets


def _read_dom_cards(page: Any) -> list[dict[str, object]]:
    """读取可见推文卡片的最小公开字段，不保留页面 HTML。"""
    cards = page.locator(DOM_TWEET_SELECTOR).evaluate_all(DOM_CARD_EVALUATOR)
    return cards if isinstance(cards, list) else []


def extract_dom_tweets(cards: list[object]) -> list[dict[str, object]]:
    """把页面可见推文卡片转换为与 XHR 相同的公开字段契约。"""
    tweets: list[dict[str, object]] = []
    seen_ids: set[str] = set()

    for card in cards:
        if not isinstance(card, Mapping):
            continue
        status_url = _string(card.get("status_url"))
        match = STATUS_ID_PATTERN.search(status_url)
        tweet_id = _public_id(match.group(1)) if match else ""
        text = _string(card.get("text"))
        if not tweet_id or not text or tweet_id in seen_ids:
            continue

        seen_ids.add(tweet_id)
        tweets.append(
            {
                "tweet_id": tweet_id,
                "text": text,
                "author": _string(card.get("author")),
                "created_at": _string(card.get("created_at")),
                "thread_id": tweet_id,
                "reply_to_id": "",
                "quoted_id": "",
                "like_count": 0,
                "repost_count": 0,
                "reply_count": 0,
                "quote_count": 0,
            }
        )

    return tweets


def _public_tweet(value: object) -> dict[str, object] | None:
    """复制字段白名单，确保原始响应对象不会写入诊断报告。"""
    if not isinstance(value, Mapping):
        return None
    tweet_id = _public_id(value.get("tweet_id"))
    text = _string(value.get("text"))
    if not tweet_id or not text:
        return None
    return {
        "tweet_id": tweet_id,
        "text": text,
        "author": _string(value.get("author")),
        "created_at": _string(value.get("created_at")),
        "thread_id": _public_id(value.get("thread_id")) or tweet_id,
        "reply_to_id": _public_id(value.get("reply_to_id")),
        "quoted_id": _public_id(value.get("quoted_id")),
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
    *,
    dom_tweets: list[dict[str, object]] | None = None,
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

    extraction_method = "xhr" if tweets else "none"
    if not tweets and dom_tweets:
        for item in dom_tweets:
            tweet = _public_tweet(item)
            if tweet and tweet["tweet_id"] not in seen_ids:
                seen_ids.add(str(tweet["tweet_id"]))
                tweets.append(tweet)
        if tweets:
            extraction_method = "dom_fallback"

    return {
        "schema_version": "x-web-probe-v1",
        "target_url": validate_target_url(target_url),
        "captured_operations": operations,
        "extraction_method": extraction_method,
        "tweet_count": len(tweets),
        "tweets": tweets,
        "errors": [_safe_error_code(error) for error in errors],
    }


def probe_exit_code(report: dict[str, object]) -> int:
    """至少捕获一条公开推文时返回成功退出码。"""
    return 0 if _count(report.get("tweet_count")) >= 1 else 1


def write_report(report: dict[str, object], output_dir: Path) -> Path:
    """把脱敏报告写入唯一固定文件名，方便 Actions 上传产物。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "probe-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def run_probe(target_url: str, output_dir: Path, timeout_ms: int = 45_000) -> int:
    """在隔离浏览器中捕获公开 X 响应，并写入脱敏诊断报告。"""
    normalized_url = validate_target_url(target_url)
    captured: list[dict[str, object]] = []
    errors: list[str] = []
    browser: Any = None
    page: Any = None
    report: dict[str, object] | None = None
    browser_closed = False

    try:
        # 延迟导入使解析层测试和生产 Docker 均不依赖 Playwright。
        from playwright.sync_api import sync_playwright
    except ImportError:
        LOGGER.error("未安装 Playwright，无法启动 X 网页探针")
        errors.append("playwright_unavailable")
    else:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()

                def capture(response: Any) -> None:
                    """仅解析允许列表中的推文响应，不保存原始网络数据。"""
                    if not is_allowed_response_url(response.url):
                        return
                    operation = operation_name(response.url)
                    try:
                        tweets = extract_tweets(response.json())
                    except Exception:
                        LOGGER.warning("解析 %s 响应失败", operation)
                        errors.append(f"{operation.lower()}:json_error")
                        return
                    captured.append({"operation": operation, "tweets": tweets})

                page.on("response", capture)
                try:
                    page.goto(normalized_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(8_000)
                except Exception:
                    LOGGER.warning("加载公开 X 页面失败")
                    errors.append("page_load_error")

                xhr_report = build_report(normalized_url, captured, errors)
                dom_tweets: list[dict[str, object]] = []
                if probe_exit_code(xhr_report):
                    try:
                        # 仅在 XHR 未产出推文时从已渲染 DOM 回退，保留 XHR 优先级。
                        dom_tweets = extract_dom_tweets(_read_dom_cards(page))
                    except Exception:
                        LOGGER.warning("解析页面可见推文卡片失败")
                        errors.append("dom_extraction_error")

                report = build_report(
                    normalized_url,
                    captured,
                    errors,
                    dom_tweets=dom_tweets,
                )
                write_report(report, output_dir)
                if probe_exit_code(report):
                    try:
                        # 截图必须在浏览器关闭前生成，才能保留失败页面证据。
                        page.screenshot(path=str(output_dir / "failure.png"), full_page=True)
                    except Exception:
                        LOGGER.warning("保存探针失败截图失败")

                try:
                    browser.close()
                    browser_closed = True
                except Exception:
                    LOGGER.warning("关闭探针浏览器失败")
        except Exception:
            LOGGER.exception("X 网页探针浏览器执行失败")
            errors.append("browser_execution_error")
        finally:
            if browser is not None and not browser_closed:
                try:
                    browser.close()
                except Exception:
                    LOGGER.warning("关闭探针浏览器失败")

    if report is None:
        report = build_report(normalized_url, captured, errors)
        write_report(report, output_dir)

    return probe_exit_code(report)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析手动探针的公开 URL 与报告输出目录参数。"""
    parser = argparse.ArgumentParser(description="公开 X 网页采集探针")
    parser.add_argument("--target-url", required=True, help="公开 X 页面 URL")
    parser.add_argument("--output-dir", required=True, type=Path, help="探针报告目录")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """运行命令行入口，并为无效 URL 返回稳定的退出码。"""
    args = parse_args(argv)
    try:
        target_url = validate_target_url(args.target_url)
    except ValueError as exc:
        LOGGER.error("参数错误：%s", exc)
        return 2
    return run_probe(target_url, args.output_dir)


if __name__ == "__main__":
    sys.exit(main())
