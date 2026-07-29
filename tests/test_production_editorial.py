import unittest
from datetime import datetime, timezone


def _item(candidate_id, *, score=9.0, content_quality="ready"):
    return {
        "id": candidate_id,
        "title": f"{candidate_id} title",
        "summary": "A sufficiently detailed source summary.",
        "url": f"https://example.test/{candidate_id}",
        "source": f"Source {candidate_id}",
        "source_type": "rss",
        "source_tier": "primary",
        "published_at": datetime(2026, 7, 28, tzinfo=timezone.utc),
        "quality_state": content_quality,
        "_editorial": {
            "score": score,
            "evidence_complete": True,
            "event_key": f"event:{candidate_id}",
            "event_type": "news",
        },
    }


class _Adapter:
    def __init__(self, candidates):
        self.candidates = candidates
        self.seen = None

    def adapt_existing(self, items):
        self.seen = items
        return self.candidates


class _Analyst:
    def __init__(self, analyses, error=None):
        self.analyses = analyses
        self.error = error

    def analyze(self, candidates):
        if self.error:
            raise self.error
        return self.analyses


class _Editorial:
    def __init__(self, decisions):
        self.decisions = decisions

    def select(self, *args, **kwargs):
        from src.domain.models import EditorialPlan

        return EditorialPlan(decisions=tuple(self.decisions), selection_report={"selector": "fake"})


def _candidate(item):
    from src.domain.models import NewsCandidate, SourceEvidence

    return NewsCandidate(
        candidate_id=item["id"],
        evidence=SourceEvidence(
            title=item["title"],
            summary=item["summary"],
            url=item["url"],
            source=item["source"],
            source_type=item["source_type"],
            published_at=item["published_at"],
            content_quality=item["quality_state"],
        ),
        source_tier=item["source_tier"],
        legacy_payload=item,
    )


def _analysis(candidate_id, *, risk="low"):
    from src.domain.models import NewsAnalysis

    return NewsAnalysis(
        candidate_id=candidate_id,
        importance_score=9.0,
        evidence_score=9.0,
        impact_score=9.0,
        risk_level=risk,
        importance_reason="important",
        verifiability_reason="verified",
        impact_analysis="high impact",
    )


def _decision(candidate_id, action, rank=None):
    from src.domain.models import EditorialAction, EditorialBrief, EditorialDecision

    return EditorialDecision(
        candidate_id=candidate_id,
        action=EditorialAction(action),
        rank=rank,
        brief=EditorialBrief(audience="reader", angle="angle", title_direction="title"),
        reason=action,
    )


class ProductionEditorialTests(unittest.TestCase):
    def test_v1_returns_original_selection_objects_and_does_not_adapt(self):
        from src.workflows.production_editorial import run_production_editorial

        selected, reserve = _item("v1-selected"), _item("v1-reserve")

        result = run_production_editorial(
            mode="v1",
            all_candidates=[selected, reserve],
            v1_selected=[selected],
            v1_reserves=[reserve],
            target_count=1,
            collector=object(),
        )

        self.assertIs(result.selected[0], selected)
        self.assertIs(result.reserves[0], reserve)
        self.assertEqual(result.report["status"], "v1")

    def test_v2_uses_ready_write_candidates_in_editorial_rank_order(self):
        from src.workflows.production_editorial import run_production_editorial

        first, second, metadata = _item("first"), _item("second"), _item("metadata", content_quality="metadata_only")
        adapter = _Adapter(tuple(_candidate(item) for item in (first, second, metadata)))
        result = run_production_editorial(
            mode="v2_assist",
            all_candidates=[first, second, metadata],
            v1_selected=[first, metadata],
            v1_reserves=[second],
            target_count=2,
            collector=adapter,
            analyst=_Analyst((_analysis("first"), _analysis("second"), _analysis("metadata", risk="high"))),
            editorial=_Editorial((_decision("second", "write", 1), _decision("metadata", "write", 2), _decision("first", "write", 3))),
        )

        self.assertEqual([item["id"] for item in result.selected], ["second", "first"])
        self.assertEqual(result.reserves, [])
        self.assertIs(adapter.seen[0], first)
        self.assertEqual(result.report["status"], "applied")
        self.assertEqual(result.report["added_v2_selected_ids"], ["second"])
        self.assertEqual(result.report["dropped_v1_selected_ids"], ["metadata"])
        self.assertEqual(result.report["analysis"]["risk_level_distribution"], {"high": 1, "low": 2})

    def test_v2_falls_back_to_v1_when_ready_write_candidates_are_insufficient(self):
        from src.workflows.production_editorial import run_production_editorial

        first, second = _item("first"), _item("second")
        result = run_production_editorial(
            mode="v2_assist",
            all_candidates=[first, second],
            v1_selected=[first, second],
            v1_reserves=[],
            target_count=2,
            collector=_Adapter((_candidate(first), _candidate(second))),
            analyst=_Analyst((_analysis("first"), _analysis("second"))),
            editorial=_Editorial((_decision("first", "write", 1), _decision("second", "reserve"))),
        )

        self.assertEqual(result.selected, [first, second])
        self.assertEqual(result.report["status"], "fallback")
        self.assertEqual(result.report["fallback_reason"], "insufficient_ready_write_candidates")

    def test_v2_falls_back_to_v1_when_an_agent_raises(self):
        from src.workflows.production_editorial import run_production_editorial

        first = _item("first")
        result = run_production_editorial(
            mode="v2_assist",
            all_candidates=[first],
            v1_selected=[first],
            v1_reserves=[],
            target_count=1,
            collector=_Adapter((_candidate(first),)),
            analyst=_Analyst((), error=RuntimeError("analyst unavailable")),
        )

        self.assertEqual(result.selected, [first])
        self.assertEqual(result.report["status"], "fallback")
        self.assertEqual(result.report["fallback_reason"], "agent_error:RuntimeError")


if __name__ == "__main__":
    unittest.main()
