"""LLM scoring filter: keep the most briefing-worthy items, drop the noise.

Replaces the rule-based relevance gates (action-word vocabularies, anchor
binding, non-news pattern matching) with a single scored judgement, following
the "AI ranks, rules deduplicate" model used by successful news-radar projects.

Design constraints:
- The scorer never blocks an edition: when the LLM is unavailable, times out or
  answers garbage, every item passes with a sentinel score and the pipeline
  falls back to the deterministic ordering it already had.
- Scoring is one batched call per edition (bounded by the candidate pool), not
  one call per item.
- The score is advisory ordering plus a hard cut: items scoring below the
  threshold are dropped, items the scorer could not judge are kept.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Iterable, Mapping

from src.llm_config import LLMConfig, structured_llm_request_options

logger = logging.getLogger(__name__)

DEFAULT_SCORE_THRESHOLD = 5.0
DEFAULT_MAX_CANDIDATES = 60

_SYSTEM_PROMPT = (
    "你是 AI 行业日报的选稿编辑。对给出的每条素材打一个 0-10 的分数，衡量它"
    "是否值得出现在当天的 AI 快讯里：10 = 所有读者都该知道的大事，"
    "7-9 = 多数读者有价值，4-6 = 边缘或过于琐碎，0-3 = 与 AI 圈无关、"
    "纯营销、多主题早报汇总、手机/汽车/航天等非 AI 新闻，或纯闲聊。"
    "只有标题、没有任何正文细节的素材（summary 为空或极短）最多打 4 分："
    "没有具体信息量，写不成有内容的快讯。"
    "只依据素材本身判断，不要发明素材里没有的信息。"
    "严格返回 JSON 对象 {\"scores\":[{\"index\":1,\"score\":7,\"reason\":\"一句话理由\"}]}，"
    "scores 必须覆盖每一个 index，不得增删字段。"
)


def _strip_code_fence(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text
    first_newline = text.find("\n")
    if first_newline == -1:
        return text
    body = text[first_newline + 1:]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.strip()


class ScoredCandidate:
    """A candidate plus its advisory score. Untouched items keep score None."""

    __slots__ = ("candidate", "score", "reason")

    def __init__(self, candidate: dict, score: float | None, reason: str = "") -> None:
        self.candidate = candidate
        self.score = score
        self.reason = reason


def score_candidates(
    candidates: list[dict],
    llm_config: LLMConfig,
    *,
    threshold: float = DEFAULT_SCORE_THRESHOLD,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    timeout: int = 90,
    client_factory: Callable[..., Any] | None = None,
) -> tuple[list[ScoredCandidate], int]:
    """Return (kept candidates in input order, drop count).

    Never raises: any LLM failure degrades to "keep everything" so a scoring
    outage can only widen the pool, never empty it.
    """
    scored = [ScoredCandidate(c, None, "") for c in candidates]
    if not candidates:
        return scored, 0
    if not llm_config.api_key:
        logger.info("Scoring LLM not configured; keeping all %d candidates", len(candidates))
        return scored, 0

    limited = candidates[:max_candidates]
    overflow = len(candidates) - len(limited)
    if overflow > 0:
        logger.warning(
            "Candidate pool %d exceeds scoring cap %d; last %d kept unscored",
            len(candidates), max_candidates, overflow,
        )

    payload = {
        "items": [
            {
                "index": index,
                "title": str(item.get("source_title") or item.get("title") or "")[:220],
                "summary": str(item.get("source_summary") or item.get("summary") or "")[:400],
            }
            for index, item in enumerate(limited, 1)
        ]
    }

    try:
        if client_factory is None:
            from openai import OpenAI

            client = OpenAI(
                api_key=llm_config.api_key,
                base_url=llm_config.base_url,
                timeout=timeout,
                max_retries=0,
            )
        else:
            client = client_factory(
                api_key=llm_config.api_key,
                base_url=llm_config.base_url,
                timeout=timeout,
                max_retries=0,
            )
        response = client.chat.completions.create(
            model=llm_config.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0,
            max_tokens=4000,
            response_format={"type": "json_object"},
            **structured_llm_request_options(llm_config),
        )
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise ValueError("scoring LLM response is empty")
        decoded = json.loads(_strip_code_fence(content))
        rows = decoded.get("scores") if isinstance(decoded, dict) else None
        if not isinstance(rows, list):
            raise ValueError("scoring LLM response has no scores list")
    except Exception as exc:
        logger.warning("Candidate scoring failed; keeping all candidates: %s", exc)
        return scored, 0

    by_index: dict[int, tuple[float, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        index = row.get("index")
        score = row.get("score")
        if not isinstance(index, int) or isinstance(index, bool):
            continue
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            continue
        by_index[index] = (float(score), str(row.get("reason") or "")[:120])

    for position, entry in enumerate(scored[: len(limited)], 1):
        hit = by_index.get(position)
        if hit is None:
            # Unscored items survive: the scorer must never silently drop news.
            entry.score = None
            entry.reason = "unscored_kept"
            continue
        entry.score, entry.reason = hit

    kept: list[ScoredCandidate] = []
    dropped = 0
    for position, entry in enumerate(scored, 1):
        if position <= len(limited) and entry.score is not None and entry.score < threshold:
            dropped += 1
            continue
        kept.append(entry)
    if dropped:
        logger.info(
            "Scoring dropped %d candidate(s) below threshold %.1f (kept %d)",
            dropped, threshold, len(kept),
        )
    return kept, dropped


def scoring_diagnostics(
    scored: Iterable[ScoredCandidate],
    *,
    dropped: int = 0,
) -> dict[str, Any]:
    """Aggregate counters for the private audit, mirroring collection diagnostics."""
    items = list(scored)
    judged = [s for s in items if s.score is not None]
    return {
        "scoring_total": len(items),
        "scoring_judged": len(judged),
        "scoring_unscored_kept": len(items) - len(judged),
        "scoring_dropped": dropped,
        "scoring_avg_score": (
            round(sum(s.score for s in judged) / len(judged), 2) if judged else None
        ),
    }
