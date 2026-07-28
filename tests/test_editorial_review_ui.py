import json
import tempfile
import unittest
from base64 import b64encode
from pathlib import Path


def _shadow_report(run_id="shadow-review-1", title="<script>unsafe title</script>"):
    return {
        "run_id": run_id,
        "generated_at": "2026-07-27T00:00:00+00:00",
        "workflow": {"state": "completed"},
        "candidates": [
            {
                "candidate_id": "candidate-1",
                "source_title": title,
                "source_summary": "Source summary",
                "source_url": "https://example.com/source",
                "source": "Example Source",
                "published_at": "2026-07-27T00:00:00+00:00",
            }
        ],
        "analysis": {
            "items": [
                {
                    "candidate_id": "candidate-1",
                    "importance_score": 8.8,
                    "evidence_score": 8.0,
                    "impact_score": 8.2,
                    "risk_level": "low",
                    "importance_reason": "High impact",
                    "verifiability_reason": "Primary source",
                    "impact_analysis": "Useful context",
                }
            ]
        },
        "editorial": {
            "decisions": [
                {
                    "candidate_id": "candidate-1",
                    "action": "write",
                    "rank": 1,
                    "reason": "Worth writing",
                }
            ]
        },
    }


class EditorialReviewServiceTests(unittest.TestCase):
    def test_load_review_run_uses_latest_feedback_and_latest_completed_run(self):
        from src.services.editorial_review import load_review_run

        with tempfile.TemporaryDirectory() as temp_dir:
            history_dir = Path(temp_dir)
            (history_dir / "shadow-review-1.json").write_text(
                json.dumps(_shadow_report()), encoding="utf-8"
            )
            (history_dir / "shadow-review-1.feedback.json").write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "candidate_id": "candidate-1",
                                "label": "not_interesting",
                                "note": "Too narrow",
                                "recorded_at": "2026-07-27T01:00:00+00:00",
                            },
                            {
                                "candidate_id": "candidate-1",
                                "label": "good_topic",
                                "note": "Changed after reading source",
                                "recorded_at": "2026-07-27T02:00:00+00:00",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            failed = _shadow_report(run_id="shadow-failed")
            failed["generated_at"] = "2026-07-28T00:00:00+00:00"
            failed["workflow"]["state"] = "failed"
            (history_dir / "shadow-failed.json").write_text(
                json.dumps(failed), encoding="utf-8"
            )

            review = load_review_run(history_dir)
            unknown = load_review_run(history_dir, run_id="shadow-missing")

        candidate = review["candidates"][0]
        self.assertEqual(review["run_id"], "shadow-review-1")
        self.assertEqual(candidate["analysis"]["importance_score"], 8.8)
        self.assertEqual(candidate["decision"]["action"], "write")
        self.assertEqual(candidate["feedback"]["label"], "good_topic")
        self.assertEqual(candidate["feedback"]["note"], "Changed after reading source")
        self.assertIsNone(unknown)


class EditorialReviewRouteTests(unittest.TestCase):
    def setUp(self):
        import app

        self.app_module = app
        self.temp_dir = tempfile.TemporaryDirectory()
        self.history_dir = Path(self.temp_dir.name)
        (self.history_dir / "shadow-review-1.json").write_text(
            json.dumps(_shadow_report()), encoding="utf-8"
        )
        self.original_username = app.EDITORIAL_REVIEW_USERNAME
        self.original_password = app.EDITORIAL_REVIEW_PASSWORD
        self.original_history_dir = app.SHADOW_HISTORY_DIR
        app.EDITORIAL_REVIEW_USERNAME = "editor"
        app.EDITORIAL_REVIEW_PASSWORD = "review-password"
        app.SHADOW_HISTORY_DIR = self.history_dir
        app.app.config.update(TESTING=True)
        self.client = app.app.test_client()

    def tearDown(self):
        self.app_module.EDITORIAL_REVIEW_USERNAME = self.original_username
        self.app_module.EDITORIAL_REVIEW_PASSWORD = self.original_password
        self.app_module.SHADOW_HISTORY_DIR = self.original_history_dir
        self.temp_dir.cleanup()

    def test_page_hides_when_disabled_and_requires_auth_when_enabled(self):
        self.app_module.EDITORIAL_REVIEW_PASSWORD = ""
        self.assertEqual(self.client.get("/editorial-review").status_code, 404)

        self.app_module.EDITORIAL_REVIEW_PASSWORD = "review-password"
        unauthenticated = self.client.get("/editorial-review")
        authenticated = self.client.get("/editorial-review", headers=self._auth_header())

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertIn("Basic", unauthenticated.headers["WWW-Authenticate"])
        self.assertEqual(authenticated.status_code, 200)
        page = authenticated.get_data(as_text=True)
        self.assertIn("&lt;script&gt;unsafe title&lt;/script&gt;", page)
        self.assertNotIn("<script>unsafe title</script>", page)
        self.assertIn("Source summary", page)

    def test_feedback_endpoint_appends_valid_label_and_rejects_invalid_payload(self):
        report_path = self.history_dir / "shadow-review-1.json"
        report_before = report_path.read_text(encoding="utf-8")
        valid = self.client.post(
            "/editorial-review/feedback",
            headers=self._auth_header(),
            json={
                "run_id": "shadow-review-1",
                "candidate_id": "candidate-1",
                "label": "good_topic",
                "note": "Useful and timely",
            },
        )
        invalid = self.client.post(
            "/editorial-review/feedback",
            headers=self._auth_header(),
            json={"run_id": "shadow-review-1", "candidate_id": "candidate-1", "label": "wrong", "note": []},
        )

        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.get_json()["event"]["label"], "good_topic")
        self.assertEqual(report_path.read_text(encoding="utf-8"), report_before)
        self.assertTrue((self.history_dir / "shadow-review-1.feedback.json").is_file())
        self.assertEqual(invalid.status_code, 400)

    def test_page_offers_explicit_note_save_and_displays_latest_saved_note(self):
        first = self.client.post(
            "/editorial-review/feedback",
            headers=self._auth_header(),
            json={
                "run_id": "shadow-review-1",
                "candidate_id": "candidate-1",
                "label": "good_topic",
                "note": "",
            },
        )
        second = self.client.post(
            "/editorial-review/feedback",
            headers=self._auth_header(),
            json={
                "run_id": "shadow-review-1",
                "candidate_id": "candidate-1",
                "label": "good_topic",
                "note": "适合从产业影响角度写。",
            },
        )
        page = self.client.get("/editorial-review", headers=self._auth_header())

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(page.status_code, 200)
        rendered = page.get_data(as_text=True)
        self.assertIn("data-save-note", rendered)
        self.assertIn("保存备注", rendered)
        self.assertIn("备注尚未保存", rendered)
        self.assertIn("适合从产业影响角度写。", rendered)
        feedback = json.loads(
            (self.history_dir / "shadow-review-1.feedback.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(feedback["events"]), 2)
        self.assertEqual(feedback["events"][-1]["label"], "good_topic")
        self.assertEqual(feedback["events"][-1]["note"], "适合从产业影响角度写。")

    def test_feedback_endpoint_rejects_note_longer_than_1000_characters(self):
        response = self.client.post(
            "/editorial-review/feedback",
            headers=self._auth_header(),
            json={
                "run_id": "shadow-review-1",
                "candidate_id": "candidate-1",
                "label": "good_topic",
                "note": "x" * 1001,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("1000", response.get_json()["error"])

    @staticmethod
    def _auth_header():
        token = b64encode(b"editor:review-password").decode()
        return {"Authorization": f"Basic {token}"}


if __name__ == "__main__":
    unittest.main()
