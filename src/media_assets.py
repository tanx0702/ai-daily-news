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
import hashlib
import io
import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from PIL import Image, ImageOps
from urllib.parse import urljoin

from src.briefing.adapters import brief_item_to_display_dict
from src.briefing.models import BriefItem

logger = logging.getLogger(__name__)


def _display_news(items: Sequence[BriefItem | Mapping[str, Any]]) -> list[dict]:
    """Copy display data before adding transient media fields."""
    return [
        brief_item_to_display_dict(item) if isinstance(item, BriefItem) else dict(item)
        for item in items
    ]

# 图片抓取超时秒数
DEFAULT_IMAGE_TIMEOUT = 8
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MIN_IMAGE_WIDTH = 320
MIN_IMAGE_HEIGHT = 180
MAX_PERCEPTUAL_HASH_DISTANCE = 4

# 已知无图的来源（天然少图，标记 text_only 不尝试抓取）。
# HN 不在这里：HN 条目常常指向外部文章，外部文章可能有 og:image。
_TEXT_ONLY_SOURCES = {"arxiv", "github", "hugging face", "huggingface"}

_BAD_IMAGE_HINTS = [
    "favicon", "icon-", "apple-touch-icon", "logo", "avatar", "profile",
    "sprite", "placeholder", "transparent", "tracking", "pixel", "badge",
    "default", "blank", "spacer", "loading", "spinner", "gravatar",
]

_GOOD_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".avif")
_MEDIA_GENERIC_TOKENS = {
    "ai", "model", "models", "product", "products", "research", "strategy",
    "team", "image", "photo", "corporate", "company", "launch", "launches",
    "release", "releases", "new", "the", "and", "for", "with", "from",
    "模型", "产品", "研究", "战略", "团队", "公司", "图片", "照片", "发布",
    "推出", "上线", "公开", "新闻", "快讯",
}
_MEDIA_ORGANIZATIONS = {
    "openai", "anthropic", "google", "deepmind", "mistral", "meta", "microsoft",
    "nvidia", "xai", "cohere", "cerebras", "谷歌", "微软", "英伟达",
}
_MEDIA_MODEL_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:gpt|claude|gemini|llama|qwen|deepseek|model)"
    r"[- ]?[a-z]*\d[\w.+-]*",
    re.I,
)


def _media_anchor_tokens(value: object) -> set[str]:
    normalized = _html.unescape(str(value or "")).casefold()
    models = {
        "model:" + re.sub(r"\s+", "-", match.group(0))
        for match in _MEDIA_MODEL_PATTERN.finditer(normalized)
    }
    tokens = {
        token.strip(".-")
        for token in re.findall(r"[a-z][a-z0-9.+-]{2,}|[\u4e00-\u9fff]{2,}", normalized)
    }
    tokens -= _MEDIA_GENERIC_TOKENS | _MEDIA_ORGANIZATIONS
    return models | {f"token:{token}" for token in tokens if token}


def evaluate_media_relevance(event_text: object, image_text: object) -> dict[str, object]:
    """Accept only images whose local metadata can be tied to the event facts."""
    event_anchors = _media_anchor_tokens(event_text)
    image_anchors = _media_anchor_tokens(image_text)
    shared = event_anchors & image_anchors
    shared_models = {anchor for anchor in shared if anchor.startswith("model:")}
    if shared_models:
        return {
            "accepted": True,
            "reason": "shared_model_anchor",
            "anchors": tuple(sorted(shared_models)),
        }
    shared_tokens = {anchor for anchor in shared if anchor.startswith("token:")}
    if len(shared_tokens) >= 2:
        return {
            "accepted": True,
            "reason": "shared_event_anchors",
            "anchors": tuple(sorted(shared_tokens)),
        }
    return {
        "accepted": False,
        "reason": "missing_semantic_anchor",
        "anchors": tuple(sorted(shared_tokens)),
    }


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


def _clean_html_text(value: object) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _html.unescape(str(value or "")))).strip()


