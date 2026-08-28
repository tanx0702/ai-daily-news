"""
RSS 新闻采集模块

从配置的 RSS 源并行抓取 AI 相关新闻，合并去重，筛选近 24 小时的内容。
"""

import json
import hashlib
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from math import log1p
from typing import Optional

import feedparser
import requests

from src.text_utils import clean_display_text

logger = logging.getLogger(__name__)

# 布尔环境变量真值/假值（大小写不敏感）
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})
DEFAULT_X_FEED_URL = "https://raw.githubusercontent.com/tanx0702/ai-daily-news/x-feed/x-feed.json"


def _env_enabled(name: str, default: bool = True) -> bool:
    """
    解析布尔环境变量。

    支持 1/true/yes/on（视为 True）和 0/false/no/off（视为 False），
    大小写不敏感。未设置时返回 default。
    """
    raw = os.environ.get(name, "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    # 未设置或无法识别 → 使用默认值
    if raw == "":
        return default
    logger.warning(
        "Env var %s has unrecognized value %r, falling back to default=%s",
        name, os.environ.get(name, ""), default,
    )
    return default


def _env_nonnegative_int(name: str, default: int) -> int:
    """读取非负整数配置，非法值回退默认值以避免中断日报。"""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(int(raw), 0)
    except ValueError:
        logger.warning("Env var %s has invalid integer value %r, using %d", name, raw, default)
        return default

# AI 相关关键词，用于过滤非 AI 新闻
# 分为高权重（标题中出现直接命中）和低权重（需在标题+摘要中综合判断）
AI_HIGH_KEYWORDS = [
    # 英文核心词
    "artificial intelligence", "machine learning", "deep learning",
    "large language model", "foundation model", "generative ai",
    "natural language processing", "computer vision", "multimodal",
    "llm", "gpt", "claude", "gemini", "llama", "mixtral", "phi",
    "transformer", "diffusion", "stable diffusion", "midjourney",
    "dall-e", "dalle", "sora", "kling", "runway",
    "agentic", "agent framework", "reasoning", "chain of thought",
    "rlhf", "reinforcement learning", "alignment", "safety",
    "openai", "anthropic", "google deepmind", "meta ai", "microsoft ai",
    "perplexity", "cursor", "copilot", "mistral", "grok", "xai",
    "fireworks", "together ai", "replicate", "hugging face",
    "huggingface", "langchain", "llamaindex", "rag", "retrieval",
    "embeddin", "vector database", "knowledge graph",
    "speech recognition", "text to speech", "tts", "voice synthesis",
    "video generation", "image generation", "image synthesis",
    # 中文核心词
    "大模型", "人工智能", "机器学习", "深度学习", "自然语言处理",
    "计算机视觉", "多模态", "生成式", "语音识别", "文本生成",
    "图像生成", "视频生成", "智能体", "强化学习", "对齐",
    "提示词", "prompt", "微调", "推理", "预训练",
]

AI_LOW_KEYWORDS = [
    # 英文辅助词（必须与高权重词搭配才生效）
    "ai startup", "ai funding", "ai investment", "ai company", "ai tool",
    "ai platform", "ai model", "ai research", "ai product",
    "chatbot", "conversational ai",
    "neural network", "nlp", "cv", "ai-powered", "ai-driven",
    # 中文辅助词
    "ai工具", "ai平台", "ai模型", "ai研究", "ai创业",
    "智能助手", "对话系统", "推荐系统",
]

# 来源权威度权重（0-10），知名来源获得更高基础分
SOURCE_AUTHORITY: dict[str, int] = {
    "openai": 10, "anthropic": 10, "google deepmind": 10, "deepmind": 10,
    "meta ai": 9, "microsoft": 9, "nvidia": 9,
    "techcrunch": 9, "the verge": 9, "mit technology review": 9,
    "wired": 8, "arstechnica": 8, "venturebeat": 8,
    "hacker news": 6, "hn": 6,
    "机器之心": 9, "量子位": 9, "36氪": 8, "虎嗅": 8, "钛媒体": 8, "品玩": 7,
}

# 热度关键词 — 标题中出现这些词加分（表示重要性/热度）
HOT_KEYWORDS: list[str] = [
    # 英文
    "breakthrough", "launch", "release", "announce", "unveil",
    "revolutionary", "new model", "funding", "raise", "billion",
    "acquisition", "partnership", "research", "paper", "benchmark",
    "open source", "open-source", "state of the art", "sota",
    "update", "major", "first", "biggest",
    # 中文
    "发布", "推出", "突破", "融资", "收购", "开源", "重磅",
    "首次", "重大", "最新", "万亿", "亿", "千万",
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
    title = clean_display_text(title_raw)
    if not title:
        return None

    link = entry.get("link", "").strip()
    pub_time, pub_source = _parse_published_multi(entry, link)

    # 摘要：优先取 summary，其次取 description
    summary_raw = entry.get("summary", "") or entry.get("description", "")
    summary = clean_display_text(summary_raw, collapse_whitespace=False) if summary_raw else ""
    # 纯文本化：去除 HTML 标签
    summary = clean_display_text(_strip_html(summary))
    image_candidates = _extract_rss_image_candidates(entry)

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
        "published_source": pub_source,
        "summary": summary[:200],  # 截断过长摘要
        "image_candidates": image_candidates,
    }


def _extract_rss_image_candidates(entry: dict) -> list[dict]:
    """从 RSS entry 中提取可能的原文图候选。"""
    candidates: list[dict] = []

    def add(url: str, source: str):
        if not url:
            return
        url = clean_display_text(url, collapse_whitespace=False)
        if not url or url.startswith("data:"):
            return
        if any(c.get("url") == url for c in candidates):
            return
        candidates.append({"url": url, "source": source})

    # media:content / media:thumbnail
    for key, source in [
        ("media_content", "rss:media_content"),
        ("media_thumbnail", "rss:media_thumbnail"),
        ("links", "rss:link"),
    ]:
        values = entry.get(key) or []
        if not isinstance(values, list):
            continue
        for obj in values:
            if not isinstance(obj, dict):
                continue
            href = obj.get("url") or obj.get("href")
            mime = str(obj.get("type", "")).lower()
            rel = str(obj.get("rel", "")).lower()
            if "image" in mime or key != "links" or rel in ("enclosure", "thumbnail"):
                add(href, source)

    # enclosure
    for enc in entry.get("enclosures", []) or []:
        if isinstance(enc, dict):
            mime = str(enc.get("type", "")).lower()
            if "image" in mime:
                add(enc.get("href") or enc.get("url"), "rss:enclosure")

    # summary/content HTML 内的第一张图
    html_parts = []
    for key in ("summary", "description"):
        if entry.get(key):
            html_parts.append(str(entry.get(key)))
    for content in entry.get("content", []) or []:
        if isinstance(content, dict) and content.get("value"):
            html_parts.append(str(content.get("value")))

    import re as _re
    for html_part in html_parts:
        for match in _re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html_part, _re.I):
            add(match.group(1), "rss:html_img")

    return candidates


def _parse_published_multi(entry: dict, url: str = "") -> tuple[Optional[datetime], str]:
    """
    多策略解析发布时间，返回 (datetime, source_label)。

    优先级：
    1. feedparser 预解析的 struct_time：published_parsed > updated_parsed > created_parsed
    2. 原始字符串字段：published > updated > created（feedparser._parse_date）
    3. URL 中提取日期（兜底）
    """
    import calendar
    import re

    # 策略 1: feedparser 预解析的 struct_time（最可靠）
    for key, label in [("published_parsed", "published_parsed"),
                       ("updated_parsed", "updated_parsed"),
                       ("created_parsed", "created_parsed")]:
        parsed = entry.get(key)
        if parsed is not None:
            try:
                epoch = calendar.timegm(parsed[:9])
                return datetime.fromtimestamp(epoch, tz=timezone.utc), label
            except Exception:
                continue

    # 策略 2: 原始字符串字段 + feedparser._parse_date
    for key, label in [("published", "published"),
                       ("updated", "updated"),
                       ("created", "created")]:
        raw = entry.get(key, "")
        if raw:
            try:
                struct = feedparser._parse_date(raw)
                if struct is not None:
                    epoch = calendar.timegm(struct[:9])
                    return datetime.fromtimestamp(epoch, tz=timezone.utc), label
            except Exception:
                continue

    # 策略 3: URL 日期兜底，例如 /2026/07/08/ 或 ?date=2026-07-08
    if url:
        m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
        if not m:
            m = re.search(r"[?&]date=(\d{4}-\d{2}-\d{2})", url)
            if m:
                try:
                    return (datetime.strptime(m.group(1), "%Y-%m-%d")
                            .replace(tzinfo=timezone.utc)), "url_date"
                except ValueError:
                    pass
        else:
            try:
                return (datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                 tzinfo=timezone.utc), "url_date")
            except ValueError:
                pass

    return None, "missing"


def _strip_html(text: str) -> str:
    """简单去除 HTML 标签。"""
    import re
    return re.sub(r"<[^>]+>", "", text).strip()


