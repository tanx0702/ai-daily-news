import os
import tempfile
import unittest

from src.file_utils import atomic_write_text


class FileUtilsTests(unittest.TestCase):
    def test_atomic_write_text_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "latest.json")
            atomic_write_text(path, "old")
            atomic_write_text(path, "new")

            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "new")

            leftovers = [name for name in os.listdir(tmpdir) if name.endswith(".tmp")]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
