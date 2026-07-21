"""
Hacker News 采集器

使用 HN Firebase API 拉取 top/new/best stories，
过滤 AI 相关内容，计算社区热度信号。
"""

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from datetime import datetime, timezone
from math import log1p
from typing import Optional

import requests

from src.collectors import BaseCollector

logger = logging.getLogger(__name__)

# HN API 基础 URL
HN_API_BASE = "https://hacker-news.firebaseio.com/v0"

# === 强 AI 关键词（标题中含任一个即通过） ===
# 短关键词（<=3 字符）使用 word-boundary 匹配，避免 "ai" 匹配 "again"/"email" 等
# 注意：<=3 字符的关键词必须放这里，不能放长关键词列表（子串匹配会命中大量 false positive）
HN_STRONG_SHORT = [
    "ai", "llm", "gpt", "rag", "nlp", "cv", "phi", "xai", "tts",
]

# 长关键词（>3 字符）使用子串匹配
HN_STRONG_LONG = [
    "artificial intelligence", "machine learning", "deep learning",
    "claude", "gemini", "llama", "mixtral",
    "openai", "anthropic", "deepmind", "meta ai",
    "transformer", "diffusion", "stable diffusion", "midjourney",
    "sora", "runway", "hugging face", "huggingface",
    "chatgpt", "grok", "mistral", "perplexity",
    "generative ai", "generative", "foundation model",
    "neural network", "speech recognition",
    "image generation", "video generation", "text to speech",
    "fine-tuning", "fine tuning", "prompt engineering",
    "chain of thought", "reasoning", "alignment",
    "rlhf", "reinforcement learning", "langchain", "llamaindex",
    "vector database", "embedding", "copilot", "cursor",
    "computer vision", "multimodal", "agentic", "agent framework",
    "open source ai", "ai tool", "ai model",
    "large language model", "benchmark",
]

# === 弱 AI 关键词（需同时有 URL 信号或强社区热度才通过） ===
HN_WEAK_KEYWORDS = [
    "agent", "retrieval", "safety", "voice", "prompt",
    "chatbot", "conversational ai", "ai-powered", "ai-driven",
    "intelligence", "automation", "robotics",
]

# === 明确排除的 false positive 模式 ===
# 标题匹配这些正则 = 非 AI 内容（即使包含 AI 关键词也排除）
HN_EXCLUDE_PATTERNS = [
    # 纯医学/健康类（偶尔提到 AI 但不是主题）
    r"\btestosterone\b", r"\bcancer\b", r"\bdisease\b",
    r"\bvaccine\b", r"\bcovid\b", r"\bclinical trial\b",
    # 纯金融/经济类（非 AI 主题）
    r"\bdebit card\b", r"\bcredit card\b", r"\bmortgage\b",
    r"\binterest rate\b", r"\binflation\b",
    # 纯体育/娱乐
    r"\bnba\b", r"\bnfl\b", r"\bpremier league\b",
    r"\bnetflix\b", r"\bmarvel\b",
]

# 已知 AI 域名 — URL 来自这些域可作为强信号
HN_AI_DOMAINS = [
    "openai.com", "anthropic.com", "deepmind.google", "ai.meta.com",
    "huggingface.co", "arxiv.org", "paperswithcode.com",
    "github.com", "gitlab.com",
    "techcrunch.com", "theverge.com", "venturebeat.com",
    "mit.edu", "stanford.edu", "berkeley.edu", "cmu.edu",
    "nvidia.com", "microsoft.com/en-us/research", "research.google",
    "stability.ai", "midjourney.com", "runwayml.com",
    "langchain.com", "llamaindex.ai", "pinecone.io",
    "replicate.com", "together.ai", "fireworks.ai",
    "perplexity.ai", "you.com", "poe.com",
    "jiqizhixin.com", "qbitai.com", "jiqizhixin.com",
]


