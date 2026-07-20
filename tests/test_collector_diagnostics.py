import unittest
from unittest.mock import patch

from src import collector


class CollectorDiagnosticsTests(unittest.TestCase):
    def test_collect_news_populates_opt_in_diagnostics_without_candidates(self):
        diagnostics = {}
        with patch.object(collector, "_load_sources", return_value=[]), patch.object(
            collector, "_env_enabled", return_value=False
        ):
            result = collector.collect_news(top_n=5, diagnostics=diagnostics)

        self.assertEqual(result, [])
        self.assertEqual(diagnostics["fetched_total"], 0)
        self.assertEqual(diagnostics["source_merge_removed"], 0)
        self.assertEqual(diagnostics["filtered_total"], 0)
        self.assertEqual(diagnostics["topic_cluster_removed"], 0)
        self.assertEqual(diagnostics["final_editorial_dedup_removed"], 0)
        self.assertEqual(diagnostics["returned_candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
