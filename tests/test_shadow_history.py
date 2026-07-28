import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


class ShadowHistoryTests(unittest.TestCase):
    def test_save_report_and_append_valid_feedback_event(self):
        from src.services.shadow_history import record_feedback, save_shadow_report

        report = {
            "run_id": "shadow-001",
            "editorial": {
                "decisions": [
                    {"candidate_id": "candidate-1", "action": "write"},
                ]
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            history_dir = Path(temp_dir)
            report_path = save_shadow_report(report, history_dir=history_dir)
            event, feedback_path = record_feedback(
                history_dir=history_dir,
                run_id="shadow-001",
                candidate_id="candidate-1",
                label="good_topic",
                note="Keep this topic family.",
                recorded_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            )

            self.assertTrue(report_path.is_file())
            self.assertEqual(event["label"], "good_topic")
            self.assertEqual(event["candidate_id"], "candidate-1")
            feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
            self.assertEqual(feedback["events"], [event])

    def test_record_feedback_rejects_unknown_label_or_candidate(self):
        from src.services.shadow_history import record_feedback, save_shadow_report

        report = {
            "run_id": "shadow-002",
            "editorial": {"decisions": [{"candidate_id": "candidate-2"}]},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            history_dir = Path(temp_dir)
            save_shadow_report(report, history_dir=history_dir)

            with self.assertRaises(ValueError):
                record_feedback(
                    history_dir=history_dir,
                    run_id="shadow-002",
                    candidate_id="candidate-2",
                    label="other",
                )
            with self.assertRaises(ValueError):
                record_feedback(
                    history_dir=history_dir,
                    run_id="shadow-002",
                    candidate_id="unknown",
                    label="duplicate",
                )

    def test_record_feedback_rejects_note_longer_than_1000_characters(self):
        from src.services.shadow_history import record_feedback, save_shadow_report

        report = {
            "run_id": "shadow-003",
            "editorial": {"decisions": [{"candidate_id": "candidate-3"}]},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            history_dir = Path(temp_dir)
            save_shadow_report(report, history_dir=history_dir)

            with self.assertRaisesRegex(ValueError, "1000"):
                record_feedback(
                    history_dir=history_dir,
                    run_id="shadow-003",
                    candidate_id="candidate-3",
                    label="good_topic",
                    note="x" * 1001,
                )


if __name__ == "__main__":
    unittest.main()
