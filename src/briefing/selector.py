"""Deterministic event queue for the fact brief production path."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Iterable

from src.briefing.config import BriefingConfig
from src.briefing.models import BriefItem, MergedEvent


_AUTHORITY_ORDER = {
    "official": 0,
    "research": 1,
    "professional_media": 2,
    "community": 3,
}


def _published_at(event: MergedEvent) -> datetime:
    return datetime.fromisoformat(
        event.canonical_evidence.published_at.replace("Z", "+00:00")
    )


def _topic_key(event: MergedEvent) -> str:
    for reason in event.rank_reasons:
        if reason.startswith("topic:"):
            return reason.removeprefix("topic:").strip().lower()
    return event.event_key


class BriefSelector:
    """Keep all independent events available while applying only soft preferences."""

    def __init__(
        self,
        events: Iterable[MergedEvent],
        config: BriefingConfig,
        *,
        quarantined_keys: Iterable[str] = (),
    ) -> None:
        self.config = config
        self.quarantined_keys = frozenset(str(key) for key in quarantined_keys)
        self._excluded: Counter[str] = Counter()
        by_key: dict[str, MergedEvent] = {}
        for event in events:
            if not event.event_key or event.event_key in self.quarantined_keys:
                continue
            if event.event_key in by_key:
                self._excluded["duplicate_event"] += 1
                continue
            by_key[event.event_key] = event
        self._events = tuple(self._order(by_key.values()))
        self._events_by_key = {event.event_key: event for event in self._events}
        self._accepted: list[BriefItem] = []
        self._processed: set[str] = set()
        if self.config.max_x_items == 0:
            for event in self._events:
                if event.canonical_evidence.channel == "x":
                    self._processed.add(event.event_key)
                    self._excluded["x_limit"] += 1

    @property
    def accepted_items(self) -> tuple[BriefItem, ...]:
        accepted_by_key = {item.event_key: item for item in self._accepted}
        return tuple(
            accepted_by_key[event.event_key]
            for event in self._events
            if event.event_key in accepted_by_key
        )

    @property
    def x_count(self) -> int:
        return sum(
            item.canonical_source.channel == "x" for item in self._accepted
        )

    @property
    def fact_count(self) -> int:
        return sum(item.content_type == "fact_event" for item in self._accepted)

    @property
    def opinion_count(self) -> int:
        return sum(
            item.content_type == "attributed_opinion" for item in self._accepted
        )

    @property
    def opinion_author_counts(self) -> Counter[str]:
        return Counter(
            item.opinion_author.strip().lower()
            for item in self._accepted
            if item.content_type == "attributed_opinion" and item.opinion_author.strip()
        )

    @property
    def excluded_counts(self) -> dict[str, int]:
        return dict(self._excluded)

    def pending(self) -> tuple[MergedEvent, ...]:
        """Return the remaining deterministic queue without deleting demoted events."""
        return tuple(
            event
            for event in self._events
            if event.event_key not in self._processed and self.can_attempt(event)
        )

    def can_attempt(self, event: MergedEvent) -> bool:
        if event.event_key in self.quarantined_keys:
            return False
        if (
            event.canonical_evidence.content_type == "attributed_opinion"
            and (
                self.opinion_count >= self.config.max_opinion_items
                or event.canonical_evidence.opinion_author.strip().lower()
                in self.opinion_author_counts
            )
        ):
            return False
        return not (
            event.canonical_evidence.channel == "x"
            and self.x_count >= self.config.max_x_items
        )

    def limit_reason(self, event: MergedEvent) -> str | None:
        source = event.canonical_evidence
        if source.channel == "x" and self.x_count >= self.config.max_x_items:
            return "x_limit"
        if source.content_type == "attributed_opinion":
            if self.opinion_count >= self.config.max_opinion_items:
                return "opinion_limit"
            author = source.opinion_author.strip().lower()
            if not author or author in self.opinion_author_counts:
                return "opinion_author_limit"
        return None

    def accept(self, item: BriefItem) -> bool:
        """Accept one validated item and consume its quota only at this boundary."""
        event = self._events_by_key.get(item.event_key)
        if event is None or item.event_key in self._processed:
            return False
        if event.canonical_evidence.channel == "x" and self.x_count >= self.config.max_x_items:
            return False
        if item.content_type == "attributed_opinion":
            author = item.opinion_author.strip().lower()
            if self.opinion_count >= self.config.max_opinion_items:
                self._excluded["opinion_limit"] += 1
                return False
            if not author or author in self.opinion_author_counts:
                self._excluded["opinion_author_limit"] += 1
                return False
        self._accepted.append(item)
        self._processed.add(item.event_key)
        return True

    def reject(self, event_key: str, reason_code: str) -> None:
        if event_key in self._events_by_key:
            self._processed.add(event_key)
        self._excluded[str(reason_code)] += 1

    def replace_accepted(
        self,
        item: BriefItem,
        *,
        remove_event_keys: Iterable[str],
        reason_code: str = "semantic_duplicate",
    ) -> bool:
        """Replace accepted duplicates atomically, preserving quotas and processed keys."""
        event = self._events_by_key.get(item.event_key)
        if event is None or item.event_key in self._processed:
            return False
        removed_keys = tuple(dict.fromkeys(str(key) for key in remove_event_keys))
        accepted_by_key = {value.event_key: value for value in self._accepted}
        if any(key not in accepted_by_key for key in removed_keys):
            return False
        projected = [
            value for value in self._accepted if value.event_key not in removed_keys
        ]
        if event.canonical_evidence.channel == "x" and sum(
            value.canonical_source.channel == "x" for value in projected
        ) >= self.config.max_x_items:
            self._excluded["x_limit"] += 1
            self._processed.add(item.event_key)
            return False
        projected_opinions = [
            value for value in projected
            if value.content_type == "attributed_opinion"
        ]
        if item.content_type == "attributed_opinion":
            author = item.opinion_author.strip().lower()
            if len(projected_opinions) >= self.config.max_opinion_items:
                self._excluded["opinion_limit"] += 1
                return False
            if not author or any(
                value.opinion_author.strip().lower() == author
                for value in projected_opinions
            ):
                self._excluded["opinion_author_limit"] += 1
                return False
        self._accepted = projected + [item]
        self._processed.update(removed_keys)
        self._processed.add(item.event_key)
        self._excluded[str(reason_code)] += len(removed_keys)
        return True

    def remove_accepted(
        self,
        *,
        remove_event_keys: Iterable[str],
        reason_code: str = "semantic_duplicate",
    ) -> bool:
        """Remove accepted duplicates atomically without accepting a replacement."""
        removed_keys = tuple(dict.fromkeys(str(key) for key in remove_event_keys))
        accepted_by_key = {value.event_key: value for value in self._accepted}
        if not removed_keys or any(key not in accepted_by_key for key in removed_keys):
            return False
        self._accepted = [
            value for value in self._accepted if value.event_key not in removed_keys
        ]
        self._processed.update(removed_keys)
        self._excluded[str(reason_code)] += len(removed_keys)
        return True

    def _order(self, events: Iterable[MergedEvent]) -> list[MergedEvent]:
        ranked = sorted(
            events,
            key=lambda event: (
                -event.editorial_score,
                _AUTHORITY_ORDER.get(event.canonical_evidence.authority, 99),
                -_published_at(event).timestamp(),
                event.event_key,
            ),
        )
        # Preferences demote repeated publishers/topics behind alternatives. They
        # never remove an otherwise independent event from the queue.
        selected: list[MergedEvent] = []
        remaining = list(ranked)
        source_counts: Counter[str] = Counter()
        topic_counts: Counter[str] = Counter()
        primary_count = 0
        has_primary_or_research = any(
            event.canonical_evidence.authority in {"official", "research"}
            for event in remaining
        )
        while remaining:
            preferred_index = next(
                (
                    index
                    for index, event in enumerate(remaining)
                    if self._is_preferred(
                        event,
                        source_counts,
                        topic_counts,
                        primary_count,
                        has_primary_or_research,
                    )
                ),
                0,
            )
            event = remaining.pop(preferred_index)
            selected.append(event)
            source_counts[event.canonical_evidence.publisher_id] += 1
            topic_counts[_topic_key(event)] += 1
            if event.canonical_evidence.authority in {"official", "research"}:
                primary_count += 1
        return selected

    def _is_preferred(
        self,
        event: MergedEvent,
        source_counts: Counter[str],
        topic_counts: Counter[str],
        primary_count: int,
        has_primary_or_research: bool,
    ) -> bool:
        source = event.canonical_evidence.publisher_id
        topic = _topic_key(event)
        is_primary = event.canonical_evidence.authority in {"official", "research"}
        if (
            has_primary_or_research
            and primary_count < self.config.min_primary_or_research
            and not is_primary
        ):
            return False
        return (
            source_counts[source] < self.config.max_items_per_source
            and topic_counts[topic] < self.config.max_items_per_topic
        )
