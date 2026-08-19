import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from src.collectors.arxiv import ArxivCollector


class ArxivCollectorTests(unittest.TestCase):
    def test_fetch_retries_once_after_timeout(self):
        response = Mock(content=b"feed")
        response.raise_for_status.return_value = None
        entry = {
            "id": "https://arxiv.org/abs/2608.12345",
            "title": "Large language model agents improve code generation",
            "summary": "A benchmark study of large language model agents.",
            "authors": [{"name": "Example Author"}],
            "published_parsed": time.gmtime(),
            "arxiv_primary_category": {"term": "cs.AI"},
        }
        collector = ArxivCollector(timeout=1)

        with patch(
            "src.collectors.arxiv.requests.get",
            side_effect=[requests.exceptions.Timeout(), response],
        ) as get, patch(
            "src.collectors.arxiv.feedparser.parse",
            return_value=SimpleNamespace(entries=[entry]),
        ):
            candidates = collector.fetch()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(get.call_count, 2)

    def test_fetch_stops_after_two_timeouts(self):
        collector = ArxivCollector(timeout=1)

        with patch(
            "src.collectors.arxiv.requests.get",
            side_effect=requests.exceptions.Timeout(),
        ) as get:
            candidates = collector.fetch()

        self.assertEqual(candidates, [])
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
