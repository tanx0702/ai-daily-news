import os
import tempfile
import unittest

from src.pipeline_artifacts import collect_archive_links


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

if __name__ == "__main__":
    unittest.main()
