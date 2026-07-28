import unittest
from datetime import datetime, timezone


def _candidate(
    candidate_id,
    *,
    tier,
    summary,
    payload,
    content_quality="ready",
    content_quality_reason="",
):
    from src.domain.models import NewsCandidate, SourceEvidence

    return NewsCandidate(
        candidate_id=candidate_id,
        evidence=SourceEvidence(
            title=f"{candidate_id} title",
            summary=summary,
            url=f"https://example.com/{candidate_id}",
            source="Example",
            source_type=payload.get("source_type", "rss"),
            published_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            content_quality=content_quality,
            content_quality_reason=content_quality_reason,
        ),
        source_tier=tier,
        legacy_payload=payload,
    )


class NewsAnalystAgentTests(unittest.TestCase):
    def test_analyze_rates_complete_primary_evidence_as_important_and_low_risk(self):
        from src.agents.news_analyst_agent import NewsAnalystAgent

        candidate = _candidate(
            "primary-1",
            tier="primary",
            summary="A complete official source summary with enough supporting detail.",
            payload={
                "_editorial": {"score": 9.2, "evidence_complete": True},
                "metrics": {"cross_source_count": 2},
            },
        )

        analysis = NewsAnalystAgent().analyze((candidate,))[0]

        self.assertEqual(analysis.candidate_id, "primary-1")
        self.assertGreaterEqual(analysis.importance_score, 8.0)
        self.assertGreaterEqual(analysis.evidence_score, 8.0)
        self.assertEqual(analysis.risk_level, "low")
        self.assertIn("高", analysis.impact_analysis)

    def test_analyze_marks_a_single_source_publish_risk_as_high_risk(self):
        from src.agents.news_analyst_agent import NewsAnalystAgent

        candidate = _candidate(
            "community-1",
            tier="community",
            summary="Short note.",
            payload={
                "source_type": "hn",
                "_editorial": {"score": 6.0, "evidence_complete": False},
                "metrics": {"cross_source_count": 0},
                "_publish_risk": {"category": "community_model_comparison"},
            },
        )

        analysis = NewsAnalystAgent().analyze((candidate,))[0]

        self.assertLess(analysis.evidence_score, 6.0)
        self.assertEqual(analysis.risk_level, "high")
        self.assertIn("需谨慎", analysis.impact_analysis)
    def test_analyze_marks_metadata_only_evidence_as_high_risk(self):
        from src.agents.news_analyst_agent import NewsAnalystAgent

        candidate = _candidate(
            "hn-rss-1",
            tier="community",
            summary="",
            content_quality="metadata_only",
            content_quality_reason="HN RSS only contains article and comments URLs.",
            payload={
                "source_type": "rss",
                "_editorial": {"score": 8.0, "evidence_complete": True},
                "metrics": {"cross_source_count": 1},
            },
        )

        analysis = NewsAnalystAgent().analyze((candidate,))[0]

        self.assertEqual(analysis.risk_level, "high")
        self.assertLessEqual(analysis.evidence_score, 2.0)
        self.assertIn("metadata_only", analysis.verifiability_reason)


if __name__ == "__main__":
    unittest.main()
