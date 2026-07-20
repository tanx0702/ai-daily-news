import unittest
from datetime import datetime, timezone


class DomainModelTests(unittest.TestCase):
    def test_candidate_preserves_source_evidence_and_legacy_payload(self):
        from src.domain.models import NewsCandidate, SourceEvidence

        payload = {"title": "AI update", "source": "Example"}
        evidence = SourceEvidence(
            title="AI update",
            summary="An evidence-backed summary.",
            url="https://example.com/news",
            source="Example",
            source_type="rss",
            published_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )

        candidate = NewsCandidate(
            candidate_id="example-1",
            evidence=evidence,
            source_tier="media",
            legacy_payload=payload,
        )

        self.assertEqual(candidate.evidence.url, "https://example.com/news")
        self.assertEqual(candidate.legacy_payload["title"], "AI update")

    def test_state_machine_rejects_skipping_analysis(self):
        from src.domain.states import WorkflowState, transition_to

        self.assertEqual(
            transition_to(WorkflowState.CREATED, WorkflowState.COLLECTED),
            WorkflowState.COLLECTED,
        )
        with self.assertRaises(ValueError):
            transition_to(WorkflowState.COLLECTED, WorkflowState.SELECTED)


if __name__ == "__main__":
    unittest.main()