def _is_ai_related(title: str, summary: str) -> bool:
    """
    判断新闻是否与 AI 相关。

    规则：
    1. 标题中包含高权重关键词 → 直接命中
    2. 标题中包含低权重关键词 + 摘要中也包含任意关键词 → 命中
    3. 摘要中包含高权重关键词 → 命中
    """
    title_lower = title.lower()
    summary_lower = summary.lower()
    combined = title_lower + " " + summary_lower

    # 规则 1: 标题中有高权重关键词 → 直接命中
    for kw in AI_HIGH_KEYWORDS:
        if kw in title_lower:
            return True

    # 规则 2: 标题有低权重词 + 摘要也有任何 AI 词 → 命中
    title_low_match = False
    for kw in AI_LOW_KEYWORDS:
        if kw in title_lower:
            title_low_match = True
            break

    if title_low_match:
        for kw in AI_HIGH_KEYWORDS + AI_LOW_KEYWORDS:
            if kw in summary_lower:
                return True

    # 规则 3: 摘要中有高权重关键词 → 命中
    for kw in AI_HIGH_KEYWORDS:
        if kw in summary_lower:
            return True

    return False


def _freshness_score(published_at: Optional[datetime], now: datetime = None) -> float:
    """
    根据发布时间计算新鲜度分数。

    0-6 小时：100
    6-12 小时：80
    12-24 小时：60
    24-36 小时：35
    36 小时以上：0
    无时间：0
    """
    if published_at is None:
        return 0.0
    if now is None:
        now = datetime.now(timezone.utc)
    age_hours = (now - published_at).total_seconds() / 3600
    if age_hours <= 6:
        return 100.0
    elif age_hours <= 12:
        return 80.0
    elif age_hours <= 24:
        return 60.0
    elif age_hours <= 36:
        return 35.0
    else:
        return 0.0


def _is_hn_community_only_source(source: str, source_type: str, cross_source: int) -> bool:
    if source_type == "hn" and cross_source == 0:
        return True

    source_lower = source.lower()
    if "hacker news" not in source_lower:
        return False

    parts = [
        part.strip()
        for part in re.split(r"\s+(?:\+|\|)\s+", source_lower)
        if part.strip()
    ]
    if not parts:
        parts = [source_lower]
    return all("hacker news" in part or part in {"hn", "hnrss"} for part in parts)


def _detect_publish_risk(item: dict) -> dict:
    """
    Detect items that are AI-related but weak candidates for auto-publishing.

    This is a ranking signal only. The quality gate decides whether to remove
    an item from the final publish list.
    """
    title = item.get("title", "")
    title_lower = title.lower()
    source_type = item.get("source_type", "rss")
    metrics = item.get("metrics", {}) or {}
    cross_source = metrics.get("cross_source_count", 0) or 0
    url_is_official = _is_official_ai_org(item.get("url", ""))
    hn_community_only = _is_hn_community_only_source(
        item.get("source", ""), source_type, cross_source,
    )

    if hn_community_only and not url_is_official:
        has_model_name = re.search(
            r"\b(gpt[-\s]?\d[\w.]*|claude\s?\w+|gemini\s?\w+|llama\s?\w+|grok\s?\w+)\b",
            title_lower,
            re.I,
        )
        has_comparison = re.search(
            r"\b(vs\.?|versus|compare|comparison|test|challenge|benchmark)\b",
            title_lower,
            re.I,
        )
        has_experiment_signal = "$" in title or "music video" in title_lower
        if has_model_name and (has_comparison or has_experiment_signal):
            return {
                "category": "community_model_comparison",
                "severity": "medium",
                "penalty": 18.0,
                "reason": "HN-only community model comparison without official or cross-source confirmation",
            }

    if source_type == "rss" and cross_source == 0:
        has_finance_metric = re.search(
            r"\b(arr|revenue|valuation|funding|financing|market cap)\b|融资|收入|营收|估值|市值",
            title_lower,
            re.I,
        )
        has_large_amount = re.search(
            r"\b\d+(?:\.\d+)?\s?(?:billion|million)\b|亿美元|亿元|千万|百万|\d+\s?倍",
            title_lower,
            re.I,
        )
        if has_finance_metric and has_large_amount:
            return {
                "category": "single_source_financial_claim",
                "severity": "medium",
                "penalty": 12.0,
                "reason": "single-source financial or growth claim should not dominate ranking",
            }

    return {}


def _score_item(
    item: dict,
    all_items: list[dict],
    *,
    include_publish_risk: bool = True,
) -> float:
    """
    为新闻条目计算热度/重要性评分。

    评分维度：
    1. 新鲜度（0-100）
    2. 来源权威度（0-10）
    3. 交叉引用加分 — 同一话题被多个来源覆盖 = 热度高（每个相似项 +3）
    4. 热度关键词加分（每个匹配 +2）
    5. 摘要质量（>50 字 +2）
    6. 社区热度（HN/GitHub 信号）
    7. HN 质量门槛 — 纯 HN 低互动内容降权
    """
    score = 0.0
    source = item.get("source", "").lower()
    title = item.get("title", "").lower()
    summary = item.get("summary", "")
    source_type = item.get("source_type", "rss")
    metrics = item.get("metrics", {})

    # 预取社区信号（用于新鲜度权重判断）
    hn_s = metrics.get("hn_score", 0) or 0
    hn_c = metrics.get("hn_comments", 0) or 0
    cross_source = metrics.get("cross_source_count", 0) or 0

    # 1. 新鲜度：时间越近分越高
    # HN-only（无跨源 + 无 AI 域名 + 低社区热度）→ 降权
    # HF-only（低 likes + 无跨源）→ 降权
    freshness = _freshness_score(item.get("published_at"))
    freshness_weight = 0.5  # 默认权重

    if source_type == "hn":
        url_is_official = _is_official_ai_org(item.get("url", ""))
        # HN-only 低质量门槛：hn_score < 10 且 hn_comments < 2 且无跨源且非官方 AI 组织
        # github.com 等通用平台 NOT 自动免罚!
        if cross_source == 0 and not url_is_official and hn_s < 10 and hn_c < 2:
            freshness_weight = 0.2
            item["_hn_low_quality"] = True

    if source_type == "huggingface":
        hf_likes = metrics.get("hf_likes", 0) or 0
        hf_downloads = metrics.get("hf_downloads", 0) or 0
        # HF-only 低质量门槛：likes < 20 且 downloads < 1000 且无跨源
        if cross_source == 0 and hf_likes < 20 and hf_downloads < 1000:
            freshness_weight = 0.2
            item["_hf_low_quality"] = True

    score += freshness * freshness_weight
    item["_freshness"] = freshness
    item.setdefault("scores", {})["freshness"] = freshness

    # 2. 来源权威度
    authority = 5  # 默认中等
    for name, weight in SOURCE_AUTHORITY.items():
        if name in source:
            authority = weight
            break
    score += authority
    item.setdefault("scores", {})["authority"] = authority

    # 3. 交叉引用：同一话题被多个来源报道 → 热度高
    cross_refs = 0
    for other in all_items:
        if other is item:
            continue
        sim = _title_similarity(item["title"], other["title"])
        if sim > 0.25:  # 中等以上相似度
            cross_refs += 1
    score += cross_refs * 3

    # 4. 热度关键词
    hot_matches = 0
    for kw in HOT_KEYWORDS:
        if kw.lower() in title:
            hot_matches += 1
    score += min(hot_matches, 5) * 2  # 上限 10 分

    # 5. 摘要质量
    if len(summary) > 50:
        score += 2

    # 6. 社区热度（HN / GitHub / HF 信号）
    community = 0.0
    if hn_s > 0 or hn_c > 0:
        community += log1p(hn_s) * 1.5 + log1p(hn_c) * 1.2
    gh_stars = metrics.get("github_stars", 0) or 0
    if gh_stars > 0:
        community += log1p(gh_stars) * 1.0
    hf_likes = metrics.get("hf_likes", 0) or 0
    hf_downloads = metrics.get("hf_downloads", 0) or 0
    if hf_likes > 0:
        community += log1p(hf_likes) * 2.0
    if hf_downloads > 100:
        community += log1p(hf_downloads / 100) * 0.5
    community += cross_source * 5  # 跨源出现是强信号
    score += community

    # 7. 技术价值（arXiv 论文信号）
    arxiv_signal = metrics.get("arxiv_signal", 0) or 0
    if arxiv_signal > 0:
        score += arxiv_signal * 0.5  # arXiv 基准 8 分 → 贡献 4 分
        item.setdefault("scores", {})["technical"] = arxiv_signal

    # 8. 大厂品牌声明可信度门禁
    brand_check = _check_brand_claim(
        item.get("title", ""), item.get("url", ""),
        item.get("source", ""), source_type, metrics,
        trusted_x_source=(source_type == "x" and bool(item.get("x_official", False))),
    )
    item["_brand_claim"] = brand_check
    if brand_check["is_brand_claim"] and brand_check["confidence"] == "low":
        # 低置信度品牌声明：降权（扣减相当于 30-50% 最终分）
        penalty = score * 0.4
        score -= penalty
        item["_brand_penalty"] = round(penalty, 1)
        item.setdefault("scores", {})["brand_penalty"] = round(penalty, 1)
    item["_confidence_level"] = brand_check["confidence"] if brand_check["is_brand_claim"] else "high"

    publish_risk = _detect_publish_risk(item) if include_publish_risk else None
    if publish_risk:
        penalty = min(float(publish_risk.get("penalty", 0.0)), max(score, 0.0))
        score -= penalty
        item["_publish_risk"] = {
            "category": publish_risk["category"],
            "severity": publish_risk["severity"],
            "reason": publish_risk["reason"],
        }
        item.setdefault("scores", {})["publish_risk_penalty"] = round(penalty, 1)

    item["_community"] = round(community, 1)
    item.setdefault("scores", {})["community"] = round(community, 1)
    item["scores"]["final"] = round(score, 1)

    return score


