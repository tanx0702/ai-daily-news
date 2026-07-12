import os
import unittest
from datetime import datetime, timezone

from src.time_utils import report_date_str


class TimeUtilsTests(unittest.TestCase):
    def test_report_date_uses_shanghai_by_default(self):
        original = os.environ.pop("APP_TIMEZONE", None)
        try:
            dt = datetime(2026, 7, 11, 17, 0, tzinfo=timezone.utc)
            self.assertEqual(report_date_str(dt), "2026-07-12")
        finally:
            if original is not None:
                os.environ["APP_TIMEZONE"] = original

    def test_report_date_honors_configured_timezone(self):
        original = os.environ.get("APP_TIMEZONE")
        try:
            os.environ["APP_TIMEZONE"] = "UTC"
            dt = datetime(2026, 7, 11, 17, 0, tzinfo=timezone.utc)
            self.assertEqual(report_date_str(dt), "2026-07-11")
        finally:
            if original is None:
                os.environ.pop("APP_TIMEZONE", None)
            else:
                os.environ["APP_TIMEZONE"] = original


if __name__ == "__main__":
    unittest.main()
