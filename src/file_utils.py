"""Filesystem helpers used by the daily pipeline."""

import os
import tempfile


def atomic_write_text(path: str, content: str, encoding: str = "utf-8") -> None:
    """Write text to path atomically by replacing the target after a full write."""
    target_dir = os.path.dirname(path) or "."
    os.makedirs(target_dir, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=target_dir,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(tmp_path, path)
        os.chmod(path, 0o644)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
