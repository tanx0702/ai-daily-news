import unittest
from datetime import datetime, timezone


class CollectorAgentTests(unittest.TestCase):
    def test_collect_with_diagnostics_preserves_v1_collection_metrics(self):
        from src.agents.collector_agent import CollectorAgent

        item = {
            "id": "rss-2",
            "title": "Evidence-backed update",
            "summary": "Source summary.",
            "url": "https://example.com/second",
            "source": "Example Feed",
            "source_type": "rss",
            "published_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
        }

        def collect_news(*, top_n, rss_timeout, diagnostics):
            diagnostics.update(
                fetched_total=9,
                source_merge_removed=2,
                filtered_total=6,
                topic_cluster_removed=1,
                final_editorial_dedup_removed=1,
                returned_candidate_count=1,
            )
            return [item]

        agent = CollectorAgent(
            collect_news=collect_news,
            annotate_candidates=lambda items: items,
        )

        candidates, metrics = agent.collect_with_diagnostics()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(metrics.fetched_total, 9)
        self.assertEqual(metrics.dedup_removed_total, 4)
        self.assertEqual(metrics.returned_candidate_count, 1)

    def test_collect_adapts_v1_candidates_without_mutating_the_source_payload(self):
        from src.agents.collector_agent import CollectorAgent

        legacy_item = {
            "id": "rss-1",
            "title": "A source-backed AI update",
            "summary": "Original source summary.",
            "url": "https://example.com/news",
            "source": "Example Feed",
            "source_type": "rss",
            "published_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
            "source_tier": "primary",
        }
        calls = {}

        def collect_news(*, top_n, rss_timeout):
            calls["collect"] = (top_n, rss_timeout)
            return [legacy_item]

        def annotate(items):
            calls["annotated"] = len(items)
            items[0]["_editorial"] = {"score": 9.1}
            return items

        agent = CollectorAgent(
            collect_news=collect_news,
            annotate_candidates=annotate,
        )

        candidates = agent.collect(top_n=12, rss_timeout=9)

        self.assertEqual(calls["collect"], (12, 9))
        self.assertEqual(calls["annotated"], 1)
        self.assertEqual(candidates[0].candidate_id, "rss-1")
        self.assertEqual(candidates[0].evidence.source, "Example Feed")
        self.assertEqual(candidates[0].source_tier, "primary")
        self.assertEqual(candidates[0].legacy_payload["_editorial"]["score"], 9.1)
        with self.assertRaises(TypeError):
            candidates[0].legacy_payload["title"] = "changed"

    def test_collect_uses_normalized_shadow_evidence_without_changing_v1_item(self):
        from src.agents.collector_agent import CollectorAgent
        from src.evidence import NormalizedEvidence

        legacy_item = {
            "id": "hn-1",
            "title": "HN title",
            "summary": "Article URL: https://example.test/story",
            "url": "https://news.ycombinator.com/item?id=1",
            "source": "Hacker News AI",
            "source_type": "rss",
            "published_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
        }
        normalized = NormalizedEvidence(
            title="HN title",
            summary="",
            url="https://example.test/story",
            content_quality="metadata_only",
            content_quality_reason="HN RSS only contains metadata.",
            details={"article_url": "https://example.test/story"},
        )
        seen_items = []

        def normalize(item):
            seen_items.append(item)
            return normalized

        agent = CollectorAgent(
            collect_news=lambda **_: [legacy_item],
            annotate_candidates=lambda items: items,
            normalize_evidence=normalize,
        )

        candidate = agent.collect()[0]

        self.assertEqual(seen_items, [legacy_item])
        self.assertEqual(candidate.evidence.summary, "")
        self.assertEqual(candidate.evidence.url, "https://example.test/story")
        self.assertEqual(candidate.evidence.content_quality, "metadata_only")
        self.assertEqual(candidate.evidence.details["article_url"], "https://example.test/story")
        self.assertEqual(legacy_item["summary"], "Article URL: https://example.test/story")

    def test_collect_returns_an_empty_tuple_when_v1_has_no_candidates(self):
        from src.agents.collector_agent import CollectorAgent

        agent = CollectorAgent(
            collect_news=lambda **_: [],
            annotate_candidates=lambda items: items,
        )

        self.assertEqual(agent.collect(), ())


if __name__ == "__main__":
    unittest.main()
