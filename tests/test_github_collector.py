import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from src.collectors.github import GitHubCollector


def _repo(repo_id, name, *, created_at, pushed_at, stars=30, description="AI tooling"):
    return {
        "id": repo_id,
        "full_name": name,
        "description": description,
        "html_url": f"https://github.com/{name}",
        "created_at": created_at,
        "pushed_at": pushed_at,
        "stargazers_count": stars,
        "forks_count": 3,
        "topics": ["llm"],
        "owner": {"login": name.split("/")[0]},
    }


class GitHubCollectorTests(unittest.TestCase):
    def test_fetch_includes_recently_pushed_existing_repository_once(self):
        collector = GitHubCollector(token="test-token")
        existing_active = _repo(
            1,
            "acme/active-agent",
            created_at="2025-01-01T00:00:00Z",
            pushed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        queries = []

        def search(query, per_page=15, **kwargs):
            queries.append(query)
            return [existing_active]

        with patch.object(collector, "_check_rate_limit", return_value={"remaining": 30, "limit": 30}), \
             patch.object(collector, "_search_repos", side_effect=search), \
             patch("src.collectors.github.time.sleep"):
            candidates = collector.fetch()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["metrics"]["github_activity_type"], "recent_push")
        self.assertEqual(candidates[0]["published_at"], datetime.now(timezone.utc).replace(microsecond=0))
        self.assertTrue(any("pushed:>=" in query for query in queries))
        self.assertTrue(any("created:>=" in query for query in queries))

    def test_fetch_uses_keyword_fallback_when_topic_queries_find_nothing(self):
        collector = GitHubCollector(token="test-token")
        active_repo = _repo(
            2,
            "acme/ai-agent",
            created_at="2025-01-01T00:00:00Z",
            pushed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        queries = []

        def search(query, per_page=15, **kwargs):
            queries.append(query)
            return [active_repo] if "in:name,description" in query else []

        with patch.object(collector, "_check_rate_limit", return_value={"remaining": 30, "limit": 30}), \
             patch.object(collector, "_search_repos", side_effect=search), \
             patch("src.collectors.github.time.sleep"):
            candidates = collector.fetch()

        self.assertEqual(len(candidates), 1)
        self.assertTrue(any("in:name,description" in query for query in queries))


if __name__ == "__main__":
    unittest.main()
