"""
RSS 新闻采集模块

从配置的 RSS 源并行抓取 AI 相关新闻，合并去重，筛选近 24 小时的内容。
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Optional

import feedparser
import requests

logger = logging.getLogger(__name__)

# AI 相关关键词，用于过滤非 AI 新闻
AI_KEYWORDS = [
    "ai", "artificial intelligence", "machine learning", "ml", "llm",
    "large language model", "nlp", "natural language processing",
    "computer vision", "cv", "deep learning", "dl", "generative ai",
    "genai", "transformer", "gpt", "claude", "gemini", "llama",
    "multimodal", "agentic", "autonomous", "foundation model",
    "大模型", "人工智能", "机器学习", "深度学习", "自然语言处理",
    "计算机视觉", "多模态", "生成式", "智能",
]


def _load_sources(config_path: str = None) -> list[dict]:
    """加载 RSS 源配置。"""
    if config_path is None:
        config_path = "config/rss_sources.json"
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["sources"]


def _parse_rss_item(entry: dict, name_hint: str = "") -> Optional[dict]:
    """将 feedparser 的 entry 转换为结构化新闻字典。"""
    title_raw = entry.get("title", "")
    title = unescape(title_raw).strip()
    if not title:
        return None

    link = entry.get("link", "").strip()
    published_raw = entry.get("published", "") or entry.get("updated", "")
    pub_parsed = _parse_date(published_raw)
    pub_time = _parse_published(pub_parsed)

    # 摘要：优先取 summary，其次取 description
    summary_raw = entry.get("summary", "") or entry.get("description", "")
    summary = unescape(summary_raw).strip() if summary_raw else ""
    # 纯文本化：去除 HTML 标签
    summary = _strip_html(summary)

    source = entry.get("source", {}).get("title", "") or ""
    if not source:
        # 尝试从 feed 信息获取
        feed = entry.get("feed", {})
        if feed:
            source = feed.get("title", "") or ""
    if not source:
        # 从 URL 推断来源
        source = name_hint  # 传入的源名称

    return {
        "title": title,
        "url": link,
        "source": source,
        "published_at": pub_time,
        "summary": summary[:200],  # 截断过长摘要
    }


def _parse_date(raw: str):
    """解析 RSS 发布时间字符串为 time.struct_time。"""
    if not raw:
        return None
    try:
        return feedparser._parse_date(raw)
    except Exception:
        return None


def _parse_published(parsed_struct) -> Optional[datetime]:
    """将 feedparser 返回的 struct_time 转为 UTC datetime。"""
    if parsed_struct is None:
        return None
    try:
        # feedparser 返回的是 UTC 时间的 struct_time
        import calendar
        epoch = calendar.timegm(parsed_struct[:9])
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    except Exception:
        return None


def _strip_html(text: str) -> str:
    """简单去除 HTML 标签。"""
    import re
    return re.sub(r"<[^>]+>", "", text).strip()


def _is_ai_related(title: str, summary: str) -> bool:
    """判断新闻是否与 AI 相关。"""
    combined = (title + " " + summary).lower()
    for keyword in AI_KEYWORDS:
        if keyword in combined:
            return True
    return False


def _fetch_source(source: dict, timeout: int = 30) -> list[dict]:
    """抓取单个 RSS 源，支持代理 fallback。"""
    name = source["name"]
    url = source["url"]
    region = source.get("region", "overseas")

    # 对国内源尝试代理 fallback
    urls_to_try = [url]
    if region == "china":
        # RSSHub 代理格式: https://rsshub.app/{path}
        path = url.replace("https://", "").replace("http://", "").split("/", 1)[-1]
        urls_to_try.append(f"https://rsshub.app/{path}")

    for attempt_url in urls_to_try:
        items = _fetch_single(name, attempt_url, timeout)
        if items:
            return items

    logger.warning("Source '%s' returned no items after all fallbacks", name)
    return []


def _fetch_single(name: str, url: str, timeout: int) -> list[dict]:
    """抓取单个 URL，验证返回内容是否为有效 RSS。"""
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (compatible; AIDailyNewsBot/1.0)"
        })
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        logger.warning("Source %s timed out after %ds", name, timeout)
        return []
    except requests.exceptions.RequestException as e:
        logger.warning("Source %s (%s) failed: %s", name, url, e)
        return []

    # 验证：RSS 响应应为 XML，不是 HTML
    content_type = resp.headers.get("Content-Type", "").lower()
    if "text/html" in content_type and "application/xml" not in content_type:
        # 检查是否是 SPA 页面（返回 HTML 而非 XML）
        if "<html" in resp.text[:500].lower():
            logger.warning("Source '%s' returned HTML, not RSS. Skipping.", name)
            return []

    feed = feedparser.parse(resp.content)
    items = []
    for entry in feed.entries:
        item = _parse_rss_item(entry, name_hint=name)
        if item:
            items.append(item)
    logger.info("Source '%s' (%s): fetched %d items", name, url.split("//")[-1][:40], len(items))
    return items


def _title_similarity(a: str, b: str) -> float:
    """计算两个标题的相似度（基于字符集合重叠度）。"""
    if not a or not b:
        return 0.0
    set_a = set(a.lower())
    set_b = set(b.lower())
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def collect_news(
    config_path: str = None,
    hours: int = 24,
    top_n: int = 20,
    rss_timeout: int = 30,
) -> list[dict]:
    """
    采集新闻的主入口。

    Args:
        config_path: RSS 源配置文件路径
        hours: 时间窗口（小时），默认 24
        top_n: 输出新闻数量，默认 20
        rss_timeout: 单个 RSS 源超时秒数

    Returns:
        结构化新闻列表，按发布时间倒序排列
    """
    sources = _load_sources(config_path)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # 并行抓取所有源
    all_items = []
    for source in sources:
        items = _fetch_source(source, rss_timeout)
        all_items.extend(items)

    logger.info("Total fetched: %d items from %d sources", len(all_items), len(sources))

    # 去重：按 URL
    seen_urls = set()
    deduped = []
    for item in all_items:
        url = item["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # 去重：标题相似度 > 0.85 视为重复
        is_dup = False
        for existing in deduped:
            if _title_similarity(item["title"], existing["title"]) > 0.85:
                is_dup = True
                break
        if is_dup:
            continue

        deduped.append(item)

    # 筛选：近 hours 小时 + AI 相关
    filtered = []
    for item in deduped:
        pub = item.get("published_at")
        if pub and pub < cutoff:
            continue
        if not _is_ai_related(item["title"], item.get("summary", "")):
            continue
        filtered.append(item)

    logger.info("After filtering: %d items", len(filtered))

    # 按发布时间倒序
    filtered.sort(key=lambda x: x.get("published_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    # 取 top_n
    return filtered[:top_n]
