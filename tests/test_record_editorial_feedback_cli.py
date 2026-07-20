import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.services.shadow_history import save_shadow_report


class RecordEditorialFeedbackCliTests(unittest.TestCase):
    def test_cli_records_a_valid_feedback_label(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            history_dir = Path(temp_dir)
            save_shadow_report(
                {
                    "run_id": "shadow-cli-1",
                    "editorial": {"decisions": [{"candidate_id": "candidate-cli"}]},
                },
                history_dir=history_dir,
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/record_editorial_feedback.py",
                    "--history-dir",
                    str(history_dir),
                    "--run-id",
                    "shadow-cli-1",
                    "--candidate-id",
                    "candidate-cli",
                    "--label",
                    "duplicate",
                    "--note",
                    "Same event as yesterday.",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertEqual(output["event"]["label"], "duplicate")
            self.assertTrue(Path(output["feedback_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
