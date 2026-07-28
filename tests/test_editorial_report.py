import unittest
from datetime import datetime, timezone


def _candidate(candidate_id):
    from src.domain.models import NewsCandidate, SourceEvidence

    return NewsCandidate(
        candidate_id=candidate_id,
        evidence=SourceEvidence(
            title=f"{candidate_id} title",
            summary="Source evidence summary.",
            url=f"https://example.com/{candidate_id}",
            source="Example",
            source_type="rss",
            published_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        ),
        source_tier="media",
        legacy_payload={},
    )


def _analysis(candidate_id, *, importance, evidence, risk):
    from src.domain.models import NewsAnalysis

    return NewsAnalysis(
        candidate_id=candidate_id,
        importance_score=importance,
        evidence_score=evidence,
        impact_score=importance,
        risk_level=risk,
        importance_reason=f"importance-{candidate_id}",
        verifiability_reason=f"evidence-{candidate_id}",
        impact_analysis=f"impact-{candidate_id}",
    )


class EditorialReportTests(unittest.TestCase):
    def test_report_contains_collection_analysis_and_reject_metrics(self):
        from src.domain.models import (
            CollectionDiagnostics,
            EditorialAction,
            EditorialBrief,
            EditorialDecision,
            EditorialPlan,
            WorkflowResult,
        )
        from src.domain.states import WorkflowState
        from src.services.editorial_report import build_editorial_report

        first = _candidate("first")
        second = _candidate("second")
        result = WorkflowResult(
            state=WorkflowState.COMPLETED,
            state_history=(WorkflowState.CREATED, WorkflowState.COMPLETED),
            candidates=(first, second),
            analyses=(
                _analysis("first", importance=9.0, evidence=8.5, risk="low"),
                _analysis("second", importance=5.0, evidence=4.0, risk="high"),
            ),
            editorial_plan=EditorialPlan(
                decisions=(
                    EditorialDecision(
                        candidate_id="first",
                        action=EditorialAction.WRITE,
                        rank=1,
                        brief=EditorialBrief("reader", "angle", "title"),
                        reason="high value",
                    ),
                    EditorialDecision(
                        candidate_id="second",
                        action=EditorialAction.REJECT,
                        rank=None,
                        brief=EditorialBrief("reader", "angle", "title"),
                        reason="high risk evidence gap",
                    ),
                ),
                selection_report={"selected_count": 1},
            ),
            collection_diagnostics=CollectionDiagnostics(
                fetched_total=12,
                source_merge_removed=2,
                topic_cluster_removed=1,
                final_editorial_dedup_removed=1,
                returned_candidate_count=2,
            ),
        )

        report = build_editorial_report(
            result,
            run_id="shadow-001",
            generated_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )

        self.assertEqual(report["run_id"], "shadow-001")
        self.assertEqual(report["collection"]["fetched_total"], 12)
        self.assertEqual(report["collection"]["dedup_removed_total"], 4)
        self.assertEqual(report["candidates"][0]["source_url"], "https://example.com/first")
        self.assertEqual(report["candidates"][0]["source_summary"], "Source evidence summary.")
        self.assertEqual(report["candidates"][0]["content_quality"], "ready")
        self.assertEqual(report["candidates"][0]["content_quality_reason"], "")
        self.assertEqual(report["candidates"][0]["evidence_details"], {})
        self.assertEqual(report["analysis"]["importance_score_distribution"]["8.5-10"], 1)
        self.assertEqual(report["analysis"]["importance_score_distribution"]["4-6.9"], 1)
        self.assertEqual(report["analysis"]["risk_level_distribution"]["high"], 1)
        self.assertEqual(report["editorial"]["write_count"], 1)
        self.assertEqual(report["editorial"]["reject_count"], 1)
        self.assertEqual(
            report["editorial"]["reject_reasons"],
            [{"reason": "high risk evidence gap", "count": 1}],
        )
        self.assertEqual(report["editorial"]["decisions"][1]["candidate_id"], "second")


if __name__ == "__main__":
    unittest.main()
