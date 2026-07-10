"""
正文媒体资源管理模块

职责：
- 为新闻条目尝试获取原文配图（og:image / twitter:image）
- 不生成任何正文占位图
- 写入每条新闻的图片状态（original / text_only），供渲染层选择卡片样式
- 记录图片来源和失败原因
- 生成 debug 报告
"""

import html as _html
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# 图片抓取超时秒数
DEFAULT_IMAGE_TIMEOUT = 8

# 已知无图的来源（天然少图，标记 text_only 不尝试抓取）
_TEXT_ONLY_SOURCES = {"hacker news", "hn", "arxiv", "github", "hugging face", "huggingface"}


def _fetch_og_image(article_url: str, timeout: int = 8) -> Optional[str]:
    """
    抓取文章页面的 og:image 或 twitter:image。

    只读前 100KB HTML，不下载整页。
    过滤明显太小的图标/logo。
    """
    try:
        resp = requests.get(
            article_url,
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return None

        html_text = resp.text[:100000]

        # og:image（优先）
        m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            html_text, re.IGNORECASE,
        )
        if not m:
            # twitter:image（备选）
            m = re.search(
                r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
                html_text, re.IGNORECASE,
            )
        if m:
            img_url = m.group(1)
            # 过滤明显太小的图标/logo
            if any(kw in img_url.lower() for kw in ["favicon", "icon-32", "logo-"]):
                return None
            # 处理相对路径
            if img_url.startswith("/"):
                from urllib.parse import urljoin
                img_url = urljoin(article_url, img_url)
            return img_url
    except Exception:
        pass
    return None


def _is_text_only_source(source: str, source_type: str = "") -> bool:
    """判断来源是否天然无图。"""
    source_lower = source.lower().strip()
    for ts in _TEXT_ONLY_SOURCES:
        if ts in source_lower:
            return True
    if source_type in ("hn", "github", "huggingface", "arxiv"):
        return True
    return False


def resolve_article_media(
    news_list: list[dict],
    *,
    docs_dir: str = "",
    pages_url: str = "",
    date_str: str = "",
    timeout: int = 8,
) -> tuple[list[dict], dict]:
    """
    为每条新闻解析图片状态。

    优先级：
    1. 已有 article_image_url → image_type="original"
    2. 抓取原文 og:image / twitter:image → 成功则 image_type="original"
    3. 无图或失败 → image_type="text_only"（不生成本地占位图）

    Args:
        news_list: 新闻列表
        docs_dir: docs 目录路径（用于 debug 报告）
        pages_url: 日报 URL
        date_str: 日期字符串
        timeout: 单条图片抓取超时秒数

    Returns:
        (news_list, media_report)
    """
    if not news_list:
        return news_list, {
            "date": date_str,
            "total": 0,
            "with_original_image": 0,
            "text_only": 0,
            "items": [],
        }

    items_report: list[dict] = []

    def _process_one(idx: int, item: dict) -> dict:
        title = (item.get("chinese_title") or item.get("title", ""))[:60]
        source = item.get("source", "")
        source_type = item.get("source_type", "")
        url = item.get("url", "")
        existing_img = item.get("article_image_url", "")

        # 已有图片 → 直接标记
        if existing_img:
            item["image_type"] = "original"
            item["image_reason"] = "existing article_image_url"
            return {
                "title": title,
                "image_type": "original",
                "image_url": existing_img[:100],
                "reason": "existing article_image_url",
            }

        # 天然无图来源 → 标记 text_only
        if _is_text_only_source(source, source_type):
            item["image_type"] = "text_only"
            item["image_reason"] = f"source usually text-only ({source_type or source})"
            item["article_image_url"] = ""
            return {
                "title": title,
                "image_type": "text_only",
                "image_url": "",
                "reason": f"source usually text-only ({source_type or source})",
            }

        # 尝试抓取 og:image
        if url:
            og_img = _fetch_og_image(url, timeout=timeout)
            if og_img:
                item["image_type"] = "original"
                item["image_reason"] = "og:image found"
                item["article_image_url"] = og_img
                logger.info("Media #%d: og:image found for '%s'", idx, title[:30])
                return {
                    "title": title,
                    "image_type": "original",
                    "image_url": og_img[:100],
                    "reason": "og:image found",
                }

        # 无图
        item["image_type"] = "text_only"
        item["image_reason"] = "no og:image found"
        item["article_image_url"] = ""
        return {
            "title": title,
            "image_type": "text_only",
            "image_url": "",
            "reason": "no og:image found",
        }

    # 顺序处理（避免并发过载）
    for i, item in enumerate(news_list):
        try:
            report = _process_one(i, item)
            items_report.append(report)
        except Exception as e:
            logger.warning("Media #%d: failed to process: %s", i, e)
            item["image_type"] = "text_only"
            item["image_reason"] = f"process error: {e}"
            item["article_image_url"] = ""
            items_report.append({
                "title": (item.get("chinese_title") or item.get("title", ""))[:60],
                "image_type": "text_only",
                "image_url": "",
                "reason": f"process error: {e}",
            })

    with_original = sum(1 for it in items_report if it["image_type"] == "original")
    text_only = sum(1 for it in items_report if it["image_type"] == "text_only")

    media_report = {
        "date": date_str,
        "total": len(news_list),
        "with_original_image": with_original,
        "text_only": text_only,
        "items": items_report,
    }

    logger.info(
        "Media resolution done: %d original images, %d text-only cards",
        with_original, text_only,
    )

    # 保存 debug 报告
    if docs_dir:
        _save_visual_report(media_report, date_str, docs_dir)

    return news_list, media_report


def _save_visual_report(report: dict, date_str: str, docs_dir: str) -> None:
    """保存 visual.json 和 visual.md 到 docs/debug/。"""
    debug_dir = os.path.join(docs_dir, "debug")
    os.makedirs(debug_dir, exist_ok=True)

    # ── visual.json ──
    json_path = os.path.join(debug_dir, f"{date_str}-visual.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    logger.info("Visual report JSON saved to %s", json_path)

    # ── visual.md ──
    md_path = os.path.join(debug_dir, f"{date_str}-visual.md")
    lines = _build_visual_md(report, date_str)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info("Visual report MD saved to %s", md_path)


def _build_visual_md(report: dict, date_str: str) -> list[str]:
    """构建 visual.md。"""
    lines = [
        f"# Visual Quality Report — {date_str}",
        "",
        "## Summary",
        "",
        f"- **Total items**: {report.get('total', 0)}",
        f"- **Original images**: {report.get('with_original_image', 0)}",
        f"- **Text-only cards**: {report.get('text_only', 0)}",
        "",
    ]

    items = report.get("items", [])
    if items:
        lines.append("## Per-Item Image Status")
        lines.append("")
        for i, it in enumerate(items, 1):
            img_type = it.get("image_type", "?")
            icon = "📷" if img_type == "original" else "📝"
            lines.append(f"### {i}. {icon} {it.get('title', '?')}")
            lines.append(f"- **Type**: {img_type}")
            lines.append(f"- **Reason**: {it.get('reason', '')}")
            if it.get("image_url"):
                lines.append(f"- **URL**: {it['image_url']}")
            lines.append("")

    return lines
