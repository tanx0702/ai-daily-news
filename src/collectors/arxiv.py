"""
arXiv 采集器

使用 arXiv API 获取近期 AI 核心领域论文，
按提交时间排序，严格过滤非核心 AI 内容。

限制：最终日报最多 2 条 arXiv 论文（由 collect_news 控制）。
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Optional
from urllib.parse import urlencode

import feedparser
import requests

from src.collectors import BaseCollector

logger = logging.getLogger(__name__)

ARXIV_API_BASE = "http://export.arxiv.org/api/query"

# arXiv 分类：核心 AI 领域
ARXIV_CATEGORIES = ["cs.AI", "cs.CL", "cs.CV", "cs.LG", "stat.ML"]

# 核心 AI 关键词（标题/摘要命中才算 AI 相关）
ARXIV_AI_KEYWORDS = [
    "large language model", "llm", "gpt", "transformer",
    "diffusion model", "diffusion", "stable diffusion",
    "multimodal", "vision-language", "vision language",
    "text-to-image", "text-to-video", "image generation",
    "video generation", "speech recognition", "tts",
    "reinforcement learning", "deep reinforcement",
    "rlhf", "alignment", "safety",
    "neural network", "deep learning", "representation learning",
    "agent", "agentic", "multi-agent",
    "rag", "retrieval-augmented", "retrieval augmented",
    "prompt", "in-context learning", "few-shot",
    "chain-of-thought", "chain of thought", "reasoning",
    "fine-tuning", "fine tuning", "instruction tuning",
    "benchmark", "evaluation", "sota",
    "mixture of experts", "moe",
    "contrastive", "self-supervised",
    "generative adversarial", "gan",
    "variational autoencoder", "vae",
    "attention mechanism", "self-attention",
    "embedding", "tokenization",
    "quantization", "pruning", "distillation",
    "federated learning",
    "graph neural network", "gnn",
    "robotics", "embodied ai",
    "code generation", "program synthesis",
]


class ArxivCollector(BaseCollector):
    """
    arXiv 采集器。

    拉取最近 48 小时的 AI 论文，严格过滤核心 AI 主题。
    默认最多输出 5 条候选（最终日报由 collect_news 限制为 2 条）。
    """

    def __init__(self, timeout: int = 30, max_results: int = 60):
        super().__init__(timeout)
        self.max_results = max_results

    def fetch(self) -> list[dict]:
        """获取近期 AI 论文。"""
        # 查询最近更新的论文
        query = " OR ".join(f"cat:{cat}" for cat in ARXIV_CATEGORIES)
        params = {
            "search_query": query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": self.max_results,
        }
        url = f"{ARXIV_API_BASE}?{urlencode(params)}"

        resp = None
        for attempt in range(2):
            try:
                resp = requests.get(url, timeout=self.timeout, headers={
                    "User-Agent": "AIDailyNewsBot/1.0",
                })
                resp.raise_for_status()
                break
            except requests.exceptions.Timeout:
                if attempt == 0:
                    logger.info("arXiv API timeout; retrying once")
                    time.sleep(0.25)
                    continue
                logger.warning("arXiv API timeout after 2 attempts")
                return []
            except requests.exceptions.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                retryable = status == 429 or (isinstance(status, int) and status >= 500)
                if attempt == 0 and retryable:
                    logger.info("arXiv API HTTP %s; retrying once", status)
                    time.sleep(0.25)
                    continue
                logger.warning("arXiv API failed: %s", exc)
                return []
            except Exception as exc:
                logger.warning("arXiv API failed: %s", exc)
                return []

        if resp is None:
            return []

        feed = feedparser.parse(resp.content)
        entries = feed.entries
        if not entries:
            logger.warning("arXiv: no entries returned")
            return []

        logger.info("arXiv: fetched %d papers", len(entries))

        # 48 小时时间窗口
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        candidates = []
        stats = {"total": len(entries), "too_old": 0, "not_ai": 0, "passed": 0}

        for entry in entries:
            paper = self._parse_paper(entry)

            # 时间窗口过滤
            pub = paper.get("published_at")
            if pub and pub < cutoff:
                stats["too_old"] += 1
                continue

            # AI 相关性过滤
            title_abs = (paper.get("title", "") + " " + paper.get("summary", "")).lower()
            if not self._is_core_ai(title_abs):
                stats["not_ai"] += 1
                continue

            stats["passed"] += 1
            candidate = self._paper_to_candidate(paper)
            if candidate:
                candidates.append(candidate)

        logger.info(
            "arXiv filter: %d total → %d passed (too_old=%d, not_core_ai=%d)",
            stats["total"], stats["passed"], stats["too_old"], stats["not_ai"],
        )

        # 限制输出：arXiv 最多输出 5 条候选（最终日报由 collect_news 限制为 2）
        candidates.sort(key=lambda c: c.get("_arxiv_signal", 0), reverse=True)
        top_n = min(len(candidates), 5)
        return candidates[:top_n]

    def _parse_paper(self, entry: dict) -> dict:
        """解析 arXiv API 返回的 entry。"""
        title = unescape(entry.get("title", "")).strip()
        summary_raw = entry.get("summary", "")
        summary = unescape(summary_raw).strip() if summary_raw else ""

        # 提取 arXiv ID
        arxiv_id = ""
        id_url = entry.get("id", "")
        if "/abs/" in id_url:
            arxiv_id = id_url.split("/abs/")[-1]
        arxiv_url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else id_url

        # 作者
        authors = entry.get("authors", [])
        author_names = [a.get("name", "") for a in authors] if authors else []

        # 时间
        pub_time = None
        pub_source = "missing"
        # published_parsed from feedparser
        pub_parsed = entry.get("published_parsed")
        if pub_parsed:
            import calendar as cal
            try:
                epoch = cal.timegm(pub_parsed[:9])
                pub_time = datetime.fromtimestamp(epoch, tz=timezone.utc)
                pub_source = "api"
            except Exception:
                pass

        # 分类
        tags = []
        arxiv_cat = entry.get("arxiv_primary_category", {})
        if arxiv_cat:
            cat_term = arxiv_cat.get("term", "")
            if cat_term:
                tags.append(cat_term)

        return {
            "title": title,
            "url": arxiv_url,
            "arxiv_id": arxiv_id,
            "summary": summary[:500],
            "authors": author_names,
            "published_at": pub_time,
            "published_source": pub_source,
            "tags": tags,
        }

    def _paper_to_candidate(self, paper: dict) -> dict:
        """将论文转为统一 candidate。"""
        authors = paper.get("authors", [])
        author_str = authors[0] if authors else ""
        if len(authors) > 1:
            author_str += " et al."

        # 标题加上作者署名
        title = f"{paper['title']} [{author_str}]"

        # arXiv 信号：基于论文质量指标
        # Phase 3 暂用简单标记；后续可加 citation/star 信号
        arxiv_signal = 8.0  # 基准：核心 AI 论文都有一定技术价值

        candidate = self.make_candidate(
            id_=f"arxiv-{paper.get('arxiv_id', abs(hash(paper['title'])))}",
            title=title[:200],
            url=paper.get("url", ""),
            source="arXiv",
            source_type="arxiv",
            published_at=paper.get("published_at"),
            published_source=paper.get("published_source", "missing"),
            summary=paper.get("summary", "")[:300],
            author=author_str,
            tags=paper.get("tags", []) + ["paper", "research"],
            metrics={
                "arxiv_signal": int(arxiv_signal),
            },
        )
        candidate["scores"]["technical"] = arxiv_signal
        candidate["_arxiv_signal"] = arxiv_signal
        return candidate

    @staticmethod
    def _is_core_ai(title_abstract: str) -> bool:
        """检查论文是否属于核心 AI 主题（非边缘）。"""
        for kw in ARXIV_AI_KEYWORDS:
            if kw in title_abstract:
                return True
        return False
