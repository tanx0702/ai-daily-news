import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


class EditorialFeedbackReviewCliTests(unittest.TestCase):
    def test_cli_writes_retro_report_without_calling_production_pipeline(self):
        root = Path(__file__).resolve().parents[1]
        now = datetime.now(timezone.utc)
        report = {
            "schema_version": "shadow-run-v1",
            "run_id": "shadow-cli-1",
            "generated_at": now.isoformat(),
            "workflow": {"state": "completed", "error": ""},
            "candidates": [
                {
                    "candidate_id": "candidate-1",
                    "source": "Example Source",
                    "source_title": "Candidate title",
                }
            ],
            "analysis": {
                "items": [
                    {
                        "candidate_id": "candidate-1",
                        "importance_score": 9.0,
                        "risk_level": "low",
                    }
                ]
            },
            "editorial": {
                "decisions": [
                    {
                        "candidate_id": "candidate-1",
                        "action": "write",
                        "reason": "high value",
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            history_dir = Path(temp_dir) / "history"
            output_dir = Path(temp_dir) / "output"
            history_dir.mkdir()
            (history_dir / "shadow-cli-1.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/review_editorial_feedback.py",
                    "--history-dir",
                    str(history_dir),
                    "--output-dir",
                    str(output_dir),
                    "--days",
                    "7",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["report"]["coverage"]["run_count"], 1)
            self.assertTrue(Path(payload["json_path"]).is_file())
            self.assertTrue(Path(payload["markdown_path"]).is_file())

    def test_cli_rejects_non_positive_day_window_before_processing_history(self):
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/review_editorial_feedback.py",
                "--days",
                "0",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("--days must be a positive integer", completed.stderr)


if __name__ == "__main__":
    unittest.main()
