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
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

# 图片抓取超时秒数
DEFAULT_IMAGE_TIMEOUT = 8

# 已知无图的来源（天然少图，标记 text_only 不尝试抓取）。
# HN 不在这里：HN 条目常常指向外部文章，外部文章可能有 og:image。
_TEXT_ONLY_SOURCES = {"arxiv", "github", "hugging face", "huggingface"}

_BAD_IMAGE_HINTS = [
    "favicon", "icon-", "apple-touch-icon", "logo", "avatar", "profile",
    "sprite", "placeholder", "transparent", "tracking", "pixel", "badge",
    "default", "blank", "spacer", "loading", "spinner", "gravatar",
]

_GOOD_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".avif")


def _normalize_image_url(image_url: str, base_url: str = "") -> str:
    """规范化图片 URL。"""
    if not image_url:
        return ""
    image_url = _html.unescape(str(image_url).strip())
    if not image_url or image_url.startswith("data:"):
        return ""
    if base_url:
        image_url = urljoin(base_url, image_url)
    return image_url


def _add_candidate(candidates: list[dict], image_url: str, source: str, base_url: str = ""):
    """追加候选图并去重。"""
    image_url = _normalize_image_url(image_url, base_url)
    if not image_url:
        return
    if not image_url.startswith(("http://", "https://")):
        return
    if any(c.get("url") == image_url for c in candidates):
        return
    candidates.append({"url": image_url, "source": source})


def _score_image_url(image_url: str, source: str = "") -> tuple[int, list[str]]:
    """根据 URL 和来源给图片候选粗评分。"""
    url_lower = image_url.lower()
    reasons: list[str] = []
    score = 50

    if any(hint in url_lower for hint in _BAD_IMAGE_HINTS):
        score -= 45
        reasons.append("bad_hint")
    if url_lower.split("?")[0].endswith(_GOOD_IMAGE_EXTS):
        score += 10
        reasons.append("image_ext")
    if "og" in source or "twitter" in source:
        score += 18
        reasons.append(source)
    if "jsonld" in source:
        score += 15
        reasons.append(source)
    if source.startswith("rss:media") or source == "rss:enclosure":
        score += 16
        reasons.append(source)
    if source in ("html:first_img", "html:srcset"):
        score += 8
        reasons.append(source)

    # URL 中常见尺寸提示。
    size_matches = re.findall(r'(\d{2,4})[xX](\d{2,4})', url_lower)
    for w_s, h_s in size_matches[:2]:
        try:
            w, h = int(w_s), int(h_s)
        except ValueError:
            continue
        if w < 300 or h < 160:
            score -= 35
            reasons.append(f"small_hint:{w}x{h}")
        elif w >= 600 and h >= 300:
            score += 12
            reasons.append(f"large_hint:{w}x{h}")
        ratio = w / max(h, 1)
        if 1.2 <= ratio <= 2.4:
            score += 8
            reasons.append("cover_ratio_hint")

    if "?" in image_url and any(k in url_lower for k in ["width=", "w=", "height=", "h="]):
        score += 4
        reasons.append("sized_url")

    return max(0, min(score, 100)), reasons


def _choose_best_image(candidates: list[dict]) -> tuple[str, dict]:
    """从候选图中选择最适合正文/封面的图片。"""
    best_url = ""
    best_meta: dict = {"score": 0, "source": "", "reasons": []}
    for cand in candidates:
        image_url = cand.get("url", "")
        source = cand.get("source", "")
        score, reasons = _score_image_url(image_url, source)
        if score > best_meta["score"]:
            best_url = image_url
            best_meta = {
                "score": score,
                "source": source,
                "reasons": reasons,
            }
    return best_url, best_meta


