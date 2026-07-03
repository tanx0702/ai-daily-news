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

    # 完全相同
    if a_clean == b_clean:
        return 1.0

    # 一个包含另一个
    if a_clean in b_clean or b_clean in a_clean:
        shorter = min(len(a_clean), len(b_clean))
        longer = max(len(a_clean), len(b_clean))
        # 短标题被长标题包含，比例越高越相似
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

    # 去重：按 URL（完全相同 URL 直接跳过）
    seen_urls = set()
    deduped = []
    for item in all_items:
        url = item["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # 去重：标题相似度 > 0.7 视为重复（降低阈值，bigram 更精确）
        is_dup = False
        for existing in deduped:
            if _title_similarity(item["title"], existing["title"]) > 0.7:
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
