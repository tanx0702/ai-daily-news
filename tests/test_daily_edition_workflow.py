import unittest


class DailyEditionWorkflowTests(unittest.TestCase):
    def test_run_advances_through_the_shadow_editorial_states(self):
        from src.domain.models import CollectionDiagnostics, EditorialPlan
        from src.domain.states import WorkflowState
        from src.workflows.daily_edition import DailyEditionWorkflow

        calls = []

        class Collector:
            def collect_with_diagnostics(self, **kwargs):
                calls.append(("collect", kwargs))
                return (
                    ("candidate",),
                    CollectionDiagnostics(fetched_total=12, source_merge_removed=3),
                )

        class Analyst:
            def analyze(self, candidates):
                calls.append(("analyze", candidates))
                return ("analysis",)

        class Editorial:
            def select(self, candidates, analyses, **kwargs):
                calls.append(("select", candidates, analyses, kwargs))
                return EditorialPlan(decisions=(), selection_report={"selected_count": 0})

        result = DailyEditionWorkflow(
            collector=Collector(),
            analyst=Analyst(),
            editorial=Editorial(),
        ).run(top_n=6, rss_timeout=7)

        self.assertEqual(result.state, WorkflowState.COMPLETED)
        self.assertEqual(
            result.state_history,
            (
                WorkflowState.CREATED,
                WorkflowState.COLLECTED,
                WorkflowState.ANALYZED,
                WorkflowState.SELECTED,
                WorkflowState.COMPLETED,
            ),
        )
        self.assertEqual(calls[0], ("collect", {"top_n": 6, "rss_timeout": 7}))
        self.assertEqual(calls[1], ("analyze", ("candidate",)))
        self.assertEqual(result.editorial_plan.selection_report["selected_count"], 0)
        self.assertEqual(result.collection_diagnostics.fetched_total, 12)
        self.assertEqual(result.collection_diagnostics.dedup_removed_total, 3)

    def test_run_returns_failed_without_invoking_later_agents(self):
        from src.domain.states import WorkflowState
        from src.workflows.daily_edition import DailyEditionWorkflow

        class BrokenCollector:
            def collect_with_diagnostics(self, **kwargs):
                raise RuntimeError("source unavailable")

        class ShouldNotRun:
            def analyze(self, candidates):
                raise AssertionError("analyst should not run")

            def select(self, candidates, analyses, **kwargs):
                raise AssertionError("editorial should not run")

        result = DailyEditionWorkflow(
            collector=BrokenCollector(),
            analyst=ShouldNotRun(),
            editorial=ShouldNotRun(),
        ).run()

        self.assertEqual(result.state, WorkflowState.FAILED)
        self.assertEqual(result.state_history, (WorkflowState.CREATED, WorkflowState.FAILED))
        self.assertIn("source unavailable", result.error)

    def test_run_existing_adapts_supplied_candidates_without_collecting_again(self):
        from src.domain.models import CollectionDiagnostics, EditorialPlan
        from src.domain.states import WorkflowState
        from src.workflows.daily_edition import DailyEditionWorkflow

        supplied_items = [{"id": "production-1", "title": "Same production item"}]
        calls = []

        class Collector:
            def collect_with_diagnostics(self, **kwargs):
                raise AssertionError("run_existing must not collect news")

            def adapt_existing(self, items):
                calls.append(("adapt", items))
                return ("adapted-candidate",)

        class Analyst:
            def analyze(self, candidates):
                calls.append(("analyze", candidates))
                return ("analysis",)

        class Editorial:
            def select(self, candidates, analyses, **kwargs):
                calls.append(("select", candidates, analyses, kwargs))
                return EditorialPlan(decisions=(), selection_report={})

        diagnostics = CollectionDiagnostics(fetched_total=11, returned_candidate_count=1)
        result = DailyEditionWorkflow(
            collector=Collector(),
            analyst=Analyst(),
            editorial=Editorial(),
        ).run_existing(
            supplied_items,
            top_n=1,
            collection_diagnostics=diagnostics,
        )

        self.assertEqual(result.state, WorkflowState.COMPLETED)
        self.assertEqual(calls[0], ("adapt", supplied_items))
        self.assertEqual(calls[1], ("analyze", ("adapted-candidate",)))
        self.assertEqual(calls[2][0], "select")
        self.assertEqual(calls[2][3], {"target_count": 1})
        self.assertEqual(result.collection_diagnostics, diagnostics)


if __name__ == "__main__":
    unittest.main()