def _add_candidate(
    candidates: list[dict],
    image_url: str,
    source: str,
    base_url: str = "",
    semantic_text: object = "",
) -> None:
    """追加候选图并去重。"""
    image_url = _normalize_image_url(image_url, base_url)
    if not image_url:
        return
    if not image_url.startswith(("http://", "https://")):
        return
    cleaned_context = _html.unescape(str(semantic_text or "")).strip()
    for candidate in candidates:
        if candidate.get("url") != image_url:
            continue
        existing = str(candidate.get("semantic_text") or "")
        if cleaned_context and cleaned_context not in existing:
            candidate["semantic_text"] = " ".join(
                part for part in (existing, cleaned_context) if part
            )
        return
    candidates.append({
        "url": image_url,
        "source": source,
        "semantic_text": cleaned_context,
    })


def _media_event_text(item: BriefItem | Mapping[str, Any]) -> str:
    """Build private image-matching context from verified display/source facts only."""
    if isinstance(item, BriefItem):
        parts = [item.chinese_title, item.canonical_source.source_title]
        parts.extend(binding.source_quote for binding in item.evidence_bindings)
        return "\n".join(part for part in parts if part)
    bindings = item.get("evidence_bindings") if isinstance(item, Mapping) else None
    binding_quotes = [
        str(binding.get("source_quote") or "")
        for binding in bindings or ()
        if isinstance(binding, Mapping)
    ]
    return "\n".join(
        part
        for part in (
            str(item.get("chinese_title") or item.get("title") or ""),
            str(item.get("source_title") or ""),
            *binding_quotes,
        )
        if part
    )


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


def _average_hash(image: Image.Image) -> str:
    """Return a compact perceptual hash without another runtime dependency."""
    grayscale = image.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
    pixels = list(grayscale.getdata())
    average = sum(pixels) / len(pixels)
    bits = "".join("1" if value >= average else "0" for value in pixels)
    return f"{int(bits, 2):016x}"


def _hash_distance(first: str, second: str) -> int:
    try:
        return (int(first, 16) ^ int(second, 16)).bit_count()
    except (TypeError, ValueError):
        return 64


def _has_expected_image_magic(content: bytes, image_format: str) -> bool:
    signatures = {
        "JPEG": content.startswith(b"\xff\xd8"),
        "PNG": content.startswith(b"\x89PNG\r\n\x1a\n"),
        "WEBP": content.startswith(b"RIFF") and content[8:12] == b"WEBP",
        "GIF": content.startswith((b"GIF87a", b"GIF89a")),
        "BMP": content.startswith(b"BM"),
    }
    return signatures.get(image_format.upper(), False)


