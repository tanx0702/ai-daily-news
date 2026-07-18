import os
import tempfile
import unittest

from src.pipeline_artifacts import build_latest_data, collect_archive_links


class PipelineArtifactsTests(unittest.TestCase):
    def test_collect_archive_links_sorts_newest_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_dir = os.path.join(tmpdir, "archive")
            os.makedirs(archive_dir)
            for name in ["2026-07-10.html", "2026-07-12.html", "notes.txt"]:
                with open(os.path.join(archive_dir, name), "w", encoding="utf-8") as f:
                    f.write("x")

            links = collect_archive_links(tmpdir, "https://tankex.xyz")

        self.assertEqual(
            links,
            [
                "https://tankex.xyz/archive/2026-07-12.html",
                "https://tankex.xyz/archive/2026-07-10.html",
            ],
        )

    def test_build_latest_data_includes_optional_reports(self):
        first_item = {"title": "OpenAI news"}
        data = build_latest_data(
            [first_item],
            "2026-07-12",
            "https://tankex.xyz",
            generated_at="2026-07-12T00:00:00+00:00",
            quality_report={
                "pass": True,
                "risk_level": "low",
                "blocked_publish": False,
                "issues": [],
                "applied_fixes": [{"field": "summary"}],
            },
            cover_subject={
                "mode": "story",
                "cover_title": "今日AI要闻",
                "cover_headline": "OpenAI news",
                "item": first_item,
            },
            media_report={
                "total": 1,
                "with_original_image": 1,
                "text_only": 0,
            },
        )

        self.assertEqual(data["date"], "2026-07-12")
        self.assertEqual(data["quality_gate"]["fixes_count"], 1)
        self.assertTrue(data["cover_subject"]["matches_top1"])
        self.assertEqual(data["media"]["with_original_image"], 1)

    def test_build_latest_data_includes_evidence_quality_diagnostics(self):
        data = build_latest_data(
            [{"title": "Source-only item", "quality_state": "source_only"}],
            "2026-07-17",
            "https://tankex.xyz",
            generated_at="2026-07-17T00:00:00+00:00",
            quality_report={
                "pass": True,
                "risk_level": "medium",
                "llm_review_status": "failed",
                "publish_filter": {"replaced_count": 2},
            },
            media_report={
                "total": 1,
                "with_original_image": 0,
                "text_only": 1,
                "items": [{"media_state": "rejected", "reason": "bad_url_hint"}],
            },
            selection_report={"selected_count": 10, "reserve_count": 20},
            source_health={"source_counts": {"Example": 1}, "source_only_count": 1},
        )

        self.assertEqual(data["quality_gate"]["llm_review_status"], "failed")
        self.assertEqual(data["quality_gate"]["publish_filter"]["replaced_count"], 2)
        self.assertEqual(data["media"]["rejected"], 1)
        self.assertEqual(data["diagnostics"]["editorial_selection"]["reserve_count"], 20)
        self.assertEqual(data["diagnostics"]["source_health"]["source_only_count"], 1)

    def test_build_latest_data_includes_publication_status(self):
        data = build_latest_data(
            [{"title": "Ready item"}],
            "2026-07-18",
            "https://tankex.xyz",
            generated_at="2026-07-18T00:00:00+00:00",
            publication={
                "status": "blocked",
                "ready": False,
                "reasons": ["source_concentration"],
            },
        )

        self.assertEqual(data["publication"]["status"], "blocked")
        self.assertEqual(data["publication"]["reasons"], ["source_concentration"])


if __name__ == "__main__":
    unittest.main()
