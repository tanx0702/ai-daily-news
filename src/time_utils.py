"""Time helpers for user-facing daily report dates."""

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_APP_TIMEZONE = "Asia/Shanghai"


def app_timezone() -> ZoneInfo:
    """Return the configured timezone for report dates."""
    name = os.environ.get("APP_TIMEZONE", DEFAULT_APP_TIMEZONE).strip()
    if not name:
        name = DEFAULT_APP_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_APP_TIMEZONE)


def now_in_app_timezone() -> datetime:
    """Return current time in the configured app timezone."""
    return datetime.now(app_timezone())


def report_date_str(dt: datetime | None = None) -> str:
    """Return YYYY-MM-DD using the configured app timezone."""
    if dt is None:
        dt = now_in_app_timezone()
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(app_timezone())
    return dt.strftime("%Y-%m-%d")