def _is_official_ai_org(url: str) -> bool:
    """
    检查 URL 是否来自官方 AI 组织（非 github.com 等通用平台）。

    只匹配：OpenAI, Anthropic, DeepMind, Meta AI, NVIDIA, Microsoft Research,
    Google Research, Stability AI, Midjourney, Runway, HuggingFace 官方博客等。
    """
    url_lower = url.lower()
    OFFICIAL_ORGS = [
        "openai.com", "anthropic.com", "deepmind.google",
        "ai.meta.com", "research.google", "microsoft.com/en-us/research",
        "nvidia.com/en-us/research", "nvidia.com/blog",
        "stability.ai", "midjourney.com", "runwayml.com",
        "huggingface.co/blog", "huggingface.co/papers",
        "arxiv.org", "paperswithcode.com",
        "replicate.com/blog", "together.ai/blog", "fireworks.ai/blog",
        "perplexity.ai/blog", "mistral.ai",
        "x.ai", "langchain.com/blog", "llamaindex.ai/blog",
    ]
    for d in OFFICIAL_ORGS:
        if d in url_lower:
            return True
    return False


def _fetch_source(
    source: dict,
    timeout: int = 30,
    state_store=None,
) -> list[dict]:
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

    outcomes = []
    for attempt_url in urls_to_try:
        outcome = _fetch_single_outcome(name, attempt_url, timeout)
        outcomes.append(outcome)
        if outcome["items"]:
            for item in outcome["items"]:
                item["source_tier"] = source.get("tier", "media")
            if state_store is not None:
                state_store.record(
                    name,
                    url,
                    status="success",
                    item_count=len(outcome["items"]),
                    latency_ms=outcome["latency_ms"],
                    content_hash=outcome["content_hash"],
                )
            return outcome["items"]

    final = next(
        (outcome for outcome in reversed(outcomes) if outcome["status"] != "empty"),
        outcomes[-1] if outcomes else {
            "status": "error",
            "items": [],
            "latency_ms": 0,
            "content_hash": "",
            "error": "no_attempt",
        },
    )
    status = final["status"]
    if all(outcome["status"] == "empty" for outcome in outcomes):
        status = "empty"
    if state_store is not None:
        state_store.record(
            name,
            url,
            status=status,
            item_count=0,
            latency_ms=sum(outcome["latency_ms"] for outcome in outcomes),
            error=final["error"],
            content_hash=final["content_hash"],
        )
    logger.warning("Source '%s' returned no items after all fallbacks", name)
    return []


def _fetch_single(name: str, url: str, timeout: int) -> list[dict]:
    """抓取单个 URL，验证返回内容是否为有效 RSS。"""
    return _fetch_single_outcome(name, url, timeout)["items"]


def _fetch_single_outcome(name: str, url: str, timeout: int) -> dict:
    """Fetch one feed while retaining sanitized health metadata."""
    started = time.monotonic()
    outcome = {
        "items": [],
        "status": "error",
        "latency_ms": 0,
        "content_hash": "",
        "error": "",
    }
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (compatible; AIDailyNewsBot/1.0)"
        })
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        logger.warning("Source %s timed out after %ds", name, timeout)
        outcome["status"] = "timeout"
        outcome["error"] = "timeout"
        outcome["latency_ms"] = _elapsed_ms(started)
        return outcome
    except requests.exceptions.RequestException as e:
        logger.warning("Source %s (%s) failed: %s", name, url, e)
        outcome["status"] = "error"
        outcome["error"] = type(e).__name__
        outcome["latency_ms"] = _elapsed_ms(started)
        return outcome

    content = bytes(resp.content or b"")
    outcome["content_hash"] = hashlib.sha256(content).hexdigest()

    # 验证：RSS 响应应为 XML，不是 HTML
    content_type = resp.headers.get("Content-Type", "").lower()
    if "text/html" in content_type and "application/xml" not in content_type:
        # 检查是否是 SPA 页面（返回 HTML 而非 XML）
        if "<html" in resp.text[:500].lower():
            logger.warning("Source '%s' returned HTML, not RSS. Skipping.", name)
            outcome["status"] = "invalid_feed"
            outcome["error"] = "html_response"
            outcome["latency_ms"] = _elapsed_ms(started)
            return outcome

    feed = feedparser.parse(content)
    items = []
    for entry in feed.entries:
        item = _parse_rss_item(entry, name_hint=name)
        if item:
            items.append(item)
    if getattr(feed, "bozo", False) and not items:
        parse_error = type(getattr(feed, "bozo_exception", None)).__name__
        logger.warning("Source '%s' returned malformed RSS: %s", name, parse_error)
        outcome["status"] = "invalid_feed"
        outcome["error"] = "parse_error"
        outcome["latency_ms"] = _elapsed_ms(started)
        return outcome
    logger.info("Source '%s' (%s): fetched %d items", name, url.split("//")[-1][:40], len(items))
    outcome["items"] = items
    outcome["status"] = "success" if items else "empty"
    outcome["latency_ms"] = _elapsed_ms(started)
    return outcome


def _elapsed_ms(started: float) -> int:
    """Return a bounded integer duration for source diagnostics."""
    return max(int((time.monotonic() - started) * 1000), 0)


def _title_similarity(a: str, b: str) -> float:
    """
    计算两个标题的相似度，综合多种方法提高准确性。

    1. 完全相同 URL → 1.0
    2. 完全相同标题（忽略大小写）→ 1.0
    3. 中文：基于分词后的词级重叠（jieba 或字符级 bigram）
    4. 英文：基于单词级 Jaccard
    5. 子串匹配：一个包含另一个 → 高相似度
    """
    if not a or not b:
        return 0.0

    a_clean = a.strip().lower()
    b_clean = b.strip().lower()
    a_canonical = _canonical_title_for_similarity(a_clean)
    b_canonical = _canonical_title_for_similarity(b_clean)

    # 完全相同
    if a_clean == b_clean:
        return 1.0
    if a_canonical and a_canonical == b_canonical:
        return 1.0

    # 一个包含另一个
    if a_clean in b_clean or b_clean in a_clean:
        shorter = min(len(a_clean), len(b_clean))
        longer = max(len(a_clean), len(b_clean))
        # 短标题被长标题包含，比例越高越相似
        return shorter / longer if longer > 0 else 0.0
    if a_canonical and b_canonical and (
            a_canonical in b_canonical or b_canonical in a_canonical):
        shorter = min(len(a_canonical), len(b_canonical))
        longer = max(len(a_canonical), len(b_canonical))
        return shorter / longer if longer > 0 else 0.0

    # 判断是否主要包含中文
    has_chinese_a = any('一' <= c <= '鿿' for c in a_clean)
    has_chinese_b = any('一' <= c <= '鿿' for c in b_clean)

    if has_chinese_a or has_chinese_b:
        # 中文标题：使用字符级 bigram 重叠
        return _chinese_bigram_similarity(a_clean, b_clean)
    else:
        # 英文标题：使用单词级 Jaccard
        return _english_word_jaccard(a_clean, b_clean)


def _canonical_title_for_similarity(title: str) -> str:
    """Normalize editorial wrappers, punctuation, and spacing for dedup."""
    title = clean_display_text(title).lower()
    title = re.sub(r"^(独家|首发|快讯|重磅)\s*[|｜:：]\s*", "", title)
    title = re.sub(r"\s+", "", title)
    title = re.sub(r"[，,。.!！?？:：;；|｜、\"'“”‘’（）()【】\[\]\-—_]", "", title)
    return title


def _chinese_bigram_similarity(a: str, b: str) -> float:
    """中文标题的 bigram 重叠度。"""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    set_a = {a[i:i+2] for i in range(len(a)-1)}
    set_b = {b[i:i+2] for i in range(len(b)-1)}
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _english_word_jaccard(a: str, b: str) -> float:
    """英文标题的单词级 Jaccard 相似度。"""
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def _normalize_rss_item(item: dict) -> dict:
    """将旧版 RSS item 转为统一 candidate 格式。"""
    from src.collectors import BaseCollector

    url = clean_display_text(item.get("url", ""), collapse_whitespace=False)
    title = clean_display_text(item.get("title", ""))

    # 稳定 ID：URL hash 或 title hash
    id_str = url or title
    id_ = f"rss-{abs(hash(id_str))}"

    candidate = BaseCollector.make_candidate(
        id_=id_,
        title=title,
        url=url,
        source=clean_display_text(item.get("source", "")),
        source_type="rss",
        published_at=item.get("published_at"),
        published_source=item.get("published_source", "missing"),
        summary=clean_display_text(item.get("summary", "")),
    )
    if item.get("source_tier"):
        candidate["source_tier"] = item["source_tier"]
    if item.get("image_candidates"):
        candidate["image_candidates"] = item.get("image_candidates", [])
    return candidate


