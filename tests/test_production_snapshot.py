import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import json

from src.file_utils import atomic_write_text


class ProductionSnapshotTests(unittest.TestCase):
    def test_round_trip_restores_known_datetime_fields_and_item_count(self):
        from src.services.production_snapshot import (
            load_production_snapshot,
            save_production_snapshot,
        )

        items = [
            {
                "title": "Production candidate",
                "published_at": datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc),
                "source_published_at": datetime(
                    2026, 8, 3, 7, 30, tzinfo=timezone.utc
                ),
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = save_production_snapshot(
                items,
                date_str="2026-08-03",
                snapshot_dir=Path(temp_dir),
            )
            loaded_items, diagnostics = load_production_snapshot(path)

        self.assertEqual(len(loaded_items), len(items))
        self.assertEqual(loaded_items[0]["published_at"], items[0]["published_at"])
        self.assertEqual(
            loaded_items[0]["source_published_at"], items[0]["source_published_at"]
        )
        self.assertEqual(diagnostics.returned_candidate_count, len(items))

    def test_load_rejects_a_snapshot_with_a_non_object_root(self):
        from src.services.production_snapshot import load_production_snapshot

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invalid.json"
            atomic_write_text(str(path), "[]")

            with self.assertRaises(ValueError):
                load_production_snapshot(path)

    def test_load_rejects_an_invalid_generated_timestamp(self):
        from src.services.production_snapshot import load_production_snapshot

        snapshot = {
            "schema_version": "production-snapshot-v1",
            "report_date": "2026-08-03",
            "generated_at": "not-a-timestamp",
            "items": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invalid.json"
            atomic_write_text(str(path), json.dumps(snapshot))

            with self.assertRaises(ValueError):
                load_production_snapshot(path)


if __name__ == "__main__":
    unittest.main()
