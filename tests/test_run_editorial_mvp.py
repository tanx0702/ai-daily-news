import unittest
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