def _merge_candidates(candidates: list[dict]) -> list[dict]:
    """
    跨源合并候选：同 URL → 合并 metrics；标题相似 → 合并 + 提升 cross_source_count。

    合并策略：
    1. 完全相同 URL → 保留最早时间、最多 metrics
    2. 标题高度相似 (>0.7) → 合并，cross_source_count++
    """
    if len(candidates) <= 1:
        return candidates

    def _merge_image_candidates(target: dict, source: dict) -> None:
        existing = target.setdefault("image_candidates", [])
        seen = {
            cand.get("url")
            for cand in existing
            if isinstance(cand, dict) and cand.get("url")
        }
        for cand in source.get("image_candidates", []) or []:
            if isinstance(cand, dict):
                url = cand.get("url")
                if url and url not in seen:
                    existing.append(cand)
                    seen.add(url)

    # 第一轮：按 URL 去重
    by_url: dict[str, dict] = {}
    for c in candidates:
        url = c.get("url", "").strip().lower().split("?")[0]  # canonical: strip query params
        if not url:
            url = c.get("id", "")
        if url in by_url:
            existing = by_url[url]
            _merge_image_candidates(existing, c)
            # 合并 metrics
            for key in existing["metrics"]:
                existing["metrics"][key] = max(
                    existing["metrics"].get(key, 0),
                    c["metrics"].get(key, 0),
                )
            # 保留更早的发布时间
            if c.get("published_at") and (
                not existing.get("published_at")
                or c["published_at"] < existing["published_at"]
            ):
                existing["published_at"] = c["published_at"]
                existing["published_source"] = c.get("published_source", "merged")
            # 记录跨源
            if c.get("source_type") != existing.get("source_type"):
                existing["metrics"]["cross_source_count"] += 1
                existing["source"] = existing["source"] + " + " + c["source"]
        else:
            by_url[url] = c

    merged = list(by_url.values())

    # 第二轮：标题相似度合并
    final = []
    for c in merged:
        is_dup = False
        for existing in final:
            if _title_similarity(c["title"], existing["title"]) > 0.7:
                # 合并到 existing
                _merge_image_candidates(existing, c)
                for key in existing["metrics"]:
                    existing["metrics"][key] = max(
                        existing["metrics"].get(key, 0),
                        c["metrics"].get(key, 0),
                    )
                existing["metrics"]["cross_source_count"] += 1
                if c.get("source") not in existing.get("source", ""):
                    existing["source"] = existing["source"] + " + " + c["source"]
                is_dup = True
                break
        if not is_dup:
            final.append(c)

    cross_count = sum(1 for c in final if c["metrics"]["cross_source_count"] > 0)
    logger.info(
        "Merge: %d candidates → %d after URL dedup → %d after title dedup (%d cross-source)",
        len(candidates), len(merged), len(final), cross_count,
    )
    return final


# ── 头条可信度门禁：大厂品牌声明检测 ──

# 大厂名称（用于检测"官方发布"类声明）
_MAJOR_BRANDS = [
    "openai", "anthropic", "google deepmind", "deepmind",
    "microsoft", "meta", "xai", "nvidia", "apple",
    "tesla", "amazon", "intel", "amd", "ibm",
]

# 大厂产品名 → 品牌名映射（产品名 + 动作词 → 触发品牌声明检测）
_PRODUCT_TO_BRAND: dict[str, str] = {
    "gpt": "openai", "chatgpt": "openai", "dall-e": "openai",
    "dalle": "openai", "sora": "openai",
    "claude": "anthropic",
    "gemini": "google deepmind", "bard": "google deepmind",
    "llama": "meta",
    "copilot": "microsoft",
    "grok": "xai",
    "midjourney": "midjourney",
    "mistral": "mistral", "mixtral": "mistral",
    "deepseek": "deepseek",
    "qwen": "alibaba",
}

# 大厂官方域名映射（brand_name → [official domains]）
_OFFICIAL_BRAND_DOMAINS: dict[str, list[str]] = {
    "openai": ["openai.com"],
    "anthropic": ["anthropic.com"],
    "google deepmind": ["deepmind.google", "blog.google", "research.google"],
    "deepmind": ["deepmind.google", "blog.google"],
    "microsoft": ["microsoft.com", "azure.microsoft.com", "blogs.microsoft.com"],
    "meta": ["ai.meta.com", "about.fb.com", "meta.com"],
    "xai": ["x.ai"],
    "nvidia": ["nvidia.com", "blogs.nvidia.com", "research.nvidia.com"],
    "apple": ["apple.com", "machinelearning.apple.com"],
    "tesla": ["tesla.com"],
    "amazon": ["amazon.com", "aws.amazon.com"],
    "intel": ["intel.com"],
    "amd": ["amd.com"],
}

# 品牌声明动作词（中英文）—— 标题中出现这些词 + 大厂名 → 高风险声明
_BRAND_CLAIM_ACTIONS_ZH = [
    "发布", "推出", "上线", "公开", "解除限制", "收购", "合并",
    "开源", "开放", "免费", "下架", "关闭", "停止", "裁员",
    "融资", "估值", "上市", "ipo", "投资",
]
_BRAND_CLAIM_ACTIONS_EN = [
    "launch", "release", "announce", "unveil", "reveal", "roll out",
    "acquire", "merge", "open source", "open-source", "shut down",
    "discontinue", "deprecate", "lay off", "layoff", "funding", "raise",
    "valuation", "ipo", "invest", "partnership", "partner",
]


def _check_brand_claim(title: str, url: str, source: str,
                       source_type: str, metrics: dict,
                       *, trusted_x_source: bool = False) -> dict:
    """
    检测标题是否包含大厂品牌声明，返回置信度评估。

    Returns:
        {
            "is_brand_claim": bool,       # 是否涉及大厂官方动作
            "confidence": "high" | "medium" | "low",
            "brand": str,                  # 匹配到的品牌名
            "reason": str,                 # 详细原因
        }
    """
    title_lower = title.lower()
    url_lower = url.lower()

    # 1. 检查是否涉及大厂或大厂产品
    matched_brand = None
    for brand in _MAJOR_BRANDS:
        if brand in title_lower:
            matched_brand = brand
            break
    if not matched_brand:
        # 检查产品名 → 映射到品牌
        for product, brand in _PRODUCT_TO_BRAND.items():
            # 使用词边界匹配避免误匹配（如 "gpt" 不应匹配 "gpt-4" 可匹配）
            if product in title_lower:
                matched_brand = brand
                break
    if not matched_brand:
        return {"is_brand_claim": False, "confidence": "high", "brand": "", "reason": ""}

    # 2. 检查是否包含声明动作词
    has_action = False
    matched_actions = []
    for kw in _BRAND_CLAIM_ACTIONS_ZH:
        if kw in title_lower:
            has_action = True
            matched_actions.append(kw)
    for kw in _BRAND_CLAIM_ACTIONS_EN:
        if kw in title_lower:
            has_action = True
            matched_actions.append(kw)

    if not has_action:
        # 只提到大厂但不是"发布/推出"类声明 → 正常报道
        return {"is_brand_claim": False, "confidence": "high", "brand": matched_brand, "reason": ""}

    # 3. 检查来源是否是官方域名
    official_domains = _OFFICIAL_BRAND_DOMAINS.get(matched_brand, [])
    # X 的官方身份只能来自仓库维护的受控账号配置，不能由网页内容自行声明。
    is_official_source = any(d in url_lower for d in official_domains) or trusted_x_source

    # 4. 检查跨源确认
    cross_count = metrics.get("cross_source_count", 0) or 0
    has_cross_source = cross_count > 0

    # 5. 检查是否来自高权威 RSS 源（而非纯 HN/GitHub/HF/arXiv 社区讨论）
    is_community_only = source_type in ("hn", "github", "huggingface", "arxiv")
    has_rss_authority = source_type == "rss"

    # ── 判定置信度 ──
    if is_official_source:
        return {
            "is_brand_claim": True, "confidence": "high",
            "brand": matched_brand,
            "reason": (
                f"官方 X 账号确认: {source}" if trusted_x_source
                else f"官方源确认: {url_lower[:60]}"
            ),
        }

    if has_cross_source and (has_rss_authority or cross_count >= 2):
        return {
            "is_brand_claim": True, "confidence": "medium",
            "brand": matched_brand,
            "reason": f"跨源确认 (cross={cross_count}, type={source_type})",
        }

    if has_cross_source and is_community_only:
        return {
            "is_brand_claim": True, "confidence": "medium",
            "brand": matched_brand,
            "reason": f"社区跨源讨论 (cross={cross_count}, 非官方)",
        }

    # 纯社区来源 + 无跨源 → 低置信度
    return {
        "is_brand_claim": True, "confidence": "low",
        "brand": matched_brand,
        "reason": (
            f"低置信度: 仅{source_type}来源, 无官方域名确认, "
            f"动作词={','.join(matched_actions[:3])}"
        ),
    }


# 已知 AI 公司/产品名称 — 用于 topic clustering
_KNOWN_ENTITIES = [
    "openai", "anthropic", "claude", "gpt", "chatgpt", "dall-e",
    "google", "deepmind", "gemini", "bard", "meta", "llama",
    "microsoft", "copilot", "nvidia", "intel", "amd",
    "apple", "siri", "amazon", "alexa", "tesla", "elon musk",
    "stability ai", "stable diffusion", "midjourney", "runway",
    "hugging face", "huggingface", "mistral", "mixtral",
    "perplexity", "replicate", "langchain", "llamaindex",
    "cursor", "windsurf", "rowboat", "factiq", "backlog",
    "cowork", "fable", "grok", "xai", "sora", "kling",
    "nexa", "qualcomm", "kimi", "minimax", "qwen",
    "jiuqian", "jiqizhixin", "qbitai", "36kr",
    "muse", "instagram",
]


def _extract_entities(title: str) -> set[str]:
    """从标题中提取已知公司/产品实体名称。"""
    title_lower = title.lower()
    entities = set()
    for entity in _KNOWN_ENTITIES:
        if entity in title_lower:
            entities.add(entity)
    return entities


def _source_bucket(source: str) -> str:
    """Normalize a source label for publisher-level balance caps."""
    if not source:
        return ""
    source = clean_display_text(str(source)).lower()
    parts = [
        part.strip()
        for part in re.split(r"\s*(?:\+|\||/|,|，)\s*", source)
        if part.strip()
    ]
    source = parts[0] if parts else source.strip()
    return re.sub(r"\s+", " ", source)


def _publish_risk_category(item: dict) -> str:
    risk = item.get("_publish_risk", {}) or {}
    category = risk.get("category", "")
    return str(category) if category else ""


