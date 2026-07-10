"""
封面图生成模块

使用 Agnes Image API 根据当日新闻标题生成每日封面图。
"""

import base64
import io
import logging
import os
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

def _env_enabled_cover(name: str, default: bool = True) -> bool:
    """解析布尔型环境变量。"""
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


# 中文字体候选列表（按优先级排序：Linux 容器 → Linux 通用 → Windows）
_FONT_CANDIDATES = [
    # Docker 容器 (fonts-noto-cjk)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    # Docker 容器 (fonts-wqy-zenhei)
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    # Linux 通用
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    # Windows
    "C:\\Windows\\Fonts\\msyh.ttc",
    "C:\\Windows\\Fonts\\simhei.ttf",
    "msyh.ttc",
    "msyhbd.ttc",
    "simhei.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """按优先级尝试加载中文字体，全部失败时返回默认字体（不抛异常）。"""
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    # 所有路径都失败，使用 Pillow 内置默认字体
    return ImageFont.load_default()


def _draw_cover_text(
    img: Image.Image,
    cover_title: str,
    date_str: str,
    news_count: int,
) -> Image.Image:
    """在封面图上叠加中文标题、日期和条数。字体加载失败时静默降级。"""
    try:
        return _draw_cover_text_impl(img, cover_title, date_str, news_count)
    except Exception as e:
        logger.warning("Failed to draw cover text: %s, returning bare image", e)
        return img


def _draw_cover_text_impl(
    img: Image.Image,
    cover_title: str,
    date_str: str,
    news_count: int,
) -> Image.Image:
    """_draw_cover_text 的实际实现。"""
    draw = ImageDraw.Draw(img)
    width, height = img.size

    # 根据图片宽度自适应字号
    base_size = max(width // 18, 28)

    font_title = _load_font(base_size)
    font_date = _load_font(max(base_size // 2, 16))
    font_count = _load_font(max(base_size // 3, 12))

    # 半透明背景条（提升文字可读性）
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    # 底部渐变遮罩
    for y in range(height // 2, height):
        alpha = int(120 * (y - height // 2) / (height // 2))
        overlay_draw.rectangle([(0, y), (width, y + 1)], fill=(0, 0, 0, min(alpha, 120)))

    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # 标题文字（底部居中）
    _, _, tw, th = draw.textbbox((0, 0), cover_title, font=font_title)
    title_y = height - th - 80
    draw.text(((width - tw) // 2, title_y), cover_title, fill="#ffffff", font=font_title)

    # 日期 + 条数
    date_text = f"{date_str} · 今日 {news_count} 条 AI 新闻"
    _, _, dw, dh = draw.textbbox((0, 0), date_text, font=font_date)
    draw.text(((width - dw) // 2, title_y + th + 12), date_text, fill="#cccccc", font=font_date)

    return img


def _generate_simple_cover(
    news_list: list[dict],
    date_str: str,
    output_path: str,
    width: int = 900,
    height: int = 500,
    cover_title: str = "AI 日报",
) -> str:
    """
    降级方案：Pillow 生成的编辑风程序化封面。

    设计：深色背景 + 细网格 + 抽象节点连线 + 渐变光晕，
    不随机切换配色，保持专业科技刊物的一致感。
    """
    import math

    img = Image.new("RGB", (width, height), "#0f172a")  # 深蓝黑底
    draw = ImageDraw.Draw(img)

    # ── 细网格背景 ──
    grid_spacing = 40
    grid_color = (30, 41, 59)  # #1e293b 暗蓝灰
    for x in range(0, width, grid_spacing):
        draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
    for y in range(0, height, grid_spacing):
        draw.line([(0, y), (width, y)], fill=grid_color, width=1)

    # ── 抽象节点和连线（稳定布局，不随机） ──
    nodes = [
        (width * 0.15, height * 0.25),
        (width * 0.35, height * 0.15),
        (width * 0.60, height * 0.30),
        (width * 0.75, height * 0.20),
        (width * 0.20, height * 0.50),
        (width * 0.55, height * 0.55),
        (width * 0.80, height * 0.45),
        (width * 0.40, height * 0.70),
        (width * 0.65, height * 0.70),
    ]

    # 连线（淡青色半透明）
    for i, (x1, y1) in enumerate(nodes):
        for j, (x2, y2) in enumerate(nodes):
            if i < j and ((x2 - x1)**2 + (y2 - y1)**2) < (width * 0.35)**2:
                alpha = max(10, 40 - int(((x2 - x1)**2 + (y2 - y1)**2) ** 0.5 / 12))
                draw.line([(x1, y1), (x2, y2)], fill=(56, 189, 248, alpha), width=1)

    # 节点圆点（青蓝渐变光晕 + 白心）
    halo_color = (56, 189, 248)  # cyan-400
    for (nx, ny) in nodes:
        # 外光晕
        for r in range(8, 1, -2):
            alpha = max(10, 60 - r * 5)
            draw.ellipse(
                [(nx - r, ny - r), (nx + r, ny + r)],
                fill=(56, 189, 248, alpha),
                outline=None,
            )
        # 实心白点
        draw.ellipse([(nx - 2, ny - 2), (nx + 2, ny + 2)], fill=(226, 232, 240))

    # ── 底部深色渐变遮罩（为文字做准备）──
    for y_offset in range(height // 2, height):
        alpha = int(180 * (y_offset - height // 2) / (height // 2))
        draw.rectangle(
            [(0, y_offset), (width, y_offset + 1)],
            fill=(15, 23, 42, min(alpha, 180)),
        )

    # ── 叠加中文标题 ──
    try:
        img = _draw_cover_text(img, cover_title, date_str, len(news_list))
    except Exception as e:
        logger.warning("Cover text overlay failed: %s, saving bare image", e)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path, "JPEG", quality=90)
    logger.info("Generated editorial simple cover at %s", output_path)
    return output_path


def generate_cover_from_news(
    news_list: list[dict],
    date_str: str,
    output_path: str = None,
    api_key: Optional[str] = None,
    base_url: str = "https://apihub.agnes-ai.com",
    cover_title: str = "",
    cover_subject: Optional[dict] = None,
) -> Optional[str]:
    """
    根据新闻标题生成封面图。

    流程（改进后）：
    1. 从可信候选池选择封面主题（select_cover_subject）
    2. 构建 prompt 调用 Agnes Image API
    3. 下载生成的图片
    4. 检测图片是否疑似含文字（_looks_like_bad_cover）
    5. 若 bad → 降级为程序化封面
    6. 叠加中文封面标题

    Args:
        news_list: 新闻列表
        date_str: 日期字符串
        output_path: 输出图片路径
        api_key: Agnes API Key
        base_url: Agnes API 基础地址
        cover_title: 中文封面标题（12-20 字）
        cover_subject: select_cover_subject() 的结果，为空则内部生成

    Returns:
        封面图路径，失败返回 None
    """
    cover_title = cover_title or "AI 日报"

    # 0. 封面主题选择（如果未外部传入）
    if cover_subject is None:
        cover_subject = select_cover_subject(news_list)
    if cover_subject.get("cover_title") and not cover_title.startswith("今日 AI 热点速览"):
        # 使用 select_cover_subject 返回的标题（可信模式）
        ct = cover_subject.get("cover_title", "")
        if ct and len(ct) >= 4:
            cover_title = ct

    api_key = api_key or os.environ.get("AGNES_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    safe_cover_enabled = _env_enabled_cover("ENABLE_SAFE_COVER", True)
    force_local_on_bad = _env_enabled_cover("FORCE_LOCAL_COVER_ON_BAD_IMAGE", True)

    if output_path is None:
        output_path = os.path.join("docs", "cover.jpg")

    # 安全模式：无可信候选时直接使用程序化封面
    if safe_cover_enabled and cover_subject.get("mode") == "generic":
        logger.info("Safe cover: no trusted candidate, using local programmatic cover")
        return _fallback_cover(news_list, date_str, output_path, cover_title)

    if not api_key:
        logger.warning("No API key for cover generation, falling back to programmatic cover")
        return _fallback_cover(news_list, date_str, output_path, cover_title)

    # 1. 构建 prompt
    prompt = _build_cover_prompt(cover_subject)

    # 2. 调用 Agnes Image API
    base_url = base_url.rstrip("/")
    image_base = base_url.replace("/v1", "") if base_url.endswith("/v1") else base_url
    ai_generated = False
    bad_ai = False

    try:
        logger.info("Generating cover image with prompt: %s", prompt[:80])
        resp = requests.post(
            f"{image_base}/v1/images/generations",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "agnes-image-2.1-flash",
                "prompt": prompt,
                "size": "900x500",
                "extra_body": {"response_format": "url"},
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        image_url = data.get("data", [{}])[0].get("url")
        if not image_url:
            raise ValueError(f"No image URL in response: {data}")

        # 3. 下载图片
        img_resp = requests.get(image_url, timeout=30)
        img_resp.raise_for_status()

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        # 4. 检测图片质量
        img = Image.open(io.BytesIO(img_resp.content))
        is_bad, bad_reason = _looks_like_bad_cover(img)

        if is_bad:
            logger.warning("AI cover image looks bad: %s", bad_reason)
            bad_ai = True
            if force_local_on_bad:
                logger.info("FORCE_LOCAL_COVER_ON_BAD_IMAGE=1, using programmatic cover")
                return _fallback_cover(news_list, date_str, output_path, cover_title)
            # 否则继续使用 AI 图，但记录 warning

        # 5. 叠加中文标题（失败时保留原图）
        try:
            img = _draw_cover_text(img, cover_title, date_str, len(news_list))
            img.save(output_path, "JPEG", quality=90)
            ai_generated = True
        except Exception as e:
            logger.warning("Cover text overlay failed: %s, saving bare image", e)
            with open(output_path, "wb") as f:
                f.write(img_resp.content)

        logger.info("Cover image generated and saved to %s (ai=%s, bad=%s)",
                     output_path, ai_generated, bad_ai)
        return output_path

    except Exception as e:
        logger.warning("Agnes Image API failed: %s, falling back to programmatic cover", e)
        return _fallback_cover(news_list, date_str, output_path, cover_title)


def _build_cover_prompt(cover_subject: dict) -> str:
    """
    构建封面图 prompt —— 不再直接使用 news_list[0]。

    Args:
        cover_subject: select_cover_subject() 的返回结果

    要求：
    - 无文字、无字母、无数字、无 logo、无水印
    - 编辑插画风格，不是海报
    - 底部留暗色空间给中文标题叠加
    """
    topic = cover_subject.get("visual_prompt_topic", "artificial intelligence")
    mode = cover_subject.get("mode", "generic")

    if mode == "generic":
        return (
            "A premium editorial technology illustration for a daily AI newsletter. "
            "Style: clean abstract geometric composition, dark graphite and navy background, "
            "subtle cyan and warm amber accent lines connecting scattered data nodes, "
            "soft depth, realistic lighting, no typography at all. "
            "Leave the bottom third dark and gradient-fade for text overlay. "
            "No text, no letters, no numbers, no logos, no UI screenshots, no watermark. "
            "Create an editorial abstract illustration, not a poster. "
            "16:9 aspect ratio, high quality."
        )
    else:
        return (
            f"A premium editorial technology magazine cover illustration. "
            f"Visual theme inspired by: \"{topic}\". "
            f"Style: clean minimalist composition, dark graphite and deep navy background, "
            f"subtle cyan and warm amber accents, abstract geometric elements softly "
            f"referencing the topic without being literal, cinematic lighting, "
            f"professional and elegant. "
            f"No text, no letters, no numbers, no logos, no UI screenshots, no watermark. "
            f"Create an editorial abstract illustration, not a poster. "
            f"Leave clean dark space at the bottom for later Chinese title overlay. "
            f"16:9 aspect ratio, high quality."
        )


def _fallback_cover(
    news_list: list[dict],
    date_str: str,
    output_path: str = None,
    cover_title: str = "AI 日报",
) -> str:
    """降级：生成 Pillow 封面图。"""
    if output_path is None:
        output_path = os.path.join("docs", "cover.jpg")
    return _generate_simple_cover(news_list, date_str, output_path, cover_title=cover_title)


# ═══════════════════════════════════════════════════════════════════
# 封面主题选择
# ═══════════════════════════════════════════════════════════════════

_RUMOR_KEYWORDS = [
    "rumor", "ask hn", "speculation", "unconfirmed", "leak",
    "传闻", "疑似", "爆料", "谣言", "辟谣", "别信谣",
]


def select_cover_subject(news_list: list[dict]) -> dict:
    """
    从可信候选池中选择封面主题。

    过滤掉：
    - _cover_excluded
    - 低置信度
    - 低置信度品牌声明
    - HN-only低热度（无跨源 + score<20）
    - 标题包含传闻关键词

    Returns:
        {
            "mode": "trusted" | "generic",
            "item": dict or None,
            "cover_title": str,
            "visual_prompt_topic": str,
            "reason": str,
        }
    """
    if not news_list:
        return {
            "mode": "generic",
            "item": None,
            "cover_title": "今日 AI 热点速览",
            "visual_prompt_topic": "a curated daily briefing about artificial intelligence",
            "reason": "empty news list",
        }

    # 构建可信候选池
    candidates = []
    excluded = []
    for item in news_list:
        title = item.get("chinese_title") or item.get("title", "")
        title_lower = title.lower()

        # 过滤条件
        if item.get("_cover_excluded"):
            excluded.append((title, f"cover_excluded: {item['_cover_excluded']}"))
            continue
        if item.get("_confidence_level") == "low":
            excluded.append((title, "low confidence"))
            continue
        bc = item.get("_brand_claim", {})
        if bc.get("confidence") == "low":
            excluded.append((title, f"low confidence brand claim: {bc.get('brand', '')}"))
            continue
        st = item.get("source_type", "")
        metrics = item.get("metrics", {}) or {}
        if st == "hn" and metrics.get("cross_source_count", 0) == 0 and metrics.get("hn_score", 0) < 20:
            excluded.append((title, f"HN-only low heat (score={metrics.get('hn_score', 0)})"))
            continue
        if any(kw in title_lower for kw in _RUMOR_KEYWORDS):
            excluded.append((title, "rumor/speculation keyword in title"))
            continue

        # 评分：RSS优先 + 有图优先 + 评分
        priority = 0
        if item.get("source_type") == "rss" or item.get("source_type") == "official":
            priority += 100
        if metrics.get("cross_source_count", 0) > 0:
            priority += 50
        if item.get("article_image_url"):
            priority += 30
        priority += item.get("_score", 0) or item.get("scores", {}).get("final", 0)
        candidates.append((priority, item))

    # 排序：优先级从高到低
    candidates.sort(key=lambda x: x[0], reverse=True)

    if candidates:
        best = candidates[0][1]
        title = best.get("chinese_title") or best.get("title", "")
        logger.info(
            "Cover subject: trusted — '%s' (excluded %d candidates)",
            title[:50], len(excluded),
        )
        return {
            "mode": "trusted",
            "item": best,
            "cover_title": title[:20].rstrip("，。；：！？、"),
            "visual_prompt_topic": _extract_topic_for_visual(best),
            "reason": f"selected from {len(candidates)} trusted candidates, {len(excluded)} excluded",
            "excluded": [{"title": t, "reason": r} for t, r in excluded],
        }

    # 没有可靠候选 — 通用主题
    logger.warning("Cover subject: generic — all %d items excluded", len(news_list))
    return {
        "mode": "generic",
        "item": None,
        "cover_title": "今日 AI 热点速览",
        "visual_prompt_topic": "a curated daily briefing about artificial intelligence industry, research and developer tools",
        "reason": f"no trusted candidate (all {len(excluded)} excluded)",
        "excluded": [{"title": t, "reason": r} for t, r in excluded],
    }


def _extract_topic_for_visual(item: dict) -> str:
    """从新闻条目提取英文视觉主题关键词。"""
    english_title = item.get("title", "")
    has_chinese = any('一' <= c <= '鿿' for c in english_title)
    if has_chinese:
        import re as _re
        en_words = _re.findall(r'[A-Z][a-z]+|[A-Z]{2,}|[a-z]{4,}',
                               english_title + " " + str(item.get("summary", "")))
        stop = {'this', 'that', 'with', 'from', 'have', 'been', 'they', 'them', 'their',
                'your', 'will', 'would', 'could', 'about', 'which', 'when', 'where'}
        en_words = [w for w in en_words if w.lower() not in stop]
        return " ".join(en_words[:8]) if en_words else "artificial intelligence technology"
    return english_title[:150]


# ═══════════════════════════════════════════════════════════════════
# 封面图片质量检测
# ═══════════════════════════════════════════════════════════════════


def _looks_like_bad_cover(img: Image.Image) -> tuple[bool, str]:
    """
    启发式检测 AI 生成封面是否疑似含文字/水印。

    检测方法（无 OCR 依赖）：
    - 尺寸过小/异常
    - 整体过暗（平均亮度 < 25）
    - 大面积高对比矩形（疑似文字区域）

    Returns:
        (is_bad: bool, reason: str)
    """
    w, h = img.size
    if w < 400 or h < 200:
        return True, f"image too small ({w}x{h})"
    if w / h < 1.2 or w / h > 3.0:
        return False, ""  # 非标准比例但可用

    # 转灰度检测整体亮度
    gray = img.convert("L")
    pixels = list(gray.getdata())
    avg_brightness = sum(pixels) / len(pixels)
    if avg_brightness < 25:
        return True, f"image too dark (avg brightness={avg_brightness:.0f})"

    # 中央区域高对比检测（疑似文字/logo）
    # 检查中央 60% 区域是否存在高对比度像素块
    cx, cy = w // 2, h // 2
    region_w, region_h = int(w * 0.5), int(h * 0.4)
    x0, y0 = cx - region_w // 2, cy - region_h // 2
    region = gray.crop((x0, y0, x0 + region_w, y0 + region_h))
    rp = list(region.getdata())

    if len(rp) > 0:
        r_min, r_max = min(rp), max(rp)
        if r_max - r_min > 200:
            # 存在极高对比度 —— 进一步检查是否是分散的小块（文字）还是大面积渐变
            high_contrast_pixels = sum(1 for p in rp if p > 230 or p < 25)
            high_ratio = high_contrast_pixels / len(rp)
            if high_ratio > 0.15:
                return True, f"suspected text/logo in center (high contrast ratio={high_ratio:.2f})"

    return False, ""
