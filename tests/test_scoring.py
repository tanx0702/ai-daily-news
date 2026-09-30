"""Tests for the LLM scoring filter (route 1)."""
import json

from src.briefing.scoring import (
    ScoredCandidate,
    score_candidates,
    scoring_diagnostics,
)
from src.llm_config import LLMConfig

CFG = LLMConfig("key", "test-model", "http://llm.example/v1")


class _Completions:
    def __init__(self, content):
        self._content = content

    def create(self, **kwargs):
        return type("R", (), {"choices": [type(
            "C", (), {"message": type("M", (), {"content": self._content})()})()]})()


class _Client:
    def __init__(self, content):
        self.chat = type("C", (), {})()
        self.chat.completions = _Completions(content)


def _factory(content):
    return lambda **kwargs: _Client(content)


def _payload(scores):
    return json.dumps({"scores": scores}, ensure_ascii=False)


def test_scoring_drops_low_scores_and_keeps_high():
    cands = [
        {"source_title": "OpenAI 发布 GPT-6.1 Sol", "source_summary": "s"},
        {"source_title": "早报｜iPhone/华为/智界RX", "source_summary": "s"},
        {"source_title": "Claude 发现噬菌体 DNA 中的未知酶系统", "source_summary": "s"},
    ]
    content = _payload([
        {"index": 1, "score": 8, "reason": "major"},
        {"index": 2, "score": 2, "reason": "digest"},
        {"index": 3, "score": 9, "reason": "research"},
    ])

    kept, dropped = score_candidates(
        cands, CFG, threshold=5, client_factory=_factory(content)
    )

    titles = [s.candidate["source_title"] for s in kept]
    assert "早报｜iPhone/华为/智界RX" not in titles
    assert len(kept) == 2
    assert dropped == 1
    assert all(s.score >= 5 for s in kept)


def test_scoring_failure_keeps_every_candidate():
    """A scoring outage widens the pool; it must never empty it."""

    def boom(**kwargs):
        raise TimeoutError("scoring llm down")

    cands = [{"source_title": "A", "source_summary": "s"},
             {"source_title": "B", "source_summary": "s"}]
    kept, dropped = score_candidates(cands, CFG, threshold=5, client_factory=boom)

    assert len(kept) == 2
    assert dropped == 0
    assert all(s.score is None for s in kept)


def test_fenced_json_reply_is_still_parsed():
    cands = [{"source_title": "T", "source_summary": "s"}]
    fenced = "```json\n%s\n```" % _payload([{"index": 1, "score": 7, "reason": "ok"}])

    kept, dropped = score_candidates(cands, CFG, threshold=5, client_factory=_factory(fenced))

    assert len(kept) == 1 and dropped == 0 and kept[0].score == 7


def test_unscored_items_are_kept_not_dropped():
    """The scorer must never silently drop news it did not judge."""
    cands = [{"source_title": "A", "source_summary": "s"},
             {"source_title": "B", "source_summary": "s"}]
    content = _payload([{"index": 1, "score": 9, "reason": "ok"}])

    kept, dropped = score_candidates(cands, CFG, threshold=5, client_factory=_factory(content))

    assert len(kept) == 2 and dropped == 0
    assert kept[1].score is None and kept[1].reason == "unscored_kept"


def test_no_api_key_keeps_everything_without_calling():
    cands = [{"source_title": "A", "source_summary": "s"}]
    called = {"n": 0}

    def factory(**kwargs):
        called["n"] += 1
        raise AssertionError("must not call the LLM")

    cfg = LLMConfig("", "m", "http://x/v1")
    kept, dropped = score_candidates(cands, cfg, client_factory=factory)

    assert len(kept) == 1 and dropped == 0 and called["n"] == 0


def test_diagnostics_report_the_true_drop_count():
    cands = [
        {"source_title": "A", "source_summary": "s"},
        {"source_title": "B", "source_summary": "s"},
    ]
    content = _payload([
        {"index": 1, "score": 9, "reason": "ok"},
        {"index": 2, "score": 1, "reason": "noise"},
    ])
    kept, dropped = score_candidates(cands, CFG, threshold=5, client_factory=_factory(content))
    diag = scoring_diagnostics(kept, dropped=dropped)

    assert diag["scoring_total"] == 1
    assert diag["scoring_dropped"] == 1
    assert diag["scoring_judged"] == 1
    assert diag["scoring_avg_score"] == 9