def _balance_counts(items: list[dict]) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
    type_counts: dict[str, int] = {}
    company_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}

    for item in items:
        st = item.get("source_type", "rss")
        type_counts[st] = type_counts.get(st, 0) + 1

        for ent in _extract_entities(item.get("title", "")):
            company_counts[ent] = company_counts.get(ent, 0) + 1

        source_bucket = _source_bucket(item.get("source", ""))
        if source_bucket:
            source_counts[source_bucket] = source_counts.get(source_bucket, 0) + 1

        risk_category = _publish_risk_category(item)
        if risk_category:
            risk_counts[risk_category] = risk_counts.get(risk_category, 0) + 1

    return type_counts, company_counts, source_counts, risk_counts


def _cluster_by_topic(items: list[dict]) -> tuple[list[dict], dict]:
    """
    主题聚类：合并同公司同产品的重复条目。

    规则：
    - 共享同一实体名称 + 标题相似度 > 0.4 → 合并
    - 合并后保留最高分、所有来源、所有 metrics
    - 同公司不同产品不合并（如 GPT + DALL-E）
    """
    if len(items) <= 1:
        return items, {"merged": 0}

    merged_count = 0
    # 按评分排序，高分优先作为保留项
    sorted_items = sorted(items, key=lambda x: x.get("_score", 0), reverse=True)

    clusters = []  # [(keeper, [merged_items])]

    for item in sorted_items:
        matched = False
        item_entities = _extract_entities(item.get("title", ""))

        for keeper, absorbed in clusters:
            keeper_entities = _extract_entities(keeper.get("title", ""))
            shared = item_entities & keeper_entities

            # 有共享实体 + 标题相似 → 同一 topic
            sim = _title_similarity(item["title"], keeper["title"])
            if shared and sim > 0.4:
                # 合并到 keeper
                absorbed.append(item)
                keeper["source"] = keeper.get("source", "") + " | " + item.get("source", "")
                for mk in keeper.get("metrics", {}):
                    keeper["metrics"][mk] = max(
                        keeper["metrics"].get(mk, 0),
                        item.get("metrics", {}).get(mk, 0),
                    )
                keeper["metrics"]["cross_source_count"] += 1
                merged_count += 1
                matched = True
                break

        if not matched:
            clusters.append((item, []))

    result = [keeper for keeper, _ in clusters]
    logger.info("Clustered: merged %d duplicate-topic items", merged_count)
    return result, {"merged": merged_count}


# ── 人物词典（用于事件指纹提取） ──
_PEOPLE_PATTERNS: list[str] = [
    "Sam Altman", "Greg Brockman", "Ilya Sutskever", "Mira Murati",
    "Fidji Simo", "Kevin Weil", "Dario Amodei", "Daniela Amodei",
    "Demis Hassabis", "Sundar Pichai", "Satya Nadella", "Mark Zuckerberg",
    "Elon Musk", "Jensen Huang", "Lisa Su", "Tim Cook", "Jeff Bezos",
    "Andy Jassy", "Arthur Mensch", "Aravind Srinivas",
    "Emmett Shear", "Jakub Pachocki", "John Schulman", "Jan Leike",
    "Noam Shazeer", "Aidan Gomez", "Clem Delangue", "Yann LeCun",
    "Geoffrey Hinton", "Andrew Ng", "Fei-Fei Li", "Andrej Karpathy",
    "李开复", "王小川", "周鸿祎", "张一鸣",
    "Hinton", "LeCun", "Bengio", "Karpathy",
]

# ── 动作词（中英文） ──
_ACTION_WORDS_ZH: set[str] = {
    "离职", "卸任", "加入", "任命", "发布", "推出", "开源", "收购", "融资",
    "合作", "调整", "辞职", "上任", "下台", "跳槽", "投资", "估值", "上市",
    "起诉", "诉讼", "索赔",
}
_ACTION_WORDS_EN: set[str] = {
    "resign", "step down", "leave", "join", "appoint", "launch", "release",
    "open-source", "acquire", "raise", "partner", "restructure", "depart",
    "quit", "hire", "promote", "sue", "sues", "sued", "lawsuit", "litigation",
    "legal action",
}
_LEGAL_ACTIONS = {"起诉", "诉讼", "索赔", "sue", "sues", "sued", "lawsuit", "litigation", "legal action"}


def _event_fingerprint(item: dict) -> dict:
    """
    提取轻量事件指纹：公司、人物、产品、核心动作。

    基于标题和摘要做模式匹配，用于判断两条新闻是否描述同一事件。
    """
    text = (item.get("title", "") + " " + str(item.get("summary", ""))).lower()

    # 公司 — 复用 brand claim 中的品牌映射
    companies: set[str] = set()
    for brand in _MAJOR_BRANDS:
        if brand in text:
            companies.add(brand)

    # 人物
    people: set[str] = set()
    text_original_case = item.get("title", "") + " " + str(item.get("summary", ""))
    for person in _PEOPLE_PATTERNS:
        if person.lower() in text or person in text_original_case:
            people.add(person.lower())

    # 产品（简化的型号/产品名匹配）
    products: set[str] = set()
    _product_kw = [
        "gpt", "claude", "gemini", "llama", "grok", "mistral", "mixtral",
        "deepseek", "qwen", "phi", "muse", "dall-e", "sora", "midjourney",
        "copilot", "chatgpt", "diffusion", "whisper",
    ]
    for p in _product_kw:
        if p in text:
            products.add(p)

    # 动作
    actions: set[str] = set()
    for w in _ACTION_WORDS_ZH:
        if w in text:
            actions.add(w)
    for w in _ACTION_WORDS_EN:
        if w in text:
            actions.add(w)

    return {
        "companies": companies,
        "people": people,
        "products": products,
        "actions": actions,
    }


def _fp_overlap(a: dict, b: dict) -> dict:
    """计算两个指纹的重叠情况。"""
    return {
        "shared_companies": a["companies"] & b["companies"],
        "shared_people": a["people"] & b["people"],
        "shared_products": a["products"] & b["products"],
        "shared_actions": a["actions"] & b["actions"],
    }


def _actions_same_category(actions_a: set[str], actions_b: set[str]) -> bool:
    """判断两组动作是否属于同一类别（离开/加入/发布/融资等）。"""
    _departure = {"离职", "卸任", "辞职", "下台", "跳槽", "resign", "step down", "leave", "depart", "quit"}
    _join = {"加入", "任命", "上任", "join", "appoint", "hire", "promote"}
    _release = {"发布", "推出", "开源", "launch", "release", "open-source"}
    _fund = {"融资", "投资", "估值", "上市", "acquire", "raise", "partner"}
    _legal = _LEGAL_ACTIONS

    for category in (_departure, _join, _release, _fund, _legal):
        hit_a = any(a in category for a in actions_a)
        hit_b = any(b in category for b in actions_b)
        if hit_a and hit_b:
            return True
    return False


def _pick_keeper(a: dict, b: dict) -> dict:
    """从两个重复条目中选择保留哪一条。"""
    # 1. 官方源优先
    a_official = a.get("source_type") == "rss"
    b_official = b.get("source_type") == "rss"
    if a_official and not b_official:
        return a
    if b_official and not a_official:
        return b

    # 2. cross_source_count 更高
    a_cross = (a.get("metrics", {}) or {}).get("cross_source_count", 0) or 0
    b_cross = (b.get("metrics", {}) or {}).get("cross_source_count", 0) or 0
    if a_cross > b_cross:
        return a
    if b_cross > a_cross:
        return b

    # 3. _confidence_level 更高
    a_conf = a.get("_confidence_level", "high")
    b_conf = b.get("_confidence_level", "high")
    conf_order = {"high": 3, "medium": 2, "low": 1}
    if conf_order.get(a_conf, 0) > conf_order.get(b_conf, 0):
        return a
    if conf_order.get(b_conf, 0) > conf_order.get(a_conf, 0):
        return b

    # 4. 有图片的优先
    a_has_img = bool(a.get("article_image_url"))
    b_has_img = bool(b.get("article_image_url"))
    if a_has_img and not b_has_img:
        return a
    if b_has_img and not a_has_img:
        return b

    # 5. 评分更高
    if a.get("_score", 0) >= b.get("_score", 0):
        return a
    return b


