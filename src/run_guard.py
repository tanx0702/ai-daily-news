"""Single-run guard for scheduled pipeline executions."""

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator


class RunLockError(RuntimeError):
    """Raised when another pipeline run appears to be active."""


@contextmanager
def single_run_lock(lock_path: str, ttl_seconds: int = 6 * 60 * 60) -> Iterator[None]:
    """Acquire an atomic lock file for one pipeline run.

    The lock is created with O_EXCL so concurrent starts cannot both enter.
    A stale lock older than ttl_seconds is removed and retried.
    """
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    fd: int | None = None

    while fd is None:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            if _is_stale_lock(lock_path, ttl_seconds):
                try:
                    os.remove(lock_path)
                except FileNotFoundError:
                    pass
                continue
            raise RunLockError(f"lock file exists: {lock_path}") from exc

    try:
        payload = {
            "pid": os.getpid(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        yield
    finally:
        os.close(fd)
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass


def _is_stale_lock(lock_path: str, ttl_seconds: int) -> bool:
    if ttl_seconds <= 0:
        return False
    try:
        age_seconds = time.time() - os.path.getmtime(lock_path)
    except FileNotFoundError:
        return True
    return age_seconds > ttl_seconds
