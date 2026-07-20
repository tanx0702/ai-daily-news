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


def _event_key(item: dict) -> str:
    editorial = item.get("_editorial") or {}
    key = str(editorial.get("event_key") or "").strip()
    return key.lower() if key else _topic_key(item)


def _ordered(items: Iterable[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda item: (
            (item.get("_editorial") or {}).get("score", 0),
            item.get("_score", 0),
        ),
        reverse=True,
    )


def _has_explainable_github_release(item: dict) -> bool:
    evidence = item.get("github_evidence") or {}
    project_description = str(evidence.get("project_description") or "").strip()
    release_notes = str(evidence.get("release_notes") or "").strip()
    return len(project_description) >= 24 and len(release_notes) >= 40


def _is_community_radar(item: dict) -> bool:
    """Keep unverified community activity available as a reserve only."""
    source_type = str(item.get("source_type") or "").lower()
    editorial = item.get("_editorial") or {}
    event_type = str(editorial.get("event_type") or "")

    if source_type == "github":
        return event_type != "github_release" or not _has_explainable_github_release(item)
    if source_type == "hn":
        metrics = item.get("metrics") or {}
        return not bool(metrics.get("cross_source_count"))
    return item.get("source_tier") == "community"


def _editorial_score(item: dict) -> float:
    return float((item.get("_editorial") or {}).get("score", 0) or 0)


def select_editorial_candidates(
    items: list[dict],
    target_count: int,
    pool_size: int | None = None,
    *,
    max_items_per_source: int = 2,
    max_items_per_topic: int = 2,
    min_primary_or_research: int = 2,
    max_items_per_event: int = 1,
) -> tuple[list[dict], list[dict], dict]:
    """Select a diverse final list and preserve remaining eligible items as reserves."""
    pool = _ordered(items)[:pool_size] if pool_size else _ordered(items)
    selected: list[dict] = []
    source_counts: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()
    event_counts: Counter[str] = Counter()

    def can_select(item: dict, *, source_cap: int) -> bool:
        source = str(item.get("source", "")).strip().lower()
        if source and source_counts[source] >= source_cap:
            return False
        topic = _topic_key(item)
        if topic and topic_counts[topic] >= max_items_per_topic:
            return False
        event = _event_key(item)
        if event and event_counts[event] >= max_items_per_event:
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
        event = _event_key(item)
        if event:
            event_counts[event] += 1

    for item in pool:
        if len(selected) >= target_count:
            break
        if (
            not _is_community_radar(item)
            and item.get("source_tier") in {"primary", "research"}
            and can_select(item, source_cap=max_items_per_source)
        ):
            select(item)
            if sum(
                candidate.get("source_tier") in {"primary", "research"}
                for candidate in selected
            ) >= min_primary_or_research:
                break

    for item in pool:
        if len(selected) >= target_count:
            break
        if (
            item in selected
            or _is_community_radar(item)
            or not can_select(item, source_cap=max_items_per_source)
        ):
            continue
        select(item)

    # 发布候选宁可缩短，也不能为了凑满日报而破坏来源或主题上限。
    cap_relaxed = False
    soft_source_cap = max(max_items_per_source, target_count // 2)
    for item in pool:
        if len(selected) >= target_count:
            break
        if (
            item in selected
            or _is_community_radar(item)
            or _editorial_score(item) < 8.5
            or not can_select(item, source_cap=soft_source_cap)
        ):
            continue
        select(item)
        cap_relaxed = True

    selected_ids = {id(item) for item in selected}
    reserves = [item for item in pool if id(item) not in selected_ids]
    final_source_counts: Counter[str] = Counter()
    final_topic_counts: Counter[str] = Counter()
    final_event_counts: Counter[str] = Counter()
    for item in selected:
        source = str(item.get("source", "")).strip().lower()
        if source:
            final_source_counts[source] += 1
        topic = _topic_key(item)
        if topic:
            final_topic_counts[topic] += 1
        event = _event_key(item)
        if event:
            final_event_counts[event] += 1
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
        "event_counts": dict(final_event_counts),
        "cap_relaxed": cap_relaxed,
        "community_radar_excluded_count": sum(
            _is_community_radar(item) for item in pool
        ),
        "insufficient_target": len(selected) < target_count,
    }
    return selected, reserves, report
