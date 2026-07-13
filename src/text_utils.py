"""
Text normalization helpers for news content.

Used to clean titles, summaries, source labels, and prompt text before HTML
escaping or LLM serialization.
"""

from __future__ import annotations

from html import unescape
from typing import Any


def clean_display_text(value: Any, *, collapse_whitespace: bool = True, max_passes: int = 3) -> str:
    """
    Normalize user-visible text.

    - Recursively decodes nested HTML entities like ``&amp;#x27;``
    - Replaces non-breaking spaces
    - Optionally collapses repeated whitespace into a single space
    """
    if value is None:
        return ""

    text = str(value)
    for _ in range(max(1, max_passes)):
        decoded = unescape(text)
        if decoded == text:
            break
        text = decoded

    text = text.replace("\u00a0", " ")
    if collapse_whitespace:
        text = " ".join(text.split())
    else:
        text = text.strip()
    return text.strip()
