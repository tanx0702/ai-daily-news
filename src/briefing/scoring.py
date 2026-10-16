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
    "孤立的零碎小事件（影响面小、只值得爱好者一看，如某工具小版本更新、"
    "转述他人观点、娱乐性边缘消息）不超过 5 分。"
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


class ScoringOutcome:
    """Scoring result: the survivors plus the whole-pool counters.

    ``kept`` is what the pipeline continues with. ``pool`` is every candidate the
    scorer saw (kept + dropped); diagnostics must be computed from it so
    ``scoring_total`` stays comparable with ``scoring_dropped`` instead of only
    counting survivors. Unpacks as ``(kept, dropped)`` for callers that only need
    the survivors.
    """

    __slots__ = ("kept", "dropped", "pool")

    def __init__(
        self,
        *,
        kept: list["ScoredCandidate"],
        dropped: int,
        pool: list["ScoredCandidate"],
    ) -> None:
        self.kept = kept
        self.dropped = dropped
        self.pool = pool

    def __iter__(self):
        yield self.kept
        yield self.dropped

    def diagnostics(self) -> dict[str, Any]:
        return scoring_diagnostics(self.pool, dropped=self.dropped)


def _score_batch(
    batch: list[dict],
    llm_config: LLMConfig,
    *,
    timeout: int,
    client_factory: Callable[..., Any] | None,
) -> list[dict] | None:
    """Score one batch; return the raw score rows or None on any failure."""
    payload = {
        "items": [
            {
                "index": index,
                "title": str(item.get("source_title") or item.get("title") or "")[:220],
                "summary": str(item.get("source_summary") or item.get("summary") or "")[:200],
            }
            for index, item in enumerate(batch, 1)
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
        return rows
    except Exception as exc:
        logger.warning("Scoring batch of %d failed; keeping that batch unscored: %s",
                       len(batch), exc)
        return None


def score_candidates(
    candidates: list[dict],
    llm_config: LLMConfig,
    *,
    threshold: float = DEFAULT_SCORE_THRESHOLD,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    timeout: int = 90,
    client_factory: Callable[..., Any] | None = None,
) -> ScoringOutcome:
    """Score the pool and return a :class:`ScoringOutcome`.

    The result unpacks as ``(kept, dropped)`` for callers that only need the
    survivors; ``outcome.diagnostics()`` reports counters over the whole pool.

    Never raises: any LLM failure degrades to "keep everything" so a scoring
    outage can only widen the pool, never empty it.
    """
    scored = [ScoredCandidate(c, None, "") for c in candidates]
    if not candidates:
        return ScoringOutcome(kept=scored, dropped=0, pool=scored)
    if not llm_config.api_key:
        logger.info("Scoring LLM not configured; keeping all %d candidates", len(candidates))
        return ScoringOutcome(kept=scored, dropped=0, pool=scored)

    limited = candidates[:max_candidates]
    overflow = len(candidates) - len(limited)
    if overflow > 0:
        logger.warning(
            "Candidate pool %d exceeds scoring cap %d; last %d kept unscored",
            len(candidates), max_candidates, overflow,
        )

    # Batch by character budget, not item count: long summaries make 25-item
    # batches exceed max_tokens=4000 in the reply (2026-10-13: fixed 25 still
    # truncated, "Expecting ',' delimiter"). The reply is ~40 chars per item
    # ({"index":N,"score":X,"reason":"..."}), the request items up to ~300
    # chars each; a 9000-char request budget keeps every reply comfortably
    # under the cap while adapting to item size.
    BUDGET_CHARS = 9000
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_size = 0
    for item in limited:
        size = min(len(str(item.get("source_title") or item.get("title") or "")), 220) \
            + min(len(str(item.get("source_summary") or item.get("summary") or "")), 200)
        if current and current_size + size > BUDGET_CHARS:
            batches.append(current)
            current, current_size = [], 0
        current.append(item)
        current_size += size
    if current:
        batches.append(current)

    by_index: dict[int, tuple[float, str]] = {}
    parse_failures = 0
    for batch in batches:
        rows = _score_batch(batch, llm_config, timeout=timeout, client_factory=client_factory)
        if rows is None:
            parse_failures += 1
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            index = row.get("index")
            score = row.get("score")
            if not isinstance(index, int) or isinstance(index, bool):
                continue
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                continue
            # Batch-local index -> absolute position in the full pool.
            offset = (limited.index(batch[0]) if batch else 0)
            by_index[offset + index] = (float(score), str(row.get("reason") or "")[:120])
    if parse_failures == len(batches):
        # Every batch failed: treat as a scoring outage, keep everything. Mark the
        # items explicitly so callers can tell "never judged" from "judged"; the
        # collector must not vouch for an unjudged candidate.
        logger.warning("Candidate scoring failed for all batches; keeping all candidates")
        for entry in scored:
            entry.score = None
            entry.reason = "unscored_kept"
        return ScoringOutcome(kept=scored, dropped=0, pool=scored)

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
    # ``scored`` is the whole pool and ``kept`` is the survivor subset. Diagnostics
    # must describe the whole pool, otherwise ``scoring_total`` would only count
    # survivors and look inconsistent with the larger ``scoring_dropped``. Expose
    # the pool on the outcome so the caller never has to guess.
    return ScoringOutcome(kept=kept, dropped=dropped, pool=scored)


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
