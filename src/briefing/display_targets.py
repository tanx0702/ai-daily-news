"""Stable display targets shared by fact-brief generation and validation."""

from __future__ import annotations

import re
from typing import Mapping


def summary_sentences(value: str) -> tuple[str, ...]:
    """Return one or two complete display sentences without terminal punctuation."""
    return tuple(
        part.strip().rstrip("。！？!? ")
        for part in re.split(r"(?<=[。！？!?])\s*", value.strip())
        if part.strip().rstrip("。！？!? ")
    )


def display_targets(title: str, brief: str) -> Mapping[str, str]:
    """Map supported target identifiers to their complete displayed claims."""
    targets = {"title": title.strip()}
    for index, sentence in enumerate(summary_sentences(brief), 1):
        targets[f"brief_{index}"] = sentence
    return targets
