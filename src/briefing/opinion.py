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
_MODEL_RELEASE_PARTY = re.compile(
    r"\bparty\s+for\s+(?:our\s+)?(?:next|new)\s+model\s+release\b",
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
# Technical-process nouns that contain a stance marker as a substring but never
# express the author's own stance (e.g. "predictions" inside "predict").
_TECHNICAL_NOUNS = re.compile(
    r"\b(?:predictions?|predicted|predictors?|expectations?|expected|"
    r"comparisons?|compared|comparison|versus|models?|modeling)\b",
    re.IGNORECASE,
)
# Instructional/courseware framing: explaining how something works is not a stance.
_INSTRUCTIONAL = re.compile(
    r"(?:\b(?:in\s+this\s+(?:video|talk|post|thread|guide|article)|"
    r"i\s+explain\s+how|this\s+(?:guide|tutorial|overview|walkthrough|video)\s+"
    r"(?:shows?|covers?|explains?|walks)|"
    r"how\s+to\s+build|walks?\s+through)\b|教程|讲解|本文介绍|本文将介绍|如何实现)",
    re.IGNORECASE,
)
# An explicit first-person stance verb: the author asserting their own view.
_FIRST_PERSON_STANCE = re.compile(
    r"\b(?:i\s+(?:think|believe|predict|expect|argue|suspect|bet)|"
    r"my\s+(?:view|take|prediction)|in\s+my\s+view)\b"
    r"|我认为|我觉得|我相信|我预计|我的看法",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class OpinionEligibility:
    eligible: bool
    reason_codes: tuple[str, ...]
    stance_type: str = ""
    context_complete: bool = False
    original_post: bool = False


def _is_promotional(text: str) -> bool:
    lower = text.lower()
    return bool(
        any(marker in lower for marker in _PROMOTIONAL)
        or _COURSE_PROMOTION.search(text)
        or _MODEL_RELEASE_PARTY.search(text)
    )


def x_content_rejection_reason(candidate: Mapping[str, object]) -> str:
    """Reject reposts and promotional posts before they can become X facts."""
    text = str(candidate.get("summary") or candidate.get("text") or "").strip()
    if (
        bool(candidate.get("x_is_repost") or candidate.get("is_repost"))
        or _REPOST_PREFIX.match(text)
    ):
        return "x_repost"
    if _is_promotional(text):
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
    if _is_promotional(text):
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
    stance_type = _detect_stance(text)
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


def _marker_in_text(text: str, marker: str) -> bool:
    """Match a stance marker on word boundaries for Latin text, substring for CJK."""
    if any("\u4e00" <= char <= "\u9fff" for char in marker):
        return marker in text
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", text, re.I))


def _detect_stance(text: str) -> str:
    """Return the author's stance type, or '' when there is no substantive stance.

    Technical-process wording must not impersonate a stance: ``predictions`` is a
    technical noun, not the author predicting something. Instructional framing
    that merely explains how something works carries no attributable stance.
    """
    lowered = text.lower()
    markers_hit = {
        stance: tuple(
            marker for marker in markers if _marker_in_text(lowered, marker)
        )
        for stance, markers in _STANCE_MARKERS.items()
    }
    if not any(markers_hit.values()):
        return ""
    first_person = bool(_FIRST_PERSON_STANCE.search(text))
    # A first-person stance verb is decisive evidence of the author's own view.
    if first_person:
        for stance in ("opinion", "critique", "prediction", "comparison"):
            if markers_hit.get(stance):
                return stance
        return "opinion"
    # Without an explicit author stance, instructional or purely technical
    # wording must not be upgraded into an opinion.
    if _INSTRUCTIONAL.search(text) or _TECHNICAL_NOUNS.search(text):
        return ""
    for stance, markers in _STANCE_MARKERS.items():
        if markers_hit[stance]:
            return stance
    return ""
