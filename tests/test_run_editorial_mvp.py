import unittest
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch


class EditorialMvpRunnerTests(unittest.TestCase):
    def test_save_shadow_result_persists_a_full_report(self):
        from scripts.run_editorial_mvp import save_shadow_result
        from src.domain.models import EditorialPlan, WorkflowResult
        from src.domain.states import WorkflowState

        result = WorkflowResult(
            state=WorkflowState.COMPLETED,
            state_history=(WorkflowState.CREATED, WorkflowState.COMPLETED),
            candidates=(),
            analyses=(),
            editorial_plan=EditorialPlan(decisions=(), selection_report={}),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            report, path = save_shadow_result(
                result,
                history_dir=Path(temp_dir),
                run_id="shadow-runner-1",
                generated_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            )

            self.assertTrue(path.is_file())
            self.assertEqual(report["run_id"], "shadow-runner-1")
            self.assertEqual(report["workflow"]["state"], "completed")

    def test_script_can_be_executed_directly_from_the_project_root(self):
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, "scripts/run_editorial_mvp.py", "--help"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Run the v2 editorial MVP", completed.stdout)

    def test_report_marks_output_as_shadow_only(self):
        from scripts.run_editorial_mvp import report_from_result
        from src.domain.models import EditorialPlan, WorkflowResult
        from src.domain.states import WorkflowState

        report = report_from_result(
            WorkflowResult(
                state=WorkflowState.COMPLETED,
                state_history=(WorkflowState.CREATED, WorkflowState.COMPLETED),
                candidates=(),
                analyses=(),
                editorial_plan=EditorialPlan(
                    decisions=(),
                    selection_report={"selected_count": 0},
                ),
            )
        )

        self.assertTrue(report["shadow_only"])
        self.assertFalse(report["publishing_enabled"])
        self.assertEqual(report["state"], "completed")
        self.assertEqual(report["selection_report"]["selected_count"], 0)

    def test_cli_loads_a_production_snapshot_and_runs_existing_candidates(self):
        from scripts import run_editorial_mvp
        from src.domain.models import CollectionDiagnostics, EditorialPlan, WorkflowResult
        from src.domain.states import WorkflowState
        from src.services.production_snapshot import save_production_snapshot

        result = WorkflowResult(
            state=WorkflowState.COMPLETED,
            state_history=(WorkflowState.CREATED, WorkflowState.COMPLETED),
            candidates=(),
            analyses=(),
            editorial_plan=EditorialPlan(decisions=(), selection_report={}),
        )
        workflow = Mock()
        workflow.run_existing.return_value = result
        items = [
            {
                "id": "production-1",
                "title": "Production candidate",
                "published_at": datetime(2026, 8, 3, tzinfo=timezone.utc),
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            snapshot = save_production_snapshot(
                items,
                date_str="2026-08-03",
                snapshot_dir=temp_path,
                collection_diagnostics=CollectionDiagnostics(
                    fetched_total=8,
                    returned_candidate_count=1,
                ),
            )
            history_dir = temp_path / "history"
            with patch("scripts.run_editorial_mvp.DailyEditionWorkflow", return_value=workflow):
                exit_code = run_editorial_mvp.main(
                    [
                        "--snapshot",
                        str(snapshot),
                        "--history-dir",
                        str(history_dir),
                        "--top-n",
                        "1",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(list(history_dir.glob("shadow-*.json")))

        workflow.run_existing.assert_called_once()
        call = workflow.run_existing.call_args
        self.assertEqual(call.args[0][0]["id"], "production-1")
        self.assertEqual(call.kwargs["top_n"], 1)
        self.assertEqual(call.kwargs["collection_diagnostics"].fetched_total, 8)

    def test_cli_missing_snapshot_fails_without_writing_a_shadow_report(self):
        from scripts import run_editorial_mvp

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            history_dir = temp_path / "history"
            with patch("scripts.run_editorial_mvp.DailyEditionWorkflow") as workflow:
                exit_code = run_editorial_mvp.main(
                    [
                        "--snapshot",
                        str(temp_path / "missing.json"),
                        "--history-dir",
                        str(history_dir),
                    ]
                )

            self.assertEqual(exit_code, 1)
            workflow.assert_not_called()
            self.assertEqual(list(history_dir.glob("*.json")) if history_dir.exists() else [], [])


if __name__ == "__main__":
    unittest.main()
