import os
import tempfile
import unittest

from src.run_guard import RunLockError, single_run_lock


class RunGuardTests(unittest.TestCase):
    def test_single_run_lock_blocks_nested_acquire(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = os.path.join(tmpdir, "daily.lock")

            with single_run_lock(lock_path):
                self.assertTrue(os.path.exists(lock_path))
                with self.assertRaises(RunLockError):
                    with single_run_lock(lock_path, ttl_seconds=0):
                        pass

            self.assertFalse(os.path.exists(lock_path))


if __name__ == "__main__":
    unittest.main()
