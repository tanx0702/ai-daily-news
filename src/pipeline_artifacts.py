"""Pipeline artifact helpers for generated daily report files."""

import json
import os
from datetime import datetime
from typing import Any, Callable

from src.file_utils import atomic_write_text
from src.generator import render_daily_html, render_wechat_article, save_html


def collect_archive_links(docs_dir: str, pages_url: str) -> list[str]:
    """Return archive links sorted newest first."""
    archive_dir = os.path.join(docs_dir, "archive")
    if not os.path.isdir(archive_dir):
        return []

    links: list[str] = []
    for fname in sorted(os.listdir(archive_dir), reverse=True):
        if fname.endswith(".html"):
            links.append(f"{pages_url}/archive/{fname}")
    return links


def render_and_save_daily_html(
    news_list: list[dict],
    date_str: str,
    docs_dir: str,
    pages_url: str,
    github_repo: str,
) -> None:
    """Render index.html and its dated archive copy."""
    archive_links = collect_archive_links(docs_dir, pages_url)
    html = render_daily_html(
        news_list,
        date_str,
        archive_links,
        github_repo=github_repo,
    )

    os.makedirs(docs_dir, exist_ok=True)
    save_html(html, os.path.join(docs_dir, "index.html"))

    archive_dir = os.path.join(docs_dir, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    save_html(html, os.path.join(archive_dir, f"{date_str}.html"))


def render_and_save_wechat_preview(
    news_list: list[dict],
    date_str: str,
    docs_dir: str,
    pages_url: str,
) -> str:
    """Render docs/wechat.html and return its public URL."""
    cover_url = f"{pages_url}/cover.jpg"
    wechat_html = render_wechat_article(
        news_list,
        date_str,
        pages_url,
        cover_image_url=cover_url,
    )
    save_html(wechat_html, os.path.join(docs_dir, "wechat.html"))
    return f"{pages_url}/wechat.html"


def build_latest_data(
    news_list: list[dict],
    date_str: str,
    pages_url: str,
    generated_at: str,
    quality_report: dict | None = None,
    cover_subject: dict | None = None,
    media_report: dict | None = None,
    selection_report: dict | None = None,
    source_health: dict | None = None,
    publication: dict | None = None,
) -> dict[str, Any]:
    """Build the JSON payload consumed by Flask and debug tools."""
    latest_data: dict[str, Any] = {
        "date": date_str,
        "news": news_list,
        "pages_url": pages_url,
        "cover_image_url": f"{pages_url}/cover.jpg",
        "wechat_preview_url": f"{pages_url}/wechat.html",
        "generated_at": generated_at,
    }

    if quality_report:
        latest_data["quality_gate"] = {
            "pass": quality_report.get("pass"),
            "risk_level": quality_report.get("risk_level"),
            "blocked_publish": quality_report.get("blocked_publish"),
            "issues_count": len(quality_report.get("issues", [])),
            "fixes_count": len(quality_report.get("applied_fixes", [])),
            "llm_review_status": quality_report.get("llm_review_status", "skipped"),
            "publish_filter": quality_report.get("publish_filter", {}),
        }

    if cover_subject:
        latest_data["cover_subject"] = {
            "mode": cover_subject.get("mode"),
            "title": cover_subject.get("cover_title", ""),
            "headline": cover_subject.get("cover_headline", ""),
            "reason": cover_subject.get("reason", ""),
            "story_type": cover_subject.get("story_type", ""),
            "cover_source": cover_subject.get("cover_source", ""),
            "matches_top1": bool(
                cover_subject.get("item") is news_list[0]
                if news_list and cover_subject.get("item")
                else False
            ),
        }

    if media_report:
        latest_data["media"] = {
            "total": media_report.get("total", 0),
            "with_original_image": media_report.get("with_original_image", 0),
            "text_only": media_report.get("text_only", 0),
            "trusted": sum(
                1 for item in media_report.get("items", [])
                if item.get("media_state") == "trusted"
            ),
            "rejected": sum(
                1 for item in media_report.get("items", [])
                if item.get("media_state") == "rejected"
            ),
        }

    if selection_report or source_health:
        latest_data["diagnostics"] = {
            "editorial_selection": selection_report or {},
            "source_health": source_health or {},
        }

    if publication is not None:
        latest_data["publication"] = publication

    return latest_data


def save_latest_data(
    latest_data: dict[str, Any],
    docs_dir: str,
    default: Callable[[Any], Any] | None = None,
) -> str:
    """Save latest.json and return the path."""
    latest_path = os.path.join(docs_dir, "latest.json")
    content = json.dumps(latest_data, ensure_ascii=False, indent=2, default=default)
    atomic_write_text(latest_path, content)
    return latest_path


def json_serial(obj: Any) -> str:
    """JSON serialization helper for datetime fields."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")
