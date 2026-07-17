"""Immutable source evidence helpers for the editorial pipeline."""


def preserve_source_evidence(item: dict) -> dict:
    """Capture collected source fields once before generated fields can replace them."""
    evidence_fields = {
        "source_title": item.get("title", ""),
        "source_summary": item.get("summary", ""),
        "source_url": item.get("url", ""),
        "source_name": item.get("source", ""),
        "source_published_at": item.get("published_at"),
    }
    for field, value in evidence_fields.items():
        item.setdefault(field, value)
    return item


def source_evidence_text(item: dict) -> str:
    """Return the source material available for a generated-text review."""
    fields = ("source_title", "source_summary", "source_excerpt")
    return "\n".join(
        str(item.get(field, "")).strip()
        for field in fields
        if str(item.get(field, "")).strip()
    )
