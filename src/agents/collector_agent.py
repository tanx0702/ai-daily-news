"""Adapt the existing v1 collection pipeline into typed v2 candidates."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from types import MappingProxyType
from typing import Any, Callable, Mapping

from src.domain.models import CollectionDiagnostics, NewsCandidate, SourceEvidence


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


class CollectorAgent:
    """Run the proven v1 collector and expose immutable candidate snapshots."""

    def __init__(
        self,
        *,
        collect_news: Callable[..., list[dict]] | None = None,
        annotate_candidates: Callable[[list[dict]], list[dict]] | None = None,
    ) -> None:
        if collect_news is None:
            from src.collector import collect_news as default_collect_news

            collect_news = default_collect_news
        if annotate_candidates is None:
            from src.editorial_quality import annotate_editorial_candidates as default_annotate

            annotate_candidates = default_annotate

        self._collect_news = collect_news
        self._annotate_candidates = annotate_candidates

    def collect(self, *, top_n: int = 30, rss_timeout: int = 30) -> tuple[NewsCandidate, ...]:
        """Collect, annotate, and convert current v1 candidates for v2 consumers."""
        items = self._collect_items(top_n=top_n, rss_timeout=rss_timeout)
        return self._adapt_items(items)

    def collect_with_diagnostics(
        self,
        *,
        top_n: int = 30,
        rss_timeout: int = 30,
    ) -> tuple[tuple[NewsCandidate, ...], CollectionDiagnostics]:
        """Return v2 candidates together with opt-in v1 collection statistics."""
        raw_diagnostics: dict[str, object] = {}
        items = self._collect_items(
            top_n=top_n,
            rss_timeout=rss_timeout,
            diagnostics=raw_diagnostics,
        )
        candidates = self._adapt_items(items)
        raw_diagnostics["returned_candidate_count"] = len(candidates)
        return candidates, CollectionDiagnostics.from_mapping(raw_diagnostics)

    def _collect_items(
        self,
        *,
        top_n: int,
        rss_timeout: int,
        diagnostics: dict[str, object] | None = None,
    ) -> list[dict]:
        if diagnostics is None:
            return list(self._collect_news(top_n=top_n, rss_timeout=rss_timeout))
        return list(
            self._collect_news(
                top_n=top_n,
                rss_timeout=rss_timeout,
                diagnostics=diagnostics,
            )
        )

    def _adapt_items(self, items: list[dict]) -> tuple[NewsCandidate, ...]:
        if not items:
            return ()

        self._annotate_candidates(items)
        return tuple(self._to_candidate(item, index) for index, item in enumerate(items, start=1))

    @staticmethod
    def _to_candidate(item: dict, index: int) -> NewsCandidate:
        published_at = item.get("published_at")
        evidence = SourceEvidence(
            title=str(item.get("source_title") or item.get("title") or ""),
            summary=str(item.get("source_summary") or item.get("summary") or ""),
            url=str(item.get("source_url") or item.get("url") or ""),
            source=str(item.get("source") or ""),
            source_type=str(item.get("source_type") or "rss"),
            published_at=published_at if isinstance(published_at, datetime) else None,
        )
        candidate_id = str(item.get("id") or item.get("url") or f"candidate-{index}")
        return NewsCandidate(
            candidate_id=candidate_id,
            evidence=evidence,
            source_tier=str(item.get("source_tier") or "media"),
            legacy_payload=_freeze(deepcopy(item)),
        )