def apply_final_editorial_dedup(items: list[dict], top_n: int) -> tuple[list[dict], dict]:
    """
    最终入选后二次去重：合并描述同一事件的条目。

    规则 A: 标题相似度 >= 0.58 → 合并
    规则 B: 事件指纹重叠（共享公司/产品 + 共享人名/动作 + 综合相似度 >= 0.35）→ 合并
    规则 C: 共享人名 + 同一家公司 + 同类动作 → 合并（人名事件强合并）

    被合并条目写入 keeper 的 merged_related_items，不静默丢弃。

    Args:
        items: 最终入选列表（已排序、已做 source balance）
        top_n: 目标数量，不足时从 reserve 补充

    Returns:
        (deduped_list, dedup_report)
    """
    if len(items) <= 1:
        return items, {"merged_groups": 0, "details": []}

    keepers: list[dict] = []
    absorbed: list[dict] = []
    dedup_details: list[dict] = []

    for i, item in enumerate(items):
        matched = False
        fp_i = _event_fingerprint(item)

        for j, keeper in enumerate(keepers):
            fp_k = _event_fingerprint(keeper)
            overlap = _fp_overlap(fp_i, fp_k)

            title_sim = _title_similarity(
                item.get("chinese_title") or item.get("title", ""),
                keeper.get("chinese_title") or keeper.get("title", ""),
            )

            # 规则 A: 标题相似度 >= 0.58
            if title_sim >= 0.58:
                matched = True
                reason = "title_similarity"

            # 规则 C: 人名 + 公司 + 同类动作
            elif (overlap["shared_people"] and overlap["shared_companies"]
                  and _actions_same_category(fp_i["actions"], fp_k["actions"])):
                matched = True
                reason = "same_person_company_event"

            # 两家相同公司之间的法律事件，媒体标题往往风格差异很大，不能仅靠
            # 词面相似度判断；共享公司对与法律动作已足以说明是同一条进展。
            elif (len(overlap["shared_companies"]) >= 2
                  and (fp_i["actions"] & _LEGAL_ACTIONS)
                  and (fp_k["actions"] & _LEGAL_ACTIONS)):
                matched = True
                reason = "same_companies_legal_event"

            # 规则 B: 事件指纹重叠 + 综合相似度 >= 0.35
            elif ((overlap["shared_companies"] or overlap["shared_products"])
                  and (overlap["shared_people"] or overlap["shared_actions"])
                  and title_sim >= 0.35):
                matched = True
                reason = "event_fingerprint_overlap"

            if matched:
                keeper.setdefault("merged_related_items", []).append({
                    "title": item.get("title", ""),
                    "chinese_title": item.get("chinese_title", ""),
                    "source": item.get("source", ""),
                    "url": item.get("url", ""),
                    "reason": reason,
                })
                keeper["merged_count"] = keeper.get("merged_count", 0) + 1

                # 选择保留谁：pick_keeper 返回更优的
                better = _pick_keeper(keeper, item)
                if better is item:
                    # item 更优，替换 keeper
                    keepers[j] = item
                    # 把原 keeper 的信息转移到 item 的 merged 中
                    item["merged_related_items"] = keeper.get("merged_related_items", [])
                    item["merged_count"] = keeper.get("merged_count", 0)
                    # 原 keeper 进入 absorbed
                    absorbed.append(keeper)
                else:
                    absorbed.append(item)

                dedup_details.append({
                    "keeper": (better.get("chinese_title") or better.get("title", ""))[:60],
                    "merged": (item.get("chinese_title") or item.get("title", ""))[:60],
                    "reason": reason,
                })
                break

        if not matched:
            keepers.append(item)

    logger.info(
        "Final editorial dedup: %d items → %d (merged %d)",
        len(items), len(keepers), len(absorbed),
    )

    report = {
        "merged_groups": len(dedup_details),
        "details": dedup_details,
    }

    return keepers, report


def _fetch_hn(timeout: int = 30) -> list[dict]:
    """从 Hacker News 采集 AI 相关新闻（带异常保护）。"""
    try:
        from src.collectors.hackernews import HackerNewsCollector
        collector = HackerNewsCollector(timeout=timeout)
        return collector.fetch()
    except Exception as e:
        logger.warning("HN collector failed: %s", e)
        return []


def _fetch_github(timeout: int = 30) -> list[dict]:
    """从 GitHub 采集 AI 热门项目（带异常保护）。"""
    try:
        from src.collectors.github import GitHubCollector
        collector = GitHubCollector(timeout=timeout)
        return collector.fetch()
    except Exception as e:
        logger.warning("GitHub collector failed: %s", e)
        return []


def _fetch_hf(timeout: int = 30) -> list[dict]:
    """从 Hugging Face 采集近期热门模型（带异常保护）。"""
    try:
        from src.collectors.huggingface import HuggingFaceCollector
        collector = HuggingFaceCollector(timeout=timeout)
        return collector.fetch()
    except Exception as e:
        logger.warning("HF collector failed: %s", e)
        return []


def _fetch_arxiv(timeout: int = 30) -> list[dict]:
    """从 arXiv 采集近期 AI 论文（带异常保护）。"""
    try:
        from src.collectors.arxiv import ArxivCollector
        collector = ArxivCollector(timeout=timeout)
        return collector.fetch()
    except Exception as e:
        logger.warning("arXiv collector failed: %s", e)
        return []


def _fetch_x(timeout: int = 30) -> list[dict]:
    """读取 GitHub Runner 发布的 X 快照，失败时保持 RSS 流程可用。"""
    try:
        from src.collectors.x_feed import XFeedCollector

        collector = XFeedCollector(
            feed_url=os.environ.get("X_FEED_URL", DEFAULT_X_FEED_URL),
            timeout=timeout,
            max_age_hours=_env_nonnegative_int("X_FEED_MAX_AGE_HOURS", 6) or 6,
            local_snapshot_path=os.environ.get("X_FEED_LOCAL_PATH", ""),
        )
        return collector.fetch()
    except Exception as e:
        logger.warning("X feed collector failed: %s", e)
        return []


def _fetch_raw_candidates(
    config_path: str | None,
    rss_timeout: int,
    source_health: dict | None = None,
) -> list[dict]:
    """Fetch and normalize every enabled source without merging or ranking."""
    sources = _load_sources(config_path)
    rss_items: list[dict] = []
    with _source_state_store() as state_store:
        for source in sources:
            rss_items.extend(_fetch_source(source, rss_timeout, state_store))
        if source_health is not None:
            source_health.update(state_store.snapshot())
    logger.info("RSS fetched: %d items from %d sources", len(rss_items), len(sources))
    rss_candidates = [_normalize_rss_item(item) for item in rss_items]

    source_fetches = (
        ("HN", "ENABLE_HN_COLLECTOR", _fetch_hn),
        ("GitHub", "ENABLE_GITHUB_COLLECTOR", _fetch_github),
        ("HF", "ENABLE_HF_COLLECTOR", _fetch_hf),
        ("arXiv", "ENABLE_ARXIV_COLLECTOR", _fetch_arxiv),
        ("X feed", "ENABLE_X_COLLECTOR", _fetch_x),
    )
    collected: list[dict] = list(rss_candidates)
    for label, flag, fetcher in source_fetches:
        if _env_enabled(flag):
            candidates = fetcher(rss_timeout)
            collected.extend(candidates)
            logger.info("%s fetched: %d candidates", label, len(candidates))
        else:
            logger.info("%s collector disabled (%s=0)", label, flag)
    return collected


def _source_state_store():
    """Create the per-run source state store without leaking a DB connection."""
    from src.source_state import SourceStateStore

    return SourceStateStore.from_environment()