def _fetch_page_image_candidates(article_url: str, timeout: int = 8) -> list[dict]:
    """
    抓取文章页面中的图片候选。

    覆盖 og:image、twitter:image、JSON-LD image、srcset、正文第一张图。
    过滤明显太小的图标/logo。
    """
    candidates: list[dict] = []
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
            return candidates

        html_text = resp.text[:180000]

        # og:image（优先）
        for pattern, source in [
            (r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)["\']', "og:image"),
            (r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::url)?["\']', "og:image"),
            (r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']', "twitter:image"),
            (r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']', "twitter:image"),
        ]:
            for m in re.finditer(pattern, html_text, re.IGNORECASE):
                _add_candidate(candidates, m.group(1), source, article_url)

        # JSON-LD image
        for m in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html_text,
            re.IGNORECASE | re.DOTALL,
        ):
            block = _html.unescape(m.group(1))
            for image_match in re.finditer(r'"image"\s*:\s*(?:"([^"]+)"|\[(.*?)\]|\{(.*?)\})', block, re.DOTALL):
                direct = image_match.group(1)
                array_body = image_match.group(2)
                object_body = image_match.group(3)
                if direct:
                    _add_candidate(candidates, direct, "jsonld:image", article_url)
                if array_body:
                    for url_m in re.finditer(r'"(https?://[^"]+|/[^"]+)"', array_body):
                        _add_candidate(candidates, url_m.group(1), "jsonld:image", article_url)
                if object_body:
                    url_m = re.search(r'"url"\s*:\s*"([^"]+)"', object_body)
                    if url_m:
                        _add_candidate(candidates, url_m.group(1), "jsonld:image", article_url)

        # srcset: 优先取每个 srcset 的最后一个（通常最大）。
        for m in re.finditer(r'<img[^>]+srcset=["\']([^"\']+)["\']', html_text, re.IGNORECASE):
            srcset = m.group(1)
            parts = [p.strip().split()[0] for p in srcset.split(",") if p.strip()]
            if parts:
                _add_candidate(candidates, parts[-1], "html:srcset", article_url)

        # 正文 img src 候选，最多取前 5 张。
        img_count = 0
        for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html_text, re.IGNORECASE):
            _add_candidate(candidates, m.group(1), "html:first_img", article_url)
            img_count += 1
            if img_count >= 5:
                break
    except Exception:
        pass
    return candidates


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

        raw_candidates = []
        for cand in item.get("image_candidates", []) or []:
            if isinstance(cand, dict):
                _add_candidate(raw_candidates, cand.get("url", ""), cand.get("source", "item:candidate"), url)
            else:
                _add_candidate(raw_candidates, str(cand), "item:candidate", url)

        # 已有图片 → 直接标记，同时评分为封面候选
        if existing_img:
            score, reasons = _score_image_url(existing_img, "existing article_image_url")
            item["image_type"] = "original"
            item["image_reason"] = "existing article_image_url"
            item["article_original_image_url"] = existing_img
            item["cover_image_url"] = existing_img if score >= 45 else ""
            item["cover_image_score"] = score
            item["cover_image_reason"] = ",".join(reasons) or "existing article_image_url"
            return {
                "title": title,
                "image_type": "original",
                "image_url": existing_img[:100],
                "cover_image_score": score,
                "reason": "existing article_image_url",
            }

        # RSS 自带媒体候选优先于页面抓取。
        best_rss_img, best_rss_meta = _choose_best_image(raw_candidates)
        if best_rss_img and best_rss_meta["score"] >= 45:
            item["image_type"] = "original"
            item["image_reason"] = f"rss image candidate ({best_rss_meta['source']})"
            item["article_image_url"] = best_rss_img
            item["article_original_image_url"] = best_rss_img
            item["cover_image_url"] = best_rss_img if best_rss_meta["score"] >= 55 else ""
            item["cover_image_score"] = best_rss_meta["score"]
            item["cover_image_reason"] = ",".join(best_rss_meta["reasons"])
            return {
                "title": title,
                "image_type": "original",
                "image_url": best_rss_img[:100],
                "cover_image_score": best_rss_meta["score"],
                "reason": item["image_reason"],
            }

        # 天然无图来源 → 标记 text_only
        if _is_text_only_source(source, source_type):
            item["image_type"] = "text_only"
            item["image_reason"] = f"source usually text-only ({source_type or source})"
            item["article_image_url"] = ""
            item["cover_image_url"] = ""
            item["cover_image_score"] = 0
            return {
                "title": title,
                "image_type": "text_only",
                "image_url": "",
                "cover_image_score": 0,
                "reason": f"source usually text-only ({source_type or source})",
            }

        # 尝试抓取页面图片候选
        if url:
            page_candidates = _fetch_page_image_candidates(url, timeout=timeout)
            best_img, best_meta = _choose_best_image(page_candidates)
            if best_img and best_meta["score"] >= 45:
                item["image_type"] = "original"
                item["image_reason"] = f"page image found ({best_meta['source']})"
                item["article_image_url"] = best_img
                item["article_original_image_url"] = best_img
                item["cover_image_url"] = best_img if best_meta["score"] >= 55 else ""
                item["cover_image_score"] = best_meta["score"]
                item["cover_image_reason"] = ",".join(best_meta["reasons"])
                logger.info(
                    "Media #%d: image found for '%s' source=%s score=%d",
                    idx, title[:30], best_meta["source"], best_meta["score"],
                )
                return {
                    "title": title,
                    "image_type": "original",
                    "image_url": best_img[:100],
                    "cover_image_score": best_meta["score"],
                    "reason": item["image_reason"],
                }

        # 无图
        item["image_type"] = "text_only"
        item["image_reason"] = "no og:image found"
        item["article_image_url"] = ""
        item["cover_image_url"] = ""
        item["cover_image_score"] = 0
        return {
            "title": title,
            "image_type": "text_only",
            "image_url": "",
            "cover_image_score": 0,
            "reason": "no og:image found",
        }

    # 顺序处理（避免并发过载）
    used_image_urls: set[str] = set()
    for i, item in enumerate(news_list):
        try:
            report = _process_one(i, item)
            image_url = item.get("article_image_url", "")
            if item.get("image_type") == "original" and image_url in used_image_urls:
                item["image_type"] = "text_only"
                item["image_reason"] = "duplicate image URL"
                item["article_image_url"] = ""
                item["cover_image_url"] = ""
                item["cover_image_score"] = 0
                report.update(
                    image_type="text_only",
                    image_url="",
                    cover_image_score=0,
                    reason="duplicate image URL",
                )
            elif item.get("image_type") == "original" and image_url:
                used_image_urls.add(image_url)
            items_report.append(report)
        except Exception as e:
            logger.warning("Media #%d: failed to process: %s", i, e)
            item["image_type"] = "text_only"
            item["image_reason"] = f"process error: {e}"
            item["article_image_url"] = ""
            item["cover_image_url"] = ""
            item["cover_image_score"] = 0
            items_report.append({
                "title": (item.get("chinese_title") or item.get("title", ""))[:60],
                "image_type": "text_only",
                "image_url": "",
                "cover_image_score": 0,
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
            lines.append(f"- **Cover image score**: {it.get('cover_image_score', 0)}")
            lines.append("")

    return lines
