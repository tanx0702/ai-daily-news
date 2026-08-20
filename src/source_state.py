"""Persistent, sanitized health state for external news sources."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from typing import Any


_HEALTHY_STATUSES = frozenset({"success", "not_modified"})
_DEFAULT_PATH = "runtime/source-state.db"


class SourceStateStore:
    """Store the latest observable result for each configured source."""

    def __init__(self, path: str):
        self.path = str(Path(path).expanduser())
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS source_health (
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                last_attempt_at TEXT NOT NULL,
                last_success_at TEXT,
                status TEXT NOT NULL,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_item_count INTEGER NOT NULL DEFAULT 0,
                last_latency_ms INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                last_content_hash TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (source_name, source_url)
            )
            """
        )
        self._connection.commit()

    @classmethod
    def from_environment(cls) -> "SourceStateStore":
        path = os.environ.get("SOURCE_STATE_DB_PATH", "").strip() or _DEFAULT_PATH
        return cls(path)

    def record(
        self,
        source_name: str,
        source_url: str,
        *,
        status: str,
        item_count: int,
        latency_ms: int,
        error: str = "",
        content_hash: str = "",
        attempted_at: str | None = None,
    ) -> None:
        now = attempted_at or datetime.now(timezone.utc).isoformat()
        previous = self._connection.execute(
            """
            SELECT consecutive_failures, last_success_at
            FROM source_health
            WHERE source_name = ? AND source_url = ?
            """,
            (source_name, source_url),
        ).fetchone()
        prior_failures = int(previous["consecutive_failures"]) if previous else 0
        failures = 0 if status in _HEALTHY_STATUSES else prior_failures + 1
        last_success_at = (
            now
            if status in _HEALTHY_STATUSES
            else previous["last_success_at"] if previous else None
        )
        self._connection.execute(
            """
            INSERT INTO source_health (
                source_name, source_url, last_attempt_at, last_success_at,
                status, consecutive_failures, last_item_count,
                last_latency_ms, last_error, last_content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_name, source_url) DO UPDATE SET
                last_attempt_at = excluded.last_attempt_at,
                last_success_at = excluded.last_success_at,
                status = excluded.status,
                consecutive_failures = excluded.consecutive_failures,
                last_item_count = excluded.last_item_count,
                last_latency_ms = excluded.last_latency_ms,
                last_error = excluded.last_error,
                last_content_hash = excluded.last_content_hash
            """,
            (
                source_name,
                source_url,
                now,
                last_success_at,
                status,
                failures,
                max(int(item_count), 0),
                max(int(latency_ms), 0),
                str(error or "")[:300],
                str(content_hash or "")[:128],
            ),
        )
        self._connection.commit()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT source_name, source_url, last_attempt_at, last_success_at,
                   status, consecutive_failures, last_item_count,
                   last_latency_ms, last_error, last_content_hash
            FROM source_health
            ORDER BY source_name, source_url
            """
        ).fetchall()
        return {
            str(row["source_name"]): {
                "source_name": str(row["source_name"]),
                "source_url": str(row["source_url"]),
                "last_attempt_at": str(row["last_attempt_at"]),
                "last_success_at": row["last_success_at"],
                "status": str(row["status"]),
                "consecutive_failures": int(row["consecutive_failures"]),
                "last_item_count": int(row["last_item_count"]),
                "last_latency_ms": int(row["last_latency_ms"]),
                "last_error": str(row["last_error"]),
                "last_content_hash": str(row["last_content_hash"]),
            }
            for row in rows
        }

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "SourceStateStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
