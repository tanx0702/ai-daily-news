import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _shadow_report(
    run_id: str,
    generated_at: datetime,
    *,
    candidate_id: str,
    source: str,
    importance_score: float,
    risk_level: str,
    action: str,
    reason: str,
) -> dict:
    return {
        "schema_version": "shadow-run-v1",
        "run_id": run_id,
        "generated_at": generated_at.isoformat(),
        "workflow": {"state": "completed", "error": ""},
        "collection": {"fetched_total": 10, "dedup_removed_total": 2},
        "candidates": [
            {
                "candidate_id": candidate_id,
                "source_title": f"{candidate_id} title",
                "source_url": f"https://example.com/{candidate_id}",
                "source": source,
            }
        ],
        "analysis": {
            "items": [
                {
                    "candidate_id": candidate_id,
                    "importance_score": importance_score,
                    "evidence_score": 8.0,
                    "risk_level": risk_level,
                }
            ]
        },
        "editorial": {
            "decisions": [
                {
                    "candidate_id": candidate_id,
                    "action": action,
                    "reason": reason,
                }
            ],
            "reject_reasons": [{"reason": reason, "count": 1}] if action == "reject" else [],
        },
    }


class EditorialRetrospectiveTests(unittest.TestCase):
    def test_aggregates_latest_feedback_within_requested_window(self):
        from src.services.editorial_retrospective import build_editorial_retrospective

        now = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            history_dir = Path(temp_dir)
            _write_json(
                history_dir / "shadow-new.json",
                _shadow_report(
                    "shadow-new",
                    now - timedelta(days=1),
                    candidate_id="candidate-write",
                    source="Primary Source",
                    importance_score=8.8,
                    risk_level="low",
                    action="write",
                    reason="high importance",
                ),
            )
            _write_json(
                history_dir / "shadow-reserve.json",
                _shadow_report(
                    "shadow-reserve",
                    now - timedelta(days=2),
                    candidate_id="candidate-reserve",
                    source="Community Source",
                    importance_score=5.4,
                    risk_level="medium",
                    action="reserve",
                    reason="candidate pool limit",
                ),
            )
            _write_json(
                history_dir / "shadow-old.json",
                _shadow_report(
                    "shadow-old",
                    now - timedelta(days=10),
                    candidate_id="candidate-old",
                    source="Old Source",
                    importance_score=9.0,
                    risk_level="low",
                    action="write",
                    reason="old item",
                ),
            )
            _write_json(
                history_dir / "shadow-new.feedback.json",
                {
                    "events": [
                        {
                            "run_id": "shadow-new",
                            "candidate_id": "candidate-write",
                            "label": "bad_topic",
                            "recorded_at": (now - timedelta(hours=8)).isoformat(),
                        },
                        {
                            "run_id": "shadow-new",
                            "candidate_id": "candidate-write",
                            "label": "good_topic",
                            "recorded_at": (now - timedelta(hours=1)).isoformat(),
                        },
                    ]
                },
            )
            _write_json(
                history_dir / "shadow-reserve.feedback.json",
                {
                    "events": [
                        {
                            "run_id": "shadow-reserve",
                            "candidate_id": "candidate-reserve",
                            "label": "good_topic",
                            "recorded_at": (now - timedelta(hours=2)).isoformat(),
                        }
                    ]
                },
            )

            report = build_editorial_retrospective(history_dir, days=7, now=now)

        self.assertEqual(report["coverage"]["run_count"], 2)
        self.assertEqual(report["coverage"]["candidate_count"], 2)
        self.assertEqual(report["coverage"]["feedback_event_count"], 3)
        self.assertEqual(report["coverage"]["reviewed_candidate_count"], 2)
        self.assertEqual(report["feedback"]["labels"]["good_topic"], 2)
        self.assertEqual(report["feedback"]["labels"]["bad_topic"], 0)
        self.assertEqual(report["feedback"]["by_source"]["Primary Source"]["good_topic"], 1)
        self.assertEqual(report["analyst_calibration"]["by_risk_level"]["low"]["good_topic"], 1)
        self.assertEqual(report["editorial_outcomes"]["missed_good_topic_count"], 1)
        self.assertEqual(report["editorial_outcomes"]["write_good_topic_count"], 1)
        self.assertIn("覆盖天数少于 3 天", report["warnings"])
        self.assertIn("人工反馈少于 20 条", report["warnings"])

    def test_skips_malformed_history_and_reports_missing_feedback(self):
        from src.services.editorial_retrospective import build_editorial_retrospective

        now = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            history_dir = Path(temp_dir)
            _write_json(
                history_dir / "shadow-valid.json",
                _shadow_report(
                    "shadow-valid",
                    now - timedelta(days=1),
                    candidate_id="candidate-valid",
                    source="Example Source",
                    importance_score=7.2,
                    risk_level="high",
                    action="reject",
                    reason="evidence risk",
                ),
            )
            (history_dir / "shadow-bad.json").write_text("{not valid json", encoding="utf-8")

            report = build_editorial_retrospective(history_dir, days=7, now=now)

        self.assertEqual(report["coverage"]["run_count"], 1)
        self.assertEqual(report["coverage"]["reviewed_candidate_count"], 0)
        self.assertEqual(report["editorial_outcomes"]["rejection_reasons"], [{"reason": "evidence risk", "count": 1}])
        self.assertIn("没有人工反馈", report["warnings"])
        self.assertIn("已跳过无效历史文件: shadow-bad.json", report["warnings"])

    def test_saves_json_and_markdown_report_atomically(self):
        from src.services.editorial_retrospective import (
            build_editorial_retrospective,
            save_editorial_retrospective,
        )

        now = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            history_dir = Path(temp_dir)
            report = build_editorial_retrospective(history_dir, days=30, now=now)
            json_path, markdown_path = save_editorial_retrospective(
                report,
                output_dir=history_dir / "reviews",
                generated_at=now,
            )

            saved = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")

        self.assertEqual(saved["schema_version"], "editorial-retrospective-v1")
        self.assertEqual(json_path.name, "editorial-retrospective-20260720T120000Z.json")
        self.assertIn("# 编辑复盘报告", markdown)
        self.assertIn("没有可用的影子运行历史", markdown)


if __name__ == "__main__":
    unittest.main()
