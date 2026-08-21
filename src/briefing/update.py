"""Deterministic eligibility checks for concrete X AI updates."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from src.briefing.opinion import x_content_rejection_reason


_MODEL_VERSION = re.compile(
    r"\b(?:gpt|claude|gemini|llama|qwen|deepseek|mistral)[\w.+-]*\d[\w.+-]*\b|"
    r"\b(?:model|模型)\s*v?\d+(?:\.\d+)*\b",
    re.IGNORECASE,
)
_MECHANICAL_PROGRESS = re.compile(
    r"\b\d+(?:\.\d+)?\s*%|#\s*\d+\b|\b(?:rank|排名)\s*#?\s*\d+|"
    r"\b\d+(?:\.\d+)?\s*(?:x|ms|s|tok/s|tokens/s)\b|"
    r"\b(?:speed|latency|速度|延迟)\s*\d+",
    re.IGNORECASE,
)
_RESULT_RELATION = re.compile(
    r"\b(?:scores?|reaches?|rank(?:s|ed)?|places?|improves?|improved|"
    r"increases?|increased|decreases?|decreased|higher|lower|faster|slower|"
    r"outperforms?|beats?|achieves?|achieved)\b|达到|提升|降低|加快|减少|超过",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class UpdateEligibility:
    eligible: bool
    reason_codes: tuple[str, ...]


def evaluate_ai_update_candidate(candidate: Mapping[str, object]) -> UpdateEligibility:
    """Accept only substantive posts with a concrete technical anchor."""
    text = " ".join(str(candidate.get("summary") or candidate.get("title") or "").split())
    rejection_candidate = dict(candidate)
    rejection_candidate["summary"] = text
    if x_content_rejection_reason(rejection_candidate):
        return UpdateEligibility(False, ("update_promotional_or_repost",))
    visible = re.sub(r"https?://\S+", "", text).strip()
    if len(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", visible)) < 8:
        return UpdateEligibility(False, ("update_no_substantive_detail",))
    if not _has_update_anchor(visible):
        return UpdateEligibility(False, ("update_missing_concrete_anchor",))
    return UpdateEligibility(True, ())


def _has_update_anchor(text: str) -> bool:
    return bool(
        _MODEL_VERSION.search(text)
        and _MECHANICAL_PROGRESS.search(text)
        and _RESULT_RELATION.search(text)
    )
