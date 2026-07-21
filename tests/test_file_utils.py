import os
import tempfile
import unittest
from unittest.mock import patch

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

    def test_atomic_write_text_makes_public_artifacts_readable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "index.html")
            with patch("src.file_utils.os.chmod") as chmod:
                atomic_write_text(path, "public")

        chmod.assert_called_once_with(path, 0o644)


if __name__ == "__main__":
    unittest.main()