class HackerNewsCollector(BaseCollector):
    """
    Hacker News 采集器。

    拉取 topstories + newstories，过滤 AI 相关，计算社区热度。
    """

    def __init__(
        self,
        timeout: int = 30,
        max_items: int = 150,
        details_timeout: Optional[float] = None,
    ):
        super().__init__(timeout)
        self.max_items = max_items
        if details_timeout is None:
            details_timeout = _env_float("HN_DETAILS_TIMEOUT", 90.0)
        self.details_timeout = details_timeout

    def fetch(self) -> list[dict]:
        """采集 HN AI 相关新闻。"""
        # 1. 获取 story IDs
        story_ids = self._get_story_ids()
        if not story_ids:
            logger.warning("HN: no story IDs fetched")
            return []

        logger.info("HN: fetched %d story IDs, fetching details...", len(story_ids))

        # 2. 并发获取 item 详情
        items = self._fetch_items_parallel(story_ids)

        logger.info("HN: fetched %d item details", len(items))

        # 3. 过滤 AI 相关（多信号判定）
        candidates = []
        stats = {"strong_short": 0, "strong_long": 0, "weak+domain": 0,
                 "excluded": 0, "rejected": 0}
        excluded_samples = []  # 记录被排除的典型 false positive

        for item in items:
            if not item or not item.get("title"):
                continue
            title = item.get("title", "")
            url = item.get("url", "") or f"https://news.ycombinator.com/item?id={item['id']}"
            title_lower = title.lower()

            # 3a. 排除模式检查（最高优先级）
            excluded = False
            for pat in HN_EXCLUDE_PATTERNS:
                if re.search(pat, title_lower):
                    excluded = True
                    if len(excluded_samples) < 5:
                        excluded_samples.append(f"EXCLUDED [{pat}]: {title[:80]}")
                    break
            if excluded:
                stats["excluded"] += 1
                continue

            # 3b. 强 AI 信号检查
            ai_signal = self._check_ai_signal(title_lower, url)
            if not ai_signal:
                stats["rejected"] += 1
                continue

            # 记录命中类型
            stats[ai_signal] = stats.get(ai_signal, 0) + 1

            # 4. 构建 candidate
            pub_time = None
            pub_source = "missing"
            if item.get("time"):
                pub_time = datetime.fromtimestamp(item["time"], tz=timezone.utc)
                pub_source = "api"

            hn_score = item.get("score", 0) or 0
            hn_comments = item.get("descendants", 0) or 0

            # 计算 HN 社区热度
            community_hotness = log1p(hn_score) * 5 + log1p(hn_comments) * 4

            candidate = self.make_candidate(
                id_=f"hn-{item['id']}",
                title=title,
                url=url,
                source="Hacker News",
                source_type="hn",
                published_at=pub_time,
                published_source=pub_source,
                summary=item.get("text", "")[:300] if item.get("text") else "",
                author=item.get("by", ""),
                tags=["hn", "community"],
                metrics={
                    "hn_score": hn_score,
                    "hn_comments": hn_comments,
                },
            )
            candidate["scores"]["community"] = round(community_hotness, 1)
            candidate["_hn_hotness"] = round(community_hotness, 1)
            candidate["_hn_filter_reason"] = ai_signal

            candidates.append(candidate)

        # 过滤统计日志
        logger.info(
            "HN filter: %d items → %d AI (strong_short=%d, strong_long=%d, weak+domain=%d), "
            "excluded=%d, rejected=%d",
            len(items), len(candidates),
            stats.get("strong_short", 0), stats.get("strong_long", 0),
            stats.get("weak+domain", 0), stats["excluded"], stats["rejected"],
        )
        for sample in excluded_samples:
            logger.info("  %s", sample)

        return candidates

    def _get_story_ids(self) -> list[int]:
        """拉取 topstories + newstories ID 列表。"""
        all_ids = set()

        for endpoint in ("topstories", "newstories"):
            try:
                resp = requests.get(
                    f"{HN_API_BASE}/{endpoint}.json",
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                ids = resp.json()
                if isinstance(ids, list):
                    all_ids.update(ids[:self.max_items])
            except Exception as e:
                logger.warning("HN %s fetch failed: %s", endpoint, e)

        return list(all_ids)

    def _fetch_items_parallel(self, story_ids: list[int], max_workers: int = 10) -> list[Optional[dict]]:
        """并发获取 item 详情。"""
        results = []

        def _fetch_one(sid: int) -> Optional[dict]:
            try:
                resp = requests.get(
                    f"{HN_API_BASE}/item/{sid}.json",
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception:
                return None

        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = {executor.submit(_fetch_one, sid): sid for sid in story_ids}
        completed = set()
        try:
            for future in as_completed(futures, timeout=self.details_timeout):
                completed.add(future)
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except Exception:
                    pass
        except FuturesTimeoutError:
            pending = [future for future in futures if future not in completed]
            for future in pending:
                future.cancel()
            logger.warning(
                "HN item detail fetch hit total timeout %.1fs; returning %d/%d completed items",
                self.details_timeout, len(results), len(story_ids),
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        return results

    @staticmethod
    def _check_ai_signal(title_lower: str, url: str) -> str:
        """
        多信号 AI 相关性检查。

        返回命中类型字符串，空字符串表示不相关。

        判定层级：
        1. 强短关键词（word boundary）："ai", "llm", "gpt", "rag", "nlp", "cv"
        2. 强长关键词（子串匹配）："openai", "deep learning", 等
        3. 弱关键词 + 已知 AI 域名：如 "agent" + github.com
        """
        # Level 1: 强短关键词（word-boundary，避免 "ai" 匹配 "again"）
        for kw in HN_STRONG_SHORT:
            if re.search(r'\b' + re.escape(kw) + r'\b', title_lower):
                return "strong_short"

        # Level 2: 强长关键词（子串匹配）
        for kw in HN_STRONG_LONG:
            if kw in title_lower:
                return "strong_long"

        # Level 3: 弱关键词 + URL domain 信号
        has_weak = False
        for kw in HN_WEAK_KEYWORDS:
            # 弱关键词也做 word-boundary（对 >3 字符的子串匹配）
            if len(kw) <= 5:
                if re.search(r'\b' + re.escape(kw) + r'\b', title_lower):
                    has_weak = True
                    break
            else:
                if kw in title_lower:
                    has_weak = True
                    break

        if has_weak:
            # 检查 URL domain 是否为已知 AI 域
            url_lower = url.lower()
            for domain in HN_AI_DOMAINS:
                if domain in url_lower:
                    return "weak+domain"

        return ""


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Env var %s has invalid value %r, using %.1f", name, raw, default)
        return default
    return value if value > 0 else default
