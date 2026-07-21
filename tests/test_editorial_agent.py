import unittest
from datetime import datetime, timezone


def _candidate(candidate_id, *, tier, score):
    from src.domain.models import NewsCandidate, SourceEvidence

    return NewsCandidate(
        candidate_id=candidate_id,
        evidence=SourceEvidence(
            title=f"{candidate_id} title",
            summary="A complete source summary with enough evidence.",
            url=f"https://example.com/{candidate_id}",
            source=f"Source {candidate_id}",
            source_type="rss",
            published_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        ),
        source_tier=tier,
        legacy_payload={
            "id": candidate_id,
            "title": f"{candidate_id} title",
            "source": f"Source {candidate_id}",
            "source_tier": tier,
            "topic_key": candidate_id,
            "_editorial": {
                "score": score,
                "event_key": f"event:{candidate_id}",
                "event_type": "news",
            },
        },
    )


def _analysis(candidate_id, risk_level="low"):
    from src.domain.models import NewsAnalysis

    return NewsAnalysis(
        candidate_id=candidate_id,
        importance_score=9.0,
        evidence_score=9.0,
        impact_score=9.0,
        risk_level=risk_level,
        importance_reason="important",
        verifiability_reason="verified",
        impact_analysis="high impact",
    )


class EditorialAgentTests(unittest.TestCase):
    def test_select_assigns_write_reserve_and_reject_decisions(self):
        from src.agents.editorial_agent import EditorialAction, EditorialAgent

        first = _candidate("first", tier="primary", score=9.4)
        second = _candidate("second", tier="media", score=8.8)
        risky = _candidate("risky", tier="community", score=9.8)

        plan = EditorialAgent().select(
            (first, second, risky),
            (_analysis("first"), _analysis("second"), _analysis("risky", "high")),
            target_count=1,
            min_primary_or_research=0,
        )
        by_candidate = {decision.candidate_id: decision for decision in plan.decisions}

        self.assertEqual(by_candidate["first"].action, EditorialAction.WRITE)
        self.assertEqual(by_candidate["second"].action, EditorialAction.RESERVE)
        self.assertEqual(by_candidate["risky"].action, EditorialAction.REJECT)
        self.assertEqual(by_candidate["first"].rank, 1)
        self.assertTrue(by_candidate["first"].brief.audience)
        self.assertTrue(by_candidate["first"].brief.angle)
        self.assertTrue(by_candidate["first"].brief.title_direction)


if __name__ == "__main__":
    unittest.main()