def validate_media_candidate(url: str, timeout: int, max_bytes: int = MAX_IMAGE_BYTES) -> dict:
    """Download, decode, and normalize a candidate image before it enters a draft."""
    image_url = _normalize_image_url(url)
    result = {
        "valid": False,
        "url": image_url,
        "reason": "",
        "jpeg_bytes": b"",
        "sha256": "",
        "phash": "",
        "width": 0,
        "height": 0,
        "format": "",
    }
    if not image_url:
        result["reason"] = "missing_url"
        return result
    if any(hint in image_url.lower() for hint in _BAD_IMAGE_HINTS):
        result["reason"] = "bad_url_hint"
        return result

    try:
        response = requests.get(
            image_url,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AIDailyNews/1.0)",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        content = response.content
        if not content:
            result["reason"] = "empty_response"
            return result
        if len(content) > max_bytes:
            result["reason"] = "image_too_large"
            return result

        with Image.open(io.BytesIO(content)) as decoded:
            image_format = str(decoded.format or "").upper()
            if not _has_expected_image_magic(content, image_format):
                result["reason"] = "unsupported_or_mismatched_image_type"
                return result
            decoded.load()
            width, height = decoded.size
            if width < MIN_IMAGE_WIDTH or height < MIN_IMAGE_HEIGHT:
                result["reason"] = f"image_too_small:{width}x{height}"
                return result
            aspect_ratio = width / max(height, 1)
            if aspect_ratio < 0.45 or aspect_ratio > 3.6:
                result["reason"] = f"logo_like_aspect_ratio:{aspect_ratio:.2f}"
                return result

            normalized = ImageOps.exif_transpose(decoded)
            if normalized.mode in ("RGBA", "LA"):
                background = Image.new("RGB", normalized.size, "white")
                background.paste(normalized, mask=normalized.getchannel("A"))
                normalized = background
            else:
                normalized = normalized.convert("RGB")
            output = io.BytesIO()
            normalized.save(output, "JPEG", quality=90, optimize=True)
            jpeg_bytes = output.getvalue()

        result.update(
            valid=True,
            reason="validated",
            jpeg_bytes=jpeg_bytes,
            sha256=hashlib.sha256(jpeg_bytes).hexdigest(),
            phash=_average_hash(normalized),
            width=width,
            height=height,
            format=image_format,
        )
    except Exception as exc:
        result["reason"] = f"download_or_decode_failed:{type(exc).__name__}"
    return result


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
        page_title_match = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
            html_text,
            re.IGNORECASE,
        ) or re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
        page_context = _clean_html_text(page_title_match.group(1)) if page_title_match else ""

        # og:image（优先）
        for pattern, source in [
            (r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)["\']', "og:image"),
            (r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::url)?["\']', "og:image"),
            (r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']', "twitter:image"),
            (r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']', "twitter:image"),
        ]:
            for m in re.finditer(pattern, html_text, re.IGNORECASE):
                _add_candidate(candidates, m.group(1), source, article_url, page_context)

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
                    _add_candidate(candidates, direct, "jsonld:image", article_url, page_context)
                if array_body:
                    for url_m in re.finditer(r'"(https?://[^"]+|/[^"]+)"', array_body):
                        _add_candidate(candidates, url_m.group(1), "jsonld:image", article_url, page_context)
                if object_body:
                    url_m = re.search(r'"url"\s*:\s*"([^"]+)"', object_body)
                    if url_m:
                        _add_candidate(candidates, url_m.group(1), "jsonld:image", article_url, page_context)

        # srcset: 优先取每个 srcset 的最后一个（通常最大）。
        for m in re.finditer(r'<img[^>]+srcset=["\']([^"\']+)["\']', html_text, re.IGNORECASE):
            srcset = m.group(1)
            parts = [p.strip().split()[0] for p in srcset.split(",") if p.strip()]
            if parts:
                _add_candidate(candidates, parts[-1], "html:srcset", article_url, page_context)

        # 正文 img src 候选，最多取前 5 张。
        img_count = 0
        for m in re.finditer(r"<img\b([^>]*)>", html_text, re.IGNORECASE):
            attributes = m.group(1)
            src_match = re.search(r"\bsrc=[\"']([^\"']+)[\"']", attributes, re.IGNORECASE)
            if not src_match:
                continue
            alt_match = re.search(r"\balt=[\"']([^\"']+)[\"']", attributes, re.IGNORECASE)
            context = _clean_html_text(alt_match.group(1)) if alt_match else page_context
            _add_candidate(candidates, src_match.group(1), "html:first_img", article_url, context)
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
    news_list: Sequence[BriefItem | Mapping[str, Any]],
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
    display_news = _display_news(news_list)
    media_contexts = [_media_event_text(item) for item in news_list]
    if not display_news:
        return display_news, {
            "date": date_str,
            "total": 0,
            "with_original_image": 0,
            "text_only": 0,
            "items": [],
        }

    items_report: list[dict] = []

    def set_text_only(item: dict, reason: str, media_state: str = "rejected") -> None:
        item["image_type"] = "text_only"
        item["media_state"] = media_state
        item["image_reason"] = reason
        item["article_image_url"] = ""
        item["cover_image_url"] = ""
        item["cover_image_score"] = 0
        item.pop("normalized_image_path", None)

    def persist_normalized_image(validation: dict) -> str:
        if not docs_dir:
            return ""
        media_dir = os.path.join(docs_dir, "media", date_str or "latest")
        os.makedirs(media_dir, exist_ok=True)
        image_path = os.path.join(media_dir, f"{validation['sha256'][:16]}.jpg")
        if not os.path.isfile(image_path):
            with open(image_path, "wb") as file:
                file.write(validation["jpeg_bytes"])
        return image_path

    def validate_candidates(
        candidates: list[dict], event_text: str
    ) -> tuple[dict | None, str]:
        failures: list[str] = []
        scored = []
        for candidate in candidates:
            score, reasons = _score_image_url(candidate["url"], candidate.get("source", ""))
            scored.append((score, candidate, reasons))
        for score, candidate, reasons in sorted(scored, key=lambda value: value[0], reverse=True):
            relevance = evaluate_media_relevance(
                event_text,
                candidate.get("semantic_text", ""),
            )
            if not relevance["accepted"]:
                failures.append(str(relevance["reason"]))
                continue
            validation = validate_media_candidate(candidate["url"], timeout)
            if validation.get("valid"):
                validation.setdefault("url", candidate["url"])
                validation["source"] = candidate.get("source", "")
                validation["score"] = score
                validation["score_reasons"] = reasons
                validation["semantic_reason"] = relevance["reason"]
                validation["semantic_anchors"] = relevance["anchors"]
                return validation, ""
            failures.append(validation.get("reason", "validation_failed"))
        return None, ";".join(failures[:3]) or "no_valid_image_candidate"

    for index, item in enumerate(display_news):
        title = (item.get("chinese_title") or item.get("title", ""))[:60]
        source = item.get("source", "")
        source_type = item.get("source_type", "")
        article_url = item.get("url", "")
        event_text = media_contexts[index]
        candidates: list[dict] = []
        _add_candidate(
            candidates,
            item.get("article_image_url", ""),
            "existing article_image_url",
            article_url,
            item.get("article_image_context") or item.get("media_semantic_text"),
        )
        for candidate in item.get("image_candidates", []) or []:
            if isinstance(candidate, dict):
                _add_candidate(
                    candidates,
                    candidate.get("url", ""),
                    candidate.get("source", "item:candidate"),
                    article_url,
                    candidate.get("semantic_text") or candidate.get("alt") or candidate.get("caption"),
                )
            else:
                _add_candidate(candidates, str(candidate), "item:candidate", article_url)
        if not candidates and not _is_text_only_source(source, source_type) and article_url:
            candidates.extend(_fetch_page_image_candidates(article_url, timeout=timeout))

        validation, reason = (
            validate_candidates(candidates, event_text)
            if candidates
            else (None, "no_image_candidate")
        )
        if not validation:
            state = "text_only" if _is_text_only_source(source, source_type) and not candidates else "rejected"
            set_text_only(item, reason, state)
            items_report.append({
                "title": title,
                "image_type": "text_only",
                "media_state": state,
                "image_url": "",
                "cover_image_score": 0,
                "reason": reason,
            })
            continue

        item["image_type"] = "original"
        item["media_state"] = "trusted"
        item["image_reason"] = (
            f"validated image ({validation['source']}; {validation['semantic_reason']})"
        )
        item["article_image_url"] = validation["url"]
        item["article_original_image_url"] = validation["url"]
        item["normalized_image_path"] = persist_normalized_image(validation)
        item["media_sha256"] = validation["sha256"]
        item["media_phash"] = validation["phash"]
        item["media_width"] = validation["width"]
        item["media_height"] = validation["height"]
        item["cover_image_url"] = validation["url"]
        item["cover_image_score"] = validation["score"]
        item["cover_image_reason"] = ",".join(validation["score_reasons"]) or "validated"
        items_report.append({
            "title": title,
            "image_type": "original",
            "media_state": "trusted",
            "image_url": validation["url"][:100],
            "cover_image_score": validation["score"],
            "sha256": validation["sha256"],
            "phash": validation["phash"],
            "dimensions": f"{validation['width']}x{validation['height']}",
            "reason": item["image_reason"],
        })

    used_hashes: set[str] = set()
    used_perceptual_hashes: list[str] = []
    for item, report in zip(display_news, items_report):
        if item.get("media_state") != "trusted":
            continue
        duplicate = (
            item["media_sha256"] in used_hashes
            or any(_hash_distance(item["media_phash"], known) <= MAX_PERCEPTUAL_HASH_DISTANCE
                   for known in used_perceptual_hashes)
        )
        if duplicate:
            set_text_only(item, "duplicate media hash")
            report.update(image_type="text_only", media_state="rejected", image_url="", reason="duplicate media hash")
            continue
        used_hashes.add(item["media_sha256"])
        used_perceptual_hashes.append(item["media_phash"])

    with_original = sum(1 for it in items_report if it["image_type"] == "original")
    text_only = sum(1 for it in items_report if it["image_type"] == "text_only")

    media_report = {
        "date": date_str,
        "total": len(display_news),
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

    return display_news, media_report


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
