import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from src.briefing.evidence import source_evidence_from_candidate
from src.briefing.publishability import validate_source_publishability
from src.collectors.github import GitHubCollector
from src.evidence import preserve_source_evidence


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
    def test_recent_push_does_not_create_a_daily_news_candidate(self):
        collector = GitHubCollector(token="test-token")
        recent_push = _repo(
            1,
            "acme/active-agent",
            created_at="2025-01-01T00:00:00Z",
            pushed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        candidate = collector._repo_to_candidate(recent_push)

        self.assertIsNone(candidate)

    def test_recent_release_candidate_requires_project_and_change_evidence(self):
        collector = GitHubCollector(token="test-token")
        repo = _repo(
            2,
            "acme/release-agent",
            created_at="2025-01-01T00:00:00Z",
            pushed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        release = {
            "tag_name": "v1.4.0",
            "html_url": "https://github.com/acme/release-agent/releases/tag/v1.4.0",
            "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": "Adds repository rule packs and a TypeScript integration for coding agents.",
            "draft": False,
            "prerelease": False,
        }

        candidate = collector._release_to_candidate(
            repo,
            release,
            project_description="A tool that applies repository rules to coding agents.",
        )

        self.assertEqual(candidate["metrics"]["github_activity_type"], "github_release")
        self.assertEqual(candidate["metrics"]["github_release_tag"], "v1.4.0")
        self.assertIn("project_description", candidate["github_evidence"])
        self.assertIn("release_notes", candidate["github_evidence"])

    def test_recent_release_candidate_is_source_publishable(self):
        collector = GitHubCollector(token="test-token")
        repo = _repo(
            22,
            "acme/coding-agent",
            created_at="2025-01-01T00:00:00Z",
            pushed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        release = {
            "tag_name": "v1.4.0",
            "html_url": "https://github.com/acme/coding-agent/releases/tag/v1.4.0",
            "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": "Adds repository rule packs and a TypeScript integration for coding agents.",
            "draft": False,
            "prerelease": False,
        }
        candidate = collector._release_to_candidate(
            repo,
            release,
            project_description="A tool that applies repository rules to coding agents.",
        )

        preserve_source_evidence(candidate)
        source = source_evidence_from_candidate(candidate)
        result = validate_source_publishability(source)

        self.assertTrue(result.accepted, result.reason_codes)

    def test_fetch_keeps_recent_push_out_of_daily_candidates(self):
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
             patch.object(collector, "_fetch_latest_release", return_value=None), \
             patch("src.collectors.github.time.sleep"):
            candidates = collector.fetch()

        self.assertEqual(candidates, [])
        self.assertTrue(any("pushed:>=" in query for query in queries))
        self.assertTrue(any("created:>=" in query for query in queries))

    def test_fetch_uses_keyword_fallback_without_promoting_recent_pushes(self):
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
             patch.object(collector, "_fetch_latest_release", return_value=None), \
             patch("src.collectors.github.time.sleep"):
            candidates = collector.fetch()

        self.assertEqual(candidates, [])
        self.assertTrue(any("in:name,description" in query for query in queries))

    def test_fetch_enriches_a_recent_repository_with_release_and_readme_evidence(self):
        collector = GitHubCollector(token="test-token")
        repo = _repo(
            3,
            "acme/release-agent",
            created_at="2025-01-01T00:00:00Z",
            pushed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        release = {
            "tag_name": "v1.4.0",
            "html_url": "https://github.com/acme/release-agent/releases/tag/v1.4.0",
            "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": "Adds repository rule packs and a TypeScript integration for coding agents.",
            "draft": False,
            "prerelease": False,
        }

        with patch.object(collector, "_check_rate_limit", return_value={"remaining": 30, "limit": 30}), \
             patch.object(collector, "_search_repos", return_value=[repo]), \
             patch.object(collector, "_fetch_latest_release", return_value=release), \
             patch.object(
                 collector,
                 "_fetch_readme_excerpt",
                 return_value="A coding-agent tool that applies repository rules across a project.",
             ), \
             patch("src.collectors.github.time.sleep"):
            candidates = collector.fetch()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["metrics"]["github_activity_type"], "github_release")
        self.assertEqual(
            candidates[0]["github_evidence"]["project_description"],
            "A coding-agent tool that applies repository rules across a project.",
        )

    def test_fetch_uses_keyword_fallback_when_topic_repositories_lack_releases(self):
        collector = GitHubCollector()
        active_repo = _repo(
            4,
            "acme/active-agent",
            created_at="2025-01-01T00:00:00Z",
            pushed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        release_repo = _repo(
            5,
            "acme/release-agent",
            created_at="2025-01-01T00:00:00Z",
            pushed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        release = {
            "tag_name": "v1.4.0",
            "html_url": "https://github.com/acme/release-agent/releases/tag/v1.4.0",
            "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": "Adds repository rule packs and a TypeScript integration for coding agents.",
            "draft": False,
            "prerelease": False,
        }

        def search(query, per_page=15, **kwargs):
            return [release_repo] if "in:name,description" in query else [active_repo]

        def fetch_release(repo):
            return release if repo["id"] == release_repo["id"] else None

        with patch.object(collector, "_check_rate_limit", return_value={"remaining": 10, "limit": 10}), \
             patch.object(collector, "_search_repos", side_effect=search), \
             patch.object(collector, "_fetch_latest_release", side_effect=fetch_release), \
             patch.object(
                 collector,
                 "_fetch_readme_excerpt",
                 return_value="A coding-agent tool that applies repository rules across a project.",
             ), \
             patch("src.collectors.github.time.sleep"):
            candidates = collector.fetch()

        self.assertEqual([item["title"] for item in candidates], ["acme/release-agent releases v1.4.0"])


if __name__ == "__main__":
    unittest.main()
