"""Source-aware editorial selection for daily news candidates."""

from collections import Counter
from typing import Iterable


_SOURCE_TYPE_TIERS = {
    "arxiv": "research",
    "hn": "community",
    "github": "community",
    "huggingface": "community",
}


def assign_source_tier(item: dict, source_config: dict | None = None) -> dict:
    """Attach a stable editorial tier without replacing an explicit assignment."""
    if item.get("source_tier"):
        return item

    if source_config and source_config.get("tier"):
        item["source_tier"] = source_config["tier"]
    else:
        item["source_tier"] = _SOURCE_TYPE_TIERS.get(
            item.get("source_type", "rss"),
            "media",
        )
    return item


def _topic_key(item: dict) -> str:
    key = str(item.get("topic_key") or item.get("_topic_key") or "").strip()
    if key:
        return key.lower()
    return str(item.get("title", "")).strip().lower()


def _ordered(items: Iterable[dict]) -> list[dict]:
    return sorted(items, key=lambda item: item.get("_score", 0), reverse=True)


def select_editorial_candidates(
    items: list[dict],
    target_count: int,
    pool_size: int | None = None,
    *,
    max_items_per_source: int = 2,
    max_items_per_topic: int = 2,
    min_primary_or_research: int = 2,
) -> tuple[list[dict], list[dict], dict]:
    """Select a diverse final list and preserve remaining eligible items as reserves."""
    pool = _ordered(items)[:pool_size] if pool_size else _ordered(items)
    selected: list[dict] = []
    source_counts: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()

    def can_select(item: dict) -> bool:
        source = str(item.get("source", "")).strip().lower()
        if source and source_counts[source] >= max_items_per_source:
            return False
        topic = _topic_key(item)
        if topic and topic_counts[topic] >= max_items_per_topic:
            return False
        return True

    def select(item: dict) -> None:
        selected.append(item)
        source = str(item.get("source", "")).strip().lower()
        if source:
            source_counts[source] += 1
        topic = _topic_key(item)
        if topic:
            topic_counts[topic] += 1

    for item in pool:
        if len(selected) >= target_count:
            break
        if item.get("source_tier") in {"primary", "research"} and can_select(item):
            select(item)
            if sum(
                candidate.get("source_tier") in {"primary", "research"}
                for candidate in selected
            ) >= min_primary_or_research:
                break

    for item in pool:
        if len(selected) >= target_count:
            break
        if item in selected or not can_select(item):
            continue
        select(item)

    # 发布候选宁可缩短，也不能为了凑满日报而破坏来源或主题上限。
    cap_relaxed = False

    selected_ids = {id(item) for item in selected}
    reserves = [item for item in pool if id(item) not in selected_ids]
    final_source_counts: Counter[str] = Counter()
    final_topic_counts: Counter[str] = Counter()
    for item in selected:
        source = str(item.get("source", "")).strip().lower()
        if source:
            final_source_counts[source] += 1
        topic = _topic_key(item)
        if topic:
            final_topic_counts[topic] += 1
    primary_or_research_count = sum(
        item.get("source_tier") in {"primary", "research"} for item in selected
    )
    report = {
        "pool_count": len(pool),
        "selected_count": len(selected),
        "reserve_count": len(reserves),
        "primary_or_research_count": primary_or_research_count,
        "source_counts": dict(final_source_counts),
        "topic_counts": dict(final_topic_counts),
        "cap_relaxed": cap_relaxed,
        "insufficient_target": len(selected) < target_count,
    }
    return selected, reserves, report
