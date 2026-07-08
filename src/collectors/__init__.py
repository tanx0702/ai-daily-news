"""
采集器基类 — 定义统一的 collector 接口和 candidate 数据结构。

所有采集器（RSS、HN、GitHub、HF、arXiv）都实现 fetch() 方法，
返回统一格式的 candidate 列表。
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """采集器抽象基类。"""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    @abstractmethod
    def fetch(self) -> list[dict]:
        """
        采集新闻，返回统一 candidate 列表。

        每个 candidate 格式：
        {
            "id": str,              # 稳定唯一标识
            "title": str,
            "url": str,
            "source": str,          # 来源名称
            "source_type": str,     # rss | hn | github | huggingface | arxiv | official
            "published_at": datetime | None,
            "published_source": str, # published_parsed | updated_parsed | url | api | missing
            "summary": str,
            "author": str,
            "tags": list[str],
            "metrics": {
                "hn_score": int,
                "hn_comments": int,
                "github_stars": int,
                "github_stars_recent": int,
                "hf_likes": int,
                "hf_downloads": int,
                "arxiv_signal": int,
                "cross_source_count": int,
            },
            "scores": {
                "freshness": float,
                "authority": float,
                "community": float,
                "technical": float,
                "china_relevance": float,
                "final": float,
            },
            "topic_key": str,
        }
        """
        ...

    @staticmethod
    def make_candidate(
        id_: str,
        title: str,
        url: str,
        source: str,
        source_type: str,
        published_at: Optional[datetime] = None,
        published_source: str = "missing",
        summary: str = "",
        author: str = "",
        tags: Optional[list[str]] = None,
        metrics: Optional[dict] = None,
    ) -> dict:
        """工厂方法：创建统一 candidate 字典。"""
        return {
            "id": id_,
            "title": title,
            "url": url,
            "source": source,
            "source_type": source_type,
            "published_at": published_at,
            "published_source": published_source,
            "summary": summary,
            "author": author,
            "tags": tags or [],
            "metrics": {
                "hn_score": 0,
                "hn_comments": 0,
                "github_stars": 0,
                "github_stars_recent": 0,
                "hf_likes": 0,
                "hf_downloads": 0,
                "arxiv_signal": 0,
                "cross_source_count": 0,
                **(metrics or {}),
            },
            "scores": {
                "freshness": 0.0,
                "authority": 0.0,
                "community": 0.0,
                "technical": 0.0,
                "china_relevance": 0.0,
                "final": 0.0,
            },
            "topic_key": "",
        }
