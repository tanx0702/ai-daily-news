"""
GitHub 采集器

使用 GitHub Search API 发现热门 AI 开源项目，
按 stars 排序，过滤最近创建/活跃的仓库。

GITHUB_TOKEN（可选）：设置后 API 限流从 10 次/min 提升到 30 次/min，
搜索限流从 10 次/min 提升到 30 次/min。
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from math import log1p
from typing import Optional

import requests

from src.collectors import BaseCollector

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"

# 搜索的 AI 相关 topic
AI_TOPICS = [
    "artificial-intelligence",
    "llm",
    "agents",
]

# 最小 star 阈值
MIN_STARS = 5


class GitHubCollector(BaseCollector):
    """
    GitHub 采集器。

    通过 Search API 发现 AI 相关热门仓库。
    无 GITHUB_TOKEN 时自动降级为单次轻量查询。
    """

    def __init__(self, timeout: int = 30, token: str = ""):
        super().__init__(timeout)
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self._rate_limited = False
        self._rate_remaining = "unknown"
        self._rate_limit = "unknown"

    @property
    def _headers(self) -> dict:
        h = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "AIDailyNewsBot/1.0",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _check_rate_limit(self) -> dict:
        """检查 API 限流状态。返回 {"remaining": int, "limit": int, "reset": int} 或 {}。"""
        try:
            resp = requests.get(
                f"{GITHUB_API_BASE}/rate_limit",
                headers=self._headers,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                search = data.get("resources", {}).get("search", {})
                return {
                    "remaining": search.get("remaining", 0),
                    "limit": search.get("limit", 0),
                    "reset": search.get("reset", 0),
                }
        except Exception:
            pass
        return {}

    def fetch(self) -> list[dict]:
        """搜索 GitHub AI 项目。"""
        seen_ids = set()
        candidates = []
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

        # 预检限流状态
        rate = self._check_rate_limit()
        self._rate_remaining = rate.get("remaining", "unknown")
        self._rate_limit = rate.get("limit", "unknown")

        if not self.token:
            logger.info(
                "GitHub: no GITHUB_TOKEN — auth=X, rate_limit=%s, remaining=%s",
                self._rate_limit, self._rate_remaining,
            )
        else:
            logger.info(
                "GitHub: using GITHUB_TOKEN — auth=Bearer, rate_limit=%s, remaining=%s",
                self._rate_limit, self._rate_remaining,
            )

        # 无 token + 已限流 → 跳过
        if not self.token and isinstance(self._rate_remaining, int) and self._rate_remaining == 0:
            logger.warning(
                "GitHub: search rate limit exhausted (0/%s remaining). "
                "Set GITHUB_TOKEN for 30 req/min instead of 10. Skipping GitHub.",
                self._rate_limit,
            )
            return []

        # 搜索策略：无 token 时只做 1 次查询，per_page=5
        topics_to_search = AI_TOPICS if self.token else AI_TOPICS[:1]
        per_page = self.max_per_topic if hasattr(self, 'max_per_topic') else (15 if self.token else 5)

        queries_made = 0
        queries_failed = 0

        for topic in topics_to_search:
            if self._rate_limited:
                break
            query = f"topic:{topic}+created:>={seven_days_ago}"
            items = self._search_repos(query, per_page)
            queries_made += 1

            if items is None:
                # rate limited or fatal error
                if self._rate_limited:
                    queries_failed += 1
                    break
                queries_failed += 1
                continue

            if not items:
                logger.info("  GitHub topic '%s': 0 repos found in last 7 days", topic)
                continue

            for item in items:
                rid = f"github-{item['id']}"
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
                candidate = self._repo_to_candidate(item)
                if candidate:
                    candidates.append(candidate)

            # 无 token 时只搜 1 个 topic
            if not self.token:
                break

            time.sleep(1.5)

        # 诊断日志
        logger.info(
            "GitHub: %d candidates from %d unique repos (queries: %d ok, %d failed, rate_remaining=%s)",
            len(candidates), len(seen_ids), queries_made - queries_failed,
            queries_failed, self._rate_remaining,
        )

        if not candidates and not self._rate_limited:
            logger.info(
                "GitHub: no AI repos found in last 7 days with stars >= %d. "
                "This may be normal — new AI repos with high stars are rare.",
                MIN_STARS,
            )

        return candidates

    def _search_repos(self, query: str, per_page: int = 15) -> Optional[list[dict]]:
        """
        执行一次 GitHub 仓库搜索。

        Returns:
            list of repo dicts on success, None on rate limit (caller should stop),
            empty list on no results or recoverable error.
        """
        url = f"{GITHUB_API_BASE}/search/repositories"
        params = {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": per_page,
        }
        try:
            resp = requests.get(
                url,
                params=params,
                headers=self._headers,
                timeout=self.timeout,
            )

            # 解析限流头
            remaining = resp.headers.get("X-RateLimit-Remaining", "")
            if remaining:
                try:
                    self._rate_remaining = int(remaining)
                except ValueError:
                    pass

            if resp.status_code == 403:
                reset_ts = resp.headers.get("X-RateLimit-Reset", "")
                reset_str = ""
                if reset_ts:
                    try:
                        reset_dt = datetime.fromtimestamp(int(reset_ts), tz=timezone.utc)
                        reset_str = f", resets at {reset_dt.strftime('%H:%M UTC')}"
                    except (ValueError, OSError):
                        pass
                logger.warning(
                    "GitHub: rate limited (403) at %s/%s remaining%s. "
                    "Set GITHUB_TOKEN env var for higher limits.",
                    self._rate_remaining, self._rate_limit, reset_str,
                )
                self._rate_limited = True
                return None

            if resp.status_code == 422:
                logger.warning("GitHub: query rejected (422) — may be too complex: %s", query[:80])
                return []

            if resp.status_code == 401:
                logger.warning(
                    "GitHub: bad credentials (401). "
                    "Check GITHUB_TOKEN or unset it for anonymous access."
                )
                return []

            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            total = data.get("total_count", 0)

            if items:
                logger.info(
                    "  GitHub search '%s': %d results (total=%d, per_page=%d)",
                    query[:60], len(items), total, per_page,
                )

            return items

        except requests.exceptions.Timeout:
            logger.warning("GitHub: search timeout for query: %s", query[:60])
            return []
        except Exception as e:
            logger.warning("GitHub: search failed: %s", e)
            return []

    def _repo_to_candidate(self, repo: dict) -> Optional[dict]:
        """将 GitHub 仓库转为统一 candidate。"""
        name = repo.get("full_name", "")
        description = (repo.get("description") or "").strip()
        title = f"{name}: {description}" if description else name

        if not name:
            return None

        # 过滤垃圾仓库
        if self._is_spam_repo(repo):
            return None

        # 时间解析
        pub_time = None
        pub_source = "missing"
        created_at = repo.get("created_at", "")
        if created_at:
            try:
                pub_time = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
                pub_time = pub_time.replace(tzinfo=timezone.utc)
                pub_source = "api"
            except ValueError:
                pass

        stars = repo.get("stargazers_count", 0) or 0
        forks = repo.get("forks_count", 0) or 0
        topics = repo.get("topics", []) or []

        # 社区热度：stars 权重高但取 log 抑制超级项目
        community_hotness = log1p(stars) * 3 + log1p(forks) * 1.5

        candidate = self.make_candidate(
            id_=f"github-{repo['id']}",
            title=title[:200],
            url=repo.get("html_url", ""),
            source="GitHub",
            source_type="github",
            published_at=pub_time,
            published_source=pub_source,
            summary=description[:300],
            author=repo.get("owner", {}).get("login", ""),
            tags=topics,
            metrics={
                "github_stars": stars,
                "github_stars_recent": stars,
            },
        )
        candidate["scores"]["community"] = round(community_hotness, 1)
        candidate["_gh_hotness"] = round(community_hotness, 1)
        return candidate

    @staticmethod
    def _is_spam_repo(repo: dict) -> bool:
        """检测垃圾/教程/镜像仓库。"""
        name = (repo.get("full_name", "") or "").lower()
        desc = (repo.get("description", "") or "").lower()
        stars = repo.get("stargazers_count", 0) or 0

        # 低 star 直接过滤
        if stars < MIN_STARS:
            return True

        # 明显的垃圾/教程/收藏列表
        spam_patterns = [
            "awesome-", "awesome_", "-list", "_list",
            "tutorial", "course", "learn-", "learning-",
            "interview", "cheatsheet", "cheat-sheet",
            "notes", "roadmap", "guide-", "handbook",
            "best-practice", "coding-interview",
            "mirror", "backup", "archive",
            "resources", "collection",
        ]
        for pattern in spam_patterns:
            if pattern in name or pattern in desc:
                return True

        return False
