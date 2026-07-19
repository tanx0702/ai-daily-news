"""LLM-assisted editorial comparison for already sourced candidates."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Iterable

from openai import OpenAI


logger = logging.getLogger(__name__)

_EVENT_KEY_PATTERN = re.compile(r"[a-z0-9][a-z0-9:_-]{0,120}")

_SYSTEM_PROMPT = """你是 AI 科技日报的选题编辑。你不能联网，也不能补充输入以外的事实。

请基于每条候选的原始标题、原始摘要、时间和来源完成两件事：
1. 将同一公司在同一发布会、同一诉讼、同一融资或同一产品事件中的多篇报道归为同一个 event_key。
2. 以 0 到 10 的 editorial_score 判断其是否值得进入当天日报。考虑时效、原始证据完整性、独立新闻价值和普通 AI 读者收益。

GitHub 的近期 push 不是产品发布；只有输入明确给出 release/版本说明时，才可按发布事件评价。
不要因来源知名、星标或营销措辞提高分数。不能从输入确认时请降低分数。
event_key 必须只用小写英文、数字、冒号、连字符或下划线，例如 event:tencent:waic-2026。
只返回 JSON。"""


def _valid_event_key(value: object) -> bool:
    return isinstance(value, str) and bool(_EVENT_KEY_PATTERN.fullmatch(value))


def _score_cap(item: dict) -> float:
    event_type = str((item.get("_editorial") or {}).get("event_type") or "")
    if event_type == "github_activity":
        return 7.4
    if event_type == "github_new_repository":
        return 8.4
    return 10.0


def apply_editorial_review(items: Iterable[dict], payload: dict) -> dict:
    """Safely merge a validated editorial-review response into candidates."""
    candidates = list(items)
    reviews = payload.get("items", []) if isinstance(payload, dict) else []
    if not isinstance(reviews, list):
        reviews = []

    applied_count = 0
    rejected_count = 0
    for review in reviews:
        if not isinstance(review, dict):
            rejected_count += 1
            continue
        raw_index = review.get("index")
        if not isinstance(raw_index, int) or not 1 <= raw_index <= len(candidates):
            rejected_count += 1
            continue
        event_key = review.get("event_key")
        score = review.get("score")
        if not _valid_event_key(event_key) or not isinstance(score, (int, float)):
            rejected_count += 1
            continue

        item = candidates[raw_index - 1]
        editorial = item.get("_editorial")
        if not isinstance(editorial, dict):
            rejected_count += 1
            continue

        bounded_score = max(0.0, min(float(score), 10.0))
        deterministic_score = float(editorial.get("score", 0.0) or 0.0)
        blended_score = min(
            _score_cap(item),
            round(deterministic_score * 0.7 + bounded_score * 0.3, 1),
        )
        editorial["event_key"] = event_key
        editorial["llm_score"] = round(bounded_score, 1)
        editorial["score"] = blended_score
        editorial.setdefault("reasons", []).append("llm_editorial_review")
        reason = review.get("reason")
        if isinstance(reason, str) and reason.strip():
            editorial["llm_reason"] = reason.strip()[:240]
        applied_count += 1

    return {
        "applied_count": applied_count,
        "rejected_count": rejected_count,
    }


def _compact_candidates(items: Iterable[dict]) -> list[dict]:
    compact: list[dict] = []
    for index, item in enumerate(items, start=1):
        editorial = item.get("_editorial") or {}
        compact.append(
            {
                "index": index,
                "original_title": str(item.get("source_title") or item.get("title") or "")[:220],
                "original_summary": str(item.get("source_summary") or "")[:420],
                "published_at": str(item.get("published_at") or "")[:40],
                "source": str(item.get("source") or "")[:80],
                "source_type": str(item.get("source_type") or "")[:40],
                "source_tier": str(item.get("source_tier") or "")[:30],
                "deterministic_event_key": str(editorial.get("event_key") or "")[:120],
                "deterministic_score": editorial.get("score", 0),
                "event_type": str(editorial.get("event_type") or "")[:40],
            }
        )
    return compact


def _parse_json_object(content: object) -> dict | None:
    if not isinstance(content, str):
        return None
    text = content.strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for offset, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def review_editorial_candidates(
    items: Iterable[dict],
    *,
    api_key: str,
    model: str,
    base_url: str,
    timeout: int,
) -> dict:
    """Ask the quality model to compare candidates, retaining local fallback."""
    candidates = list(items)
    if not api_key or not candidates:
        return {"status": "skipped", "applied_count": 0, "rejected_count": 0, "notes": []}

    user_prompt = json.dumps(
        {
            "candidates": _compact_candidates(candidates),
            "instruction": (
                "返回 {\"items\":[...],\"edition_notes\":[...]}。"
                "items 每项必须包含 index(从 1 开始), event_key, score(0-10), reason。"
                "所有候选都必须恰好返回一次。"
            ),
        },
        ensure_ascii=False,
    )
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            max_tokens=int(os.environ.get("EDITORIAL_REVIEW_MAX_TOKENS", "5000")),
            response_format={"type": "json_object"},
        )
        payload = _parse_json_object(response.choices[0].message.content)
        if payload is None:
            raise ValueError("editorial review did not return a JSON object")
        applied = apply_editorial_review(candidates, payload)
        notes = payload.get("edition_notes", [])
        if not isinstance(notes, list):
            notes = []
        return {"status": "passed", **applied, "notes": [str(note)[:240] for note in notes]}
    except Exception as exc:
        logger.warning("Editorial LLM review failed, keeping deterministic scores: %s", exc)
        return {
            "status": "failed",
            "applied_count": 0,
            "rejected_count": 0,
            "notes": [f"editorial_review_failed: {exc}"],
        }
