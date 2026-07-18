"""Publish-readiness checks shared by the pipeline and operational surfaces."""

from collections import Counter


MIN_PUBLISHABLE_ITEMS = 6
MAX_SOURCE_SHARE = 0.5


def evaluate_publish_readiness(news_list: list[dict], quality_report: dict | None) -> dict:
    """Return a stable decision for whether a WeChat draft may be created."""
    quality_report = quality_report or {}
    selected_count = len(news_list)
    source_counts = Counter(
        str(item.get("source") or "unknown") for item in news_list
    )
    reasons: list[str] = []

    if selected_count < MIN_PUBLISHABLE_ITEMS:
        reasons.append("insufficient_items")

    if any(item.get("quality_state", "ready") != "ready" for item in news_list):
        reasons.append("non_ready_item")

    if selected_count and max(source_counts.values(), default=0) / selected_count > MAX_SOURCE_SHARE:
        reasons.append("source_concentration")

    review_status = quality_report.get("llm_review_status", "skipped")
    if review_status == "failed":
        reasons.append("quality_review_failed")
    elif review_status != "passed":
        reasons.append("quality_review_not_passed")

    if quality_report.get("risk_level") == "high":
        reasons.append("high_publish_risk")

    return {
        "ready": not reasons,
        "reasons": reasons,
        "selected_count": selected_count,
        "minimum_items": MIN_PUBLISHABLE_ITEMS,
        "source_counts": dict(source_counts),
        "max_source_share": MAX_SOURCE_SHARE,
        "quality_review_status": review_status,
    }