def collect_candidates(
    config_path: str | None = None,
    hours: int | None = None,
    limit: int | None = 45,
    rss_timeout: int = 30,
    diagnostics: dict | None = None,
    candidate_audit: list[dict[str, object]] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Return a scored candidate pool before event clustering or final quotas."""
    if hours is None:
        hours = int(os.environ.get("DAILY_NEWS_HOURS", "36"))
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = current_time - timedelta(hours=hours)
    allow_undated = _env_enabled("DAILY_ALLOW_UNDATED", default=False)
    source_health: dict = {}
    all_candidates = _fetch_raw_candidates(
        config_path,
        rss_timeout,
        source_health=source_health,
    )

    from src.evidence import preserve_source_evidence
    from src.editorial_selection import assign_source_tier

    stats = {
        "fetched_total": len(all_candidates),
        "no_date": 0,
        "too_old": 0,
        "not_ai": 0,
    }
    filtered: list[dict] = []
    for item in all_candidates:
        published_at = item.get("published_at")
        if published_at is None:
            if not allow_undated:
                stats["no_date"] += 1
                continue
        elif published_at < cutoff:
            stats["too_old"] += 1
            continue

        if item.get("source_type", "rss") == "rss" and not _is_ai_related(
            item.get("title", ""),
            item.get("summary", ""),
        ):
            stats["not_ai"] += 1
            continue

        assign_source_tier(item)
        preserve_source_evidence(item)
        filtered.append(item)

    from src.briefing.classification import classify_source_content
    from src.briefing.evidence import source_evidence_from_candidate
    from src.briefing.publishability import validate_content_source_publishability

    publishable: list[dict] = []
    preflight_rejected: list[dict] = []
    classification_rejected_items: list[dict] = []
    invalid_evidence: list[dict] = []
    preflight_reason_counts: dict[str, int] = {}
    classification_counts: dict[str, int] = {}
    classification_rejected = 0

    def record_classification(
        item: dict,
        position: int,
        *,
        source_evidence,
        original_content_type: str,
        content_type: str | None,
        classification_reason_codes: tuple[str, ...],
        subject_anchors: tuple[str, ...] = (),
        detail_anchors: tuple[str, ...] = (),
        preflight_accepted: bool,
        final_reason_codes: tuple[str, ...],
        content_llm_skipped: bool | None = None,
    ) -> None:
        if candidate_audit is None:
            return
        source_record = (
            source_evidence.to_dict()
            if source_evidence is not None
            else {
                "source_title": str(item.get("source_title") or item.get("title") or ""),
                "evidence_text": str(item.get("source_summary") or item.get("summary") or ""),
                "url": str(item.get("source_url") or item.get("url") or ""),
            }
        )
        skipped = (
            not preflight_accepted
            if content_llm_skipped is None
            else content_llm_skipped
        )
        candidate_audit.append({
            "candidate_type": "content_classification",
            "candidate_id": str(item.get("id") or item.get("url") or position),
            "source_evidence": source_record,
            "original_content_type": original_content_type,
            "content_type": content_type,
            "classification_reason_codes": list(classification_reason_codes),
            "classification_subject_anchors": list(subject_anchors),
            "classification_detail_anchors": list(detail_anchors),
            "preflight_accepted": preflight_accepted,
            "content_llm_skipped": skipped,
            "attempts": [],
            "final_state": (
                "eligible" if preflight_accepted
                else "rejected" if skipped
                else "deferred"
            ),
            "final_reason_codes": list(final_reason_codes),
        })

    for position, item in enumerate(filtered, 1):
        item["_score"] = _score_item(
            item,
            filtered,
            include_publish_risk=False,
        )
        original_content_type = str(item.get("content_type") or "fact_event")
        trusted_x_collector = (
            str(item.get("source_type") or "").strip().lower() == "x"
        )
        source_evidence = source_evidence_from_candidate(
            item,
            trusted_x_collector=trusted_x_collector,
        )
        if source_evidence is None:
            reasons = ("invalid_source_evidence",)
            item["_publishability_preflight"] = {
                "accepted": False,
                "reason_codes": list(reasons),
            }
            invalid_evidence.append(item)
            record_classification(
                item,
                position,
                source_evidence=None,
                original_content_type=original_content_type,
                content_type=None,
                classification_reason_codes=reasons,
                preflight_accepted=False,
                final_reason_codes=reasons,
            )
            continue

        classification = classify_source_content(source_evidence)
        if classification.content_type is None:
            classification_rejected += 1
            reasons = classification.reason_codes or ("non_news_content",)
            item["_publishability_preflight"] = {
                "accepted": False,
                "reason_codes": list(reasons),
            }
            classification_rejected_items.append(item)
            for reason in reasons:
                preflight_reason_counts[reason] = (
                    preflight_reason_counts.get(reason, 0) + 1
                )
            record_classification(
                item,
                position,
                source_evidence=source_evidence,
                original_content_type=original_content_type,
                content_type=None,
                classification_reason_codes=classification.reason_codes,
                subject_anchors=classification.subject_anchors,
                detail_anchors=classification.detail_anchors,
                preflight_accepted=False,
                final_reason_codes=reasons,
            )
            continue

        item["content_type"] = classification.content_type
        classification_counts[classification.content_type] = (
            classification_counts.get(classification.content_type, 0) + 1
        )
        source_evidence = source_evidence_from_candidate(
            item,
            trusted_x_collector=trusted_x_collector,
        )
        if source_evidence is None:
            reasons = ("invalid_source_evidence",)
            item["_publishability_preflight"] = {
                "accepted": False,
                "reason_codes": list(reasons),
            }
            invalid_evidence.append(item)
            record_classification(
                item,
                position,
                source_evidence=None,
                original_content_type=original_content_type,
                content_type=classification.content_type,
                classification_reason_codes=classification.reason_codes,
                subject_anchors=classification.subject_anchors,
                detail_anchors=classification.detail_anchors,
                preflight_accepted=False,
                final_reason_codes=reasons,
            )
            continue

        preflight = validate_content_source_publishability(source_evidence)
        item["_publishability_preflight"] = {
            "accepted": preflight.accepted,
            "reason_codes": list(preflight.reason_codes),
        }
        if preflight.accepted:
            publishable.append(item)
            record_classification(
                item,
                position,
                source_evidence=source_evidence,
                original_content_type=original_content_type,
                content_type=classification.content_type,
                classification_reason_codes=classification.reason_codes,
                subject_anchors=classification.subject_anchors,
                detail_anchors=classification.detail_anchors,
                preflight_accepted=True,
                final_reason_codes=(),
            )
            continue
        preflight_rejected.append(item)
        for reason in preflight.reason_codes:
            preflight_reason_counts[reason] = preflight_reason_counts.get(reason, 0) + 1
        record_classification(
            item,
            position,
            source_evidence=source_evidence,
            original_content_type=original_content_type,
            content_type=classification.content_type,
            classification_reason_codes=classification.reason_codes,
            subject_anchors=classification.subject_anchors,
            detail_anchors=classification.detail_anchors,
            preflight_accepted=False,
            final_reason_codes=preflight.reason_codes,
            content_llm_skipped=False,
        )

    def preflight_sort_key(item: dict) -> tuple:
        return (
            item.get("_score", 0),
            item.get("published_at") or datetime.min.replace(tzinfo=timezone.utc),
            str(item.get("url") or ""),
        )

    publishable.sort(key=preflight_sort_key, reverse=True)
    preflight_rejected.sort(key=preflight_sort_key, reverse=True)
    prioritized = [*publishable, *preflight_rejected]
    result = prioritized if limit is None else prioritized[:max(int(limit), 0)]
    if diagnostics is not None:
        diagnostics.update(
            {
                **stats,
                "filtered_total": len(filtered),
                "returned_candidate_count": len(result),
                "publishability_preflight_total": len(filtered),
                "publishability_preflight_passed": len(publishable),
                "publishability_preflight_rejected": (
                    len(preflight_rejected) + len(classification_rejected_items)
                ),
                "publishability_preflight_invalid_evidence": len(invalid_evidence),
                "publishability_preflight_reason_counts": dict(
                    sorted(preflight_reason_counts.items())
                ),
                "content_classification_counts": dict(
                    sorted(classification_counts.items())
                ),
                "content_classification_rejected": classification_rejected,
                "content_llm_skipped_count": (
                    len(classification_rejected_items) + len(invalid_evidence)
                ),
                "source_merge_removed": 0,
                "topic_cluster_removed": 0,
                "final_editorial_dedup_removed": 0,
                "source_health": source_health,
            }
        )
    logger.info(
        "Candidate pool: fetched=%d filtered=%d returned=%d (cutoff=%dh)",
        len(all_candidates),
        len(filtered),
        len(result),
        hours,
    )
    return result


def collect_news(
    config_path: str = None,
    hours: int = None,
    top_n: int = 20,
    rss_timeout: int = 30,
    diagnostics: dict | None = None,
) -> list[dict]:
    """
    采集新闻的主入口。

    Args:
        config_path: RSS 源配置文件路径
        hours: 时间窗口（小时），默认从 DAILY_NEWS_HOURS 读取（36）
        top_n: 输出新闻数量，默认 20
        rss_timeout: 单个 RSS 源超时秒数
        diagnostics: 可选的影子流程采集统计输出；不传时不影响既有行为

    Returns:
        结构化新闻列表，按评分倒序排列
    """
    if hours is None:
        hours = int(os.environ.get("DAILY_NEWS_HOURS", "36"))
    allow_undated = _env_enabled("DAILY_ALLOW_UNDATED", default=False)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    all_candidates = _fetch_raw_candidates(config_path, rss_timeout)

    # ---- 多源合并（legacy collect_news only） ----
    merged = _merge_candidates(all_candidates)
    from src.evidence import preserve_source_evidence
    from src.editorial_selection import assign_source_tier

    for item in merged:
        assign_source_tier(item)
        preserve_source_evidence(item)

    # ---- 过滤统计 ----
    stats = {
        "total_fetched": len(all_candidates),
        "duplicates": len(all_candidates) - len(merged),  # 被合并的
        "no_date": 0,
        "too_old": 0,
        "not_ai": 0,
        "final": 0,
    }

    logger.info("Total candidates: %d, after merge: %d", len(all_candidates), len(merged))

    # ---- 筛选：时间窗口 + 无日期过滤 + AI 相关 ----
    filtered = []
    for item in merged:
        pub = item.get("published_at")

        # 无日期：默认拒绝（DAILY_ALLOW_UNDATED=1 放行）
        if pub is None:
            if not allow_undated:
                stats["no_date"] += 1
                continue
        elif pub < cutoff:
            stats["too_old"] += 1
            continue

        # AI 相关性（非 RSS 来源已在各自受控来源中完成筛选）
        source_type = item.get("source_type", "rss")
        if source_type == "rss":
            if not _is_ai_related(item["title"], item.get("summary", "")):
                stats["not_ai"] += 1
                continue
        # HN/GitHub: 已由各自 collector 过滤，跳过 AI 检查
        filtered.append(item)

    stats["final"] = len(filtered)

    # 过滤统计日志
    logger.info(
        "Filter stats: total=%d, dup=%d, no_date=%d, too_old=%d (cutoff=%dh), not_ai=%d, final=%d",
        stats["total_fetched"], stats["duplicates"], stats["no_date"],
        stats["too_old"], hours, stats["not_ai"], stats["final"],
    )

    # 评分：综合新鲜度 + 来源权威度 + 交叉引用 + 热度关键词
    for item in filtered:
        item["_score"] = _score_item(item, filtered)

    # 按评分降序（同分按时间降序）
    filtered.sort(
        key=lambda x: (
            x.get("_score", 0),
            x.get("published_at") or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )

    # 选题聚类：合并同主题/同公司/同产品的重复条目
    clustered, cluster_stats = _cluster_by_topic(filtered)
    logger.info(
        "Topic clustering: %d items → %d (merged %d duplicate topics)",
        len(filtered), len(clustered), cluster_stats["merged"],
    )

    # 选题平衡：限制单一 source_type + 同公司/同产品上限
    balanced = _apply_source_balance(clustered, top_n)

    # 最终编辑去重：合并同一人物+公司+事件条目
    final_candidates = balanced[:top_n]
    reserves = balanced[top_n:]
    dedup_result, dedup_report = apply_final_editorial_dedup(final_candidates, top_n)
    # 不足 top_n 时从 reserve 补充
    if len(dedup_result) < top_n and reserves:
        needed = top_n - len(dedup_result)
        taken_urls = {item.get("url", "") for item in dedup_result}
        for r in reserves:
            if r.get("url", "") not in taken_urls:
                dedup_result.append(r)
                taken_urls.add(r.get("url", ""))
                if len(dedup_result) >= top_n:
                    break
        logger.info("Dedup refilled: %d items from reserves", needed)

    balanced = dedup_result

    # 打印 Top N（最终日报）
    logger.info("--- Final Top %d (DAILY_TOP_N=%d) ---", top_n, top_n)
    for i, item in enumerate(balanced[:top_n]):
        st = item.get("source_type", "rss")
        community = item.get("_community", 0)
        penalty = " [LQ]" if item.get("_hn_low_quality") else ""
        logger.info(
            "  #%d [score=%.1f, fresh=%.0f, comm=%.1f, type=%s%s] %s (%s)",
            i + 1, item.get("_score", 0), item.get("_freshness", 0),
            community, st, penalty, item["title"][:60], item.get("source", ""),
        )

    # 打印 Debug Top 15（额外上下文）
    if top_n < 15 and len(balanced) > top_n:
        logger.info("--- Debug Top 15 (for verification) ---")
        for i, item in enumerate(balanced[:15]):
            if i < top_n:
                continue  # 已在上面打印过
            st = item.get("source_type", "rss")
            community = item.get("_community", 0)
            logger.info(
                "  #%d [score=%.1f, fresh=%.0f, comm=%.1f, type=%s] %s (%s)",
                i + 1, item.get("_score", 0), item.get("_freshness", 0),
                community, st, item["title"][:60], item.get("source", ""),
            )

    result = balanced[:top_n]
    if diagnostics is not None:
        diagnostics.update(
            {
                "fetched_total": len(all_candidates),
                "source_merge_removed": len(all_candidates) - len(merged),
                "filtered_total": len(filtered),
                "topic_cluster_removed": cluster_stats["merged"],
                "final_editorial_dedup_removed": dedup_report["merged_groups"],
                "returned_candidate_count": len(result),
            }
        )
    return result


def _apply_source_balance(items: list[dict], top_n: int) -> list[dict]:
    """
    选题平衡：多维度限制确保榜单多样性。

    规则：
    - HN max 50%（上限），RSS/官方/媒体 min 40%（下限）
    - arXiv 硬上限 2，HuggingFace 硬上限 2，X 默认为 3
    - 同一公司/产品最多 2 条
    - 同一发布源和单源融资类发布风险稿设置软上限，有替补时优先分散
    - 超限项放入 reserve，不足时补充
    """
    if not items:
        return items

    hn_cap = max(int(top_n * 0.5), 4)     # HN 最多 50%
    rss_min = max(int(top_n * 0.4), 3)    # RSS/官方 最少 40%
    source_cap = max(2, int(top_n * 0.3))  # top10 时同一发布源最多 3 条
    x_cap = _env_nonnegative_int("DAILY_X_MAX_ITEMS", 3)
    risk_caps = {
        "single_source_financial_claim": max(1, int(top_n * 0.2)),
        "community_model_comparison": max(1, int(top_n * 0.1)),
    }

    HARD_CAPS = {
        "arxiv": 2,
        "huggingface": 2,
        "hn": hn_cap,
        "x": x_cap,
    }

    type_counts: dict[str, int] = {}
    company_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    selected = []
    reserves = []

    def can_select(
        item: dict,
        *,
        enforce_type: bool = True,
        enforce_company: bool = True,
        enforce_source: bool = True,
        enforce_risk: bool = True,
    ) -> bool:
        st = item.get("source_type", "rss")
        type_cap = HARD_CAPS.get(st)
        if enforce_type and type_cap is not None and type_counts.get(st, 0) >= type_cap:
            return False

        if enforce_company:
            for ent in _extract_entities(item.get("title", "")):
                if company_counts.get(ent, 0) >= 2:
                    return False

        source_bucket = _source_bucket(item.get("source", ""))
        if enforce_source and source_bucket and source_counts.get(source_bucket, 0) >= source_cap:
            return False

        risk_category = _publish_risk_category(item)
        risk_cap = risk_caps.get(risk_category)
        if enforce_risk and risk_cap is not None and risk_counts.get(risk_category, 0) >= risk_cap:
            return False

        return True

    def add_selected(item: dict, reason: str = "") -> None:
        if reason:
            item["_balance_relaxed"] = reason
        selected.append(item)

        st = item.get("source_type", "rss")
        type_counts[st] = type_counts.get(st, 0) + 1

        for ent in _extract_entities(item.get("title", "")):
            company_counts[ent] = company_counts.get(ent, 0) + 1

        source_bucket = _source_bucket(item.get("source", ""))
        if source_bucket:
            source_counts[source_bucket] = source_counts.get(source_bucket, 0) + 1

        risk_category = _publish_risk_category(item)
        if risk_category:
            risk_counts[risk_category] = risk_counts.get(risk_category, 0) + 1

    for item in items:
        if can_select(item):
            add_selected(item)
        else:
            reserves.append(item)

    # RSS 下限保障：如果 RSS 不足 40%，从 reserves 升格 RSS 条目
    rss_count = type_counts.get("rss", 0)
    rss_promoted = 0
    if rss_count < rss_min:
        for item in reserves[:]:
            if (
                item.get("source_type") == "rss"
                and rss_count < rss_min
                and can_select(item)
            ):
                add_selected(item)
                reserves.remove(item)
                rss_count += 1
                rss_promoted += 1
        if rss_count < rss_min:
            logger.info(
                "Balance: RSS target %d not met — only %d RSS items passed filtering (need %d more)",
                rss_min, rss_count, rss_min - rss_count,
            )

    # 如果 selected 不够 top_n，先从仍满足软上限的 reserves 补充
    for item in reserves[:]:
        if len(selected) >= top_n:
            break
        if can_select(item):
            add_selected(item)
            reserves.remove(item)

    # 候选不足时才放宽发布源/风险题材上限，保证日报仍能补满
    for item in reserves[:]:
        if len(selected) >= top_n:
            break
        if can_select(item, enforce_source=False, enforce_risk=False):
            add_selected(item, "候选不足，放宽发布源/风险题材上限补齐日报")
            reserves.remove(item)

    while len(selected) < top_n and reserves:
        fallback = reserves.pop(0)
        # X 来源上限是发布安全约束，候选不足时也不放宽。
        if fallback.get("source_type") == "x" and type_counts.get("x", 0) >= x_cap:
            continue
        add_selected(fallback, "候选不足，放宽全部均衡上限补齐日报")

    # ── 多样性后处理：小 top_n 时检查同实体分布 ──
    if top_n <= 10:
        selected = _ensure_diversity(selected, reserves, top_n)
        type_counts, company_counts, source_counts, risk_counts = _balance_counts(selected)

    type_dist = {st: c for st, c in type_counts.items()}
    company_dist = {e: c for e, c in company_counts.items() if c >= 2}
    source_dist = {s: c for s, c in source_counts.items() if c >= 2}
    risk_dist = {r: c for r, c in risk_counts.items() if c >= 2}
    rss_pct = type_dist.get("rss", 0) / max(len(selected), 1) * 100
    hn_pct = type_dist.get("hn", 0) / max(len(selected), 1) * 100
    logger.info(
        "Balance: top %d types=%s (RSS %.0f%% / HN %.0f%%, RSS promoted=%d)",
        len(selected), type_dist, rss_pct, hn_pct, rss_promoted,
    )
    if company_dist:
        logger.info("  Companies (>=2): %s", company_dist)
    if source_dist:
        logger.info("  Sources (>=2): %s", source_dist)
    if risk_dist:
        logger.info("  Publish risks (>=2): %s", risk_dist)

    return selected


def _ensure_diversity(selected: list[dict], reserves: list[dict], top_n: int) -> list[dict]:
    """
    多样性保障：确保同一实体不过度充斥榜单。

    当 top_n <= 5 时，同一公司/产品最多 2 条（已在主循环中保证）。
    此函数进一步检查：如果单个实体 ≥3 条出现在 selected 中
    且仍有来自不同实体的高分 reserve 可替换，则执行替换。
    """
    if not reserves or len(selected) <= 1:
        return selected

    # 统计当前各实体出现次数
    entity_map: dict[str, list[int]] = {}  # entity → [selected indices]
    for i, item in enumerate(selected):
        entities = _extract_entities(item.get("title", ""))
        for ent in entities:
            entity_map.setdefault(ent, []).append(i)

    # 找出过度集中的实体（≥3 条）
    over_entities = {ent: idxs for ent, idxs in entity_map.items() if len(idxs) >= 3}
    if not over_entities:
        return selected

    # 尝试替换：保留最高分的 2 条，其余尝试从 reserves 中找不同实体的条目替换
    replaced_count = 0
    for ent, idxs in over_entities.items():
        if len(idxs) <= 2:
            continue
        # 保留 score 最高的 2 条
        sorted_idxs = sorted(idxs, key=lambda i: selected[i].get("_score", 0), reverse=True)
        for idx in sorted_idxs[2:]:
            if not reserves:
                break
            # 找 reserves 中与当前过度实体不重叠且与 replacement 当前 selected 不重复的条目
            for ri, r_item in enumerate(reserves):
                r_entities = _extract_entities(r_item.get("title", ""))
                if ent not in r_entities:
                    # 替换
                    old_title = selected[idx].get("chinese_title") or selected[idx]["title"]
                    new_title = r_item.get("chinese_title") or r_item["title"]
                    logger.info(
                        "Diversity: replacing '%s' [entity=%s] → '%s'",
                        old_title[:40], ent, new_title[:40],
                    )
                    r_item["_diversity_swap"] = f"替补入选: 替换同实体'{ent}'过度集中"
                    selected[idx] = r_item
                    reserves.pop(ri)
                    replaced_count += 1
                    break

    if replaced_count > 0:
        logger.info("Diversity: %d items replaced for better entity balance", replaced_count)

    return selected

