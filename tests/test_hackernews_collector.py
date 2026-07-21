import unittest
from concurrent.futures import TimeoutError as FuturesTimeoutError
from unittest.mock import patch

from src.collectors import hackernews


class _FakeFuture:
    def __init__(self, value):
        self.value = value
        self.cancelled = False

    def result(self):
        return self.value

    def cancel(self):
        self.cancelled = True
        return True


class _FakeExecutor:
    last_instance = None

    def __init__(self, max_workers):
        self.max_workers = max_workers
        self.futures = []
        self.shutdown_args = None
        _FakeExecutor.last_instance = self

    def submit(self, fn, sid):
        future = _FakeFuture({"id": sid, "title": f"AI story {sid}"})
        self.futures.append(future)
        return future

    def shutdown(self, wait=True, cancel_futures=False):
        self.shutdown_args = (wait, cancel_futures)


def _as_completed_with_timeout(futures, timeout=None):
    futures = list(futures)
    if timeout != 0.01:
        raise AssertionError(f"Expected total timeout 0.01, got {timeout!r}")
    yield futures[0]
    raise FuturesTimeoutError()


class HackerNewsCollectorTests(unittest.TestCase):
    def test_fetch_items_parallel_uses_global_timeout_and_cancels_pending(self):
        collector = hackernews.HackerNewsCollector(timeout=30, details_timeout=0.01)

        with patch.object(hackernews, "ThreadPoolExecutor", _FakeExecutor):
            with patch.object(hackernews, "as_completed", _as_completed_with_timeout):
                result = collector._fetch_items_parallel([1, 2, 3], max_workers=2)

        executor = _FakeExecutor.last_instance
        self.assertEqual(result, [{"id": 1, "title": "AI story 1"}])
        self.assertEqual(executor.shutdown_args, (False, True))
        self.assertFalse(executor.futures[0].cancelled)
        self.assertTrue(executor.futures[1].cancelled)
        self.assertTrue(executor.futures[2].cancelled)


if __name__ == "__main__":
    unittest.main()
