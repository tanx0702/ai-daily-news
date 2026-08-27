"""Deterministic eligibility checks for attributed X opinions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping


_PROMOTIONAL = (
    "招聘", "课程", "报名", "折扣", "优惠", "join us", "hiring",
    "register", "workshop", "webinar", "congratulations", "congrats", "祝贺",
)
_COURSE_PROMOTION = re.compile(
    r"\b(?:(?:our|this|new|ai)\s+course|course\s+(?:registration|enrollment))\b",
    re.IGNORECASE,
)
_REPOST_PREFIX = re.compile(r"^\s*(?:rt\s+@|转发\s*[:：])", re.IGNORECASE)
_AI_TOPIC = re.compile(
    r"(?:\b(?:ai|artificial intelligence|machine learning|deep learning|llms?|"
    r"models?|agents?|gpt|claude|gemini|llama|qwen|deepseek|mistral|openai|"
    r"anthropic)\b|人工智能|机器学习|深度学习|大模型|模型|智能体)",
    re.IGNORECASE,
)
_STANCE_MARKERS = {
    "prediction": ("will", "likely", "expect", "predict", "将会", "可能", "预计"),
    "critique": (
        "wrong", "fail", "problem", "overrated", "not every",
        "质疑", "错误", "问题", "局限",
    ),
    "comparison": ("better", "worse", "than", "versus", "更好", "不如", "相比"),
    "opinion": (
        "i think", "i believe", "in my view", "认为", "我觉得", "我相信", "观点", "需要",
    ),
}


@dataclass(frozen=True, slots=True)
class OpinionEligibility:
    eligible: bool
    reason_codes: tuple[str, ...]
    stance_type: str = ""
    context_complete: bool = False
    original_post: bool = False


def x_content_rejection_reason(candidate: Mapping[str, object]) -> str:
    """Reject reposts and promotional posts before they can become X facts."""
    text = str(candidate.get("summary") or candidate.get("text") or "").strip()
    if (
        bool(candidate.get("x_is_repost") or candidate.get("is_repost"))
        or _REPOST_PREFIX.match(text)
    ):
        return "x_repost"
    lower = text.lower()
    if any(marker in lower for marker in _PROMOTIONAL) or _COURSE_PROMOTION.search(text):
        return "x_promotional_content"
    return ""


def evaluate_opinion_candidate(
    candidate: Mapping[str, object],
    registry_source: Mapping[str, object] | None,
) -> OpinionEligibility:
    """Accept only substantive, attributable posts from explicitly allowed people."""
    if not registry_source or not bool(registry_source.get("opinion_eligible")):
        return OpinionEligibility(False, ("opinion_author_not_allowed",))

    text = str(candidate.get("summary") or candidate.get("text") or "").strip()
    reply_to = str(candidate.get("x_reply_to_id") or candidate.get("reply_to_id") or "")
    quoted_id = str(candidate.get("x_quoted_id") or candidate.get("quoted_id") or "")
    context_complete = bool(
        candidate.get("x_context_complete") or candidate.get("context_complete")
    )
    original_post = not bool(candidate.get("x_is_repost") or candidate.get("is_repost"))
    if not original_post:
        return OpinionEligibility(False, ("opinion_repost_only",), original_post=False)
    if reply_to and not context_complete:
        return OpinionEligibility(
            False,
            ("opinion_context_missing",),
            context_complete=False,
            original_post=True,
        )
    lower = text.lower()
    if any(marker in lower for marker in _PROMOTIONAL) or _COURSE_PROMOTION.search(text):
        return OpinionEligibility(
            False,
            ("opinion_promotional_content",),
            context_complete=context_complete or not reply_to,
            original_post=True,
        )
    words = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", text)
    link_only = not re.sub(r"https?://\S+", "", text).strip()
    if link_only or (quoted_id and len(words) < 8):
        return OpinionEligibility(
            False,
            ("opinion_repost_only",),
            context_complete=context_complete or not reply_to,
            original_post=True,
        )
    if not _AI_TOPIC.search(text):
        return OpinionEligibility(
            False,
            ("opinion_no_ai_topic",),
            context_complete=context_complete or not reply_to,
            original_post=True,
        )
    stance_type = next(
        (
            stance
            for stance, markers in _STANCE_MARKERS.items()
            if any(marker in lower for marker in markers)
        ),
        "",
    )
    if len(words) < 10 or not stance_type:
        return OpinionEligibility(
            False,
            ("opinion_no_substantive_claim",),
            context_complete=context_complete or not reply_to,
            original_post=True,
        )
    return OpinionEligibility(
        True,
        (),
        stance_type=stance_type,
        context_complete=context_complete or not reply_to,
        original_post=True,
    )
