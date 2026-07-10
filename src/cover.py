"""
封面图生成模块

使用 Agnes Image API 根据当日新闻标题生成每日封面图。
"""

import io
import logging
import os
import re
from typing import Optional
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

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


# 封面策略：
# 1. 优先使用正文头条/Top3 的真实原文图。
# 2. 没有真实原文图时，使用程序化标题排版封面。
# 3. AI 生图只作为显式开启的可选兜底，不作为默认封面来源。
# 4. 如果封面里出现文字，必须由 Pillow 程序渲染，不能交给图片模型乱写。


# ═══════════════════════════════════════════════════════════════════
# 故事类型色板 — 用于本地 fallback 封面
# ═══════════════════════════════════════════════════════════════════

_STORY_TYPE_PALETTE: dict[str, dict[str, tuple]] = {
    "personnel": {
        "bg": (38, 35, 58),          # 深紫蓝 (更明快)
        "primary": (220, 160, 90),   # 暖橙金
        "secondary": (65, 55, 85),   # 中紫
        "accent": (255, 200, 120),   # 亮橙黄
    },
    "product": {
        "bg": (25, 45, 55),          # 深青蓝
        "primary": (80, 180, 180),   # 明快青绿
        "secondary": (45, 70, 80),   # 中青
        "accent": (150, 230, 220),   # 亮青绿
    },
    "model": {
        "bg": (40, 30, 65),          # 深靛紫
        "primary": (140, 100, 180),  # 明快紫
        "secondary": (60, 45, 90),   # 中紫
        "accent": (200, 160, 240),   # 亮紫
    },
    "research": {
        "bg": (28, 42, 35),          # 深绿灰
        "primary": (90, 160, 120),   # 明快翠绿
        "secondary": (45, 65, 55),   # 中绿
        "accent": (160, 220, 180),   # 亮翠绿
    },
    "business": {
        "bg": (35, 35, 55),          # 深蓝灰
        "primary": (220, 190, 100),  # 明快金色
        "secondary": (60, 55, 75),   # 中蓝
        "accent": (255, 230, 140),   # 亮金黄
    },
    "policy": {
        "bg": (40, 42, 48),          # 深灰蓝
        "primary": (100, 140, 180),  # 明快天蓝
        "secondary": (55, 60, 70),   # 中灰蓝
        "accent": (170, 200, 235),   # 亮天蓝
    },
    "general": {
        "bg": (32, 40, 50),          # 深蓝灰 (更明快)
        "primary": (90, 160, 200),   # 明快青蓝
        "secondary": (55, 65, 80),   # 中灰蓝
        "accent": (160, 220, 255),   # 亮天蓝
    },
}


_FONT_CANDIDATES = [
    # Linux / Docker
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    # Windows
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """加载中英文字体，缺失时回退到 PIL 默认字体。"""
    candidates = list(_FONT_CANDIDATES)
    if bold:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ] + candidates
    for path in candidates:
        try:
            if os.path.exists(path):
                return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _fit_font(text: str, max_width: int, start_size: int, min_size: int = 26, bold: bool = True):
    """按宽度自适应字体大小。"""
    size = start_size
    while size >= min_size:
        font = _load_font(size, bold=bold)
        bbox = ImageDraw.Draw(Image.new("RGB", (10, 10))).textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return font
        size -= 2
    return _load_font(min_size, bold=bold)


def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int = 2) -> list[str]:
    """中英文混排标题折行。"""
    text = " ".join(str(text).strip().split())
    if not text:
        return []

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines: list[str] = []
    current = ""

    # 中文按字符、英文按词，避免长英文被拆得太碎。
    tokens: list[str] = []
    buf = ""
    for ch in text:
        if ord(ch) < 128 and (ch.isalnum() or ch in "-_."):
            buf += ch
        else:
            if buf:
                tokens.append(buf)
                buf = ""
            if ch.isspace():
                tokens.append(" ")
            else:
                tokens.append(ch)
    if buf:
        tokens.append(buf)

    for token in tokens:
        candidate = current + token
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current.strip())
        current = token.strip()
        if len(lines) >= max_lines:
            break

    if current and len(lines) < max_lines:
        lines.append(current.strip())

    if len(lines) == max_lines and "".join(lines) != text:
        last = lines[-1]
        while last:
            candidate = last + "..."
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if bbox[2] - bbox[0] <= max_width:
                lines[-1] = candidate
                break
            last = last[:-1]
    return lines


def _cover_story_title(news_list: list[dict], cover_subject: Optional[dict] = None) -> str:
    """封面标题永远取封面绑定的新闻，兜底取正文第一条。"""
    item = None
    if cover_subject:
        if cover_subject.get("cover_headline"):
            return str(cover_subject["cover_headline"]).strip()
        item = cover_subject.get("item")
    if not item and news_list:
        item = news_list[0]
    if not item:
        return "今日AI要闻"
    if item.get("cover_headline"):
        return str(item["cover_headline"]).strip()
    title = item.get("chinese_title") or item.get("title") or "今日AI要闻"
    return str(title).strip()


def _cover_kicker(date_str: str, item: Optional[dict] = None) -> str:
    source = ""
    if item:
        source = item.get("source") or item.get("source_type") or ""
        source = source.split(" + ")[0].strip()
    if source:
        return f"{date_str} · {source}"
    return date_str


def _download_image(url: str, timeout: int = 15) -> Optional[Image.Image]:
    """下载原文图并转为 RGB。"""
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer": f"{urlparse(url).scheme}://{urlparse(url).netloc}/",
            },
        )
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "").lower()
        if "svg" in content_type:
            return None
        img = Image.open(io.BytesIO(resp.content))
        img.load()
        return img.convert("RGB")
    except Exception as exc:
        logger.warning("Failed to download cover source image: %s", exc)
        return None


def _crop_cover(img: Image.Image, width: int = 900, height: int = 500) -> Image.Image:
    """居中裁剪到公众号封面比例。"""
    src_w, src_h = img.size
    target_ratio = width / height
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        new_w = int(src_h * target_ratio)
        left = (src_w - new_w) // 2
        img = img.crop((left, 0, left + new_w, src_h))
    else:
        new_h = int(src_w / target_ratio)
        top = max(0, (src_h - new_h) // 2)
        img = img.crop((0, top, src_w, top + new_h))
    return img.resize((width, height), Image.Resampling.LANCZOS)


def _draw_title_overlay(
    img: Image.Image,
    title: str,
    date_str: str,
    item: Optional[dict] = None,
) -> Image.Image:
    """给真实原文图加可控标题蒙层。"""
    width, height = img.size
    out = img.convert("RGB")
    draw = ImageDraw.Draw(out, "RGBA")

    # 底部渐变，保证白字可读，同时不遮住主图主体。
    for y in range(int(height * 0.45), height):
        ratio = (y - int(height * 0.45)) / (height - int(height * 0.45))
        alpha = int(190 * ratio)
        draw.rectangle([(0, y), (width, y + 1)], fill=(8, 12, 22, alpha))

    margin_x = 54
    title_font = _fit_font(title, width - margin_x * 2, 46, 28, bold=True)
    meta_font = _load_font(18, bold=False)
    lines = _wrap_text(title, title_font, width - margin_x * 2, max_lines=2)
    line_h = int(title_font.size * 1.25) if hasattr(title_font, "size") else 42
    block_h = len(lines) * line_h + 34
    y = height - block_h - 34

    kicker = _cover_kicker(date_str, item)
    draw.text((margin_x, y), kicker, font=meta_font, fill=(225, 231, 239, 215))
    y += 30
    for line in lines:
        draw.text((margin_x, y), line, font=title_font, fill=(255, 255, 255, 245))
        y += line_h

    return out


def _generate_cover_from_article_image(
    item: dict,
    date_str: str,
    output_path: str,
    width: int = 900,
    height: int = 500,
) -> Optional[str]:
    """使用真实原文图生成封面。"""
    image_url = item.get("cover_image_url") or ""
    img = _download_image(image_url)
    if not img:
        return None
    if img.width < 300 or img.height < 180:
        logger.info("Cover source image too small: %sx%s", img.width, img.height)
        return None

    title = item.get("cover_headline") or item.get("chinese_title") or item.get("title") or "今日AI要闻"
    cover = _crop_cover(img, width, height)
    cover = ImageEnhance.Color(cover).enhance(0.92)
    cover = ImageEnhance.Contrast(cover).enhance(0.96)
    cover = _draw_title_overlay(cover, str(title), date_str, item)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    cover.save(output_path, "JPEG", quality=92)
    logger.info("Generated cover from article image: %s", output_path)
    return output_path


def _generate_title_card_cover(
    news_list: list[dict],
    date_str: str,
    output_path: str,
    width: int = 900,
    height: int = 500,
    cover_title: str = "今日AI要闻",
    story_type: str = "general",
    cover_subject: Optional[dict] = None,
) -> str:
    """
    降级方案：Pillow 生成标题排版型封面。

    设计优化重点：
    - 标题智能截断到 15-20 字，避免拥挤
    - 使用更明快的配色（橙黄、青绿渐变）
    - 增大字号和留白，提升可读性
    - 简化元素，统一品牌为"今日AI要闻"
    """
    palette = _STORY_TYPE_PALETTE.get(story_type, _STORY_TYPE_PALETTE["general"])
    bg_color = palette["bg"]
    primary = palette["primary"]
    secondary = palette["secondary"]
    accent = palette["accent"]
    bound_item = cover_subject.get("item") if cover_subject else None
    title = _cover_story_title(news_list, cover_subject)

    # 标题智能截断：15-20 字最佳，超过则截断
    if len(title) > 20:
        title = _make_cover_headline(
            bound_item or news_list[0] if news_list else {},
            story_type
        )

    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img, "RGBA")

    # 背景：从深色到亮色的渐变，增加活力
    for y in range(height):
        ratio = y / height
        # 增强渐变对比度，从暗到中亮
        blend = tuple(
            int(bg_color[i] * (1 - ratio * 0.45) + secondary[i] * ratio * 0.45 + accent[i] * ratio * 0.15)
            for i in range(3)
        )
        draw.line([(0, y), (width, y)], fill=(*blend, 255))

    # 左侧装饰条 - 更细，更克制
    draw.rectangle([(0, 0), (12, height)], fill=(*accent, 200))

    # 右上角柔和色块 - 增加视觉趣味但不喧宾夺主
    draw.polygon(
        [(width - 280, 0), (width, 0), (width, int(height * 0.42))],
        fill=(*primary, 65),
    )

    # 柔和光晕质感
    texture = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(texture, "RGBA")
    for cx, cy, r, alpha in [
        (int(width * 0.82), int(height * 0.20), 140, 38),
        (int(width * 0.15), int(height * 0.85), 160, 25),
    ]:
        tdraw.ellipse([(cx-r, cy-r), (cx+r, cy+r)], fill=(*accent, alpha))
    texture = texture.filter(ImageFilter.GaussianBlur(40))
    img = Image.alpha_composite(img.convert("RGBA"), texture).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    # 文案区域 - 增大边距和字号
    margin_x = 72
    top_y = 85

    # 顶部品牌标识 - 统一为"今日AI要闻"
    brand_font = _load_font(22, bold=True)
    draw.text(
        (margin_x, top_y),
        "今日AI要闻",
        font=brand_font,
        fill=(255, 255, 255, 200)
    )

    # 日期标签
    date_font = _load_font(16, bold=False)
    draw.text(
        (margin_x, top_y + 35),
        date_str,
        font=date_font,
        fill=(*accent, 220)
    )

    # 主标题 - 更大字号，最多2行
    title_y = top_y + 85
    title_font = _fit_font(title, width - margin_x * 2 - 60, 62, 38, bold=True)
    lines = _wrap_text(title, title_font, width - margin_x * 2 - 60, max_lines=2)
    line_h = int(getattr(title_font, "size", 52) * 1.35)

    for line in lines:
        draw.text((margin_x, title_y), line, font=title_font, fill=(255, 255, 255, 255))
        title_y += line_h

    # 底部来源信息（如果有）
    if bound_item:
        source = bound_item.get("source") or bound_item.get("source_type") or ""
        source = source.split(" + ")[0].strip()
        if source:
            meta_font = _load_font(17, bold=False)
            meta_y = height - 68
            draw.text(
                (margin_x, meta_y),
                f"来源: {source}",
                font=meta_font,
                fill=(200, 210, 220, 180)
            )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path, "JPEG", quality=94)
    logger.info(
        "Generated editorial title-card cover (type=%s) at %s",
        story_type, output_path,
    )
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

    流程：
    1. 从正文 Top 1-3 选择与头条一致的封面主题。
    2. 优先使用该新闻的真实原文图生成封面。
    3. 没有真实原文图时，生成标题排版型编辑封面。
    4. AI 生图仅在 ENABLE_AI_COVER_GENERATION=1 时作为可选兜底。

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
    cover_title = cover_title or "今日AI要闻"

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

    bound_item = cover_subject.get("item") if cover_subject else None

    # 1. 优先用绑定新闻的真实原文图。
    if bound_item and bound_item.get("cover_image_url"):
        cover_from_image = _generate_cover_from_article_image(bound_item, date_str, output_path)
        if cover_from_image:
            cover_subject["cover_source"] = "first_article_image"
            return cover_from_image

    # 2. 如果绑定新闻无图，可从 Top 2-3 找同故事类型且有原文图的条目做背景。
    # 仍然使用绑定新闻标题叠加，避免封面主题漂移。
    target_type = cover_subject.get("story_type", "general") if cover_subject else "general"
    if bound_item:
        for item in news_list[:3]:
            if item is bound_item:
                continue
            if not item.get("cover_image_url"):
                continue
            if _classify_story_type(item) != target_type:
                continue
            proxy = dict(item)
            proxy["chinese_title"] = bound_item.get("chinese_title") or bound_item.get("title")
            proxy["title"] = bound_item.get("title") or bound_item.get("chinese_title")
            cover_from_related = _generate_cover_from_article_image(proxy, date_str, output_path)
            if cover_from_related:
                cover_subject["cover_source"] = "related_article_image"
                return cover_from_related

    # 3. 默认不再调用 AI 生图；无图时使用标题排版封面。
    ai_cover_enabled = _env_enabled_cover("ENABLE_AI_COVER_GENERATION", False)
    if not ai_cover_enabled:
        logger.info("AI cover generation disabled; using editorial title-card cover")
        if cover_subject is not None:
            cover_subject["cover_source"] = "editorial_title_card"
        return _fallback_cover(news_list, date_str, output_path, cover_title, cover_subject)

    if safe_cover_enabled and cover_subject.get("mode") == "generic":
        logger.info("Safe cover: no trusted candidate, using title-card cover")
        cover_subject["cover_source"] = "editorial_title_card"
        return _fallback_cover(news_list, date_str, output_path, cover_title, cover_subject)

    if not api_key:
        logger.warning("No API key for AI cover generation, using title-card cover")
        if cover_subject is not None:
            cover_subject["cover_source"] = "editorial_title_card"
        return _fallback_cover(news_list, date_str, output_path, cover_title, cover_subject)

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
                logger.info("FORCE_LOCAL_COVER_ON_BAD_IMAGE=1, using title-card cover")
                if cover_subject is not None:
                    cover_subject["cover_source"] = "editorial_title_card"
                return _fallback_cover(news_list, date_str, output_path, cover_title, cover_subject)
            # 否则继续使用 AI 图，但记录 warning

        # 5. 保存图片（不叠加任何文字 — 封面图片本体必须是纯视觉图）
        img.save(output_path, "JPEG", quality=90)
        ai_generated = True
        if cover_subject is not None:
            cover_subject["cover_source"] = "ai_generated"

        logger.info("Cover image generated and saved to %s (ai=%s, bad=%s)",
                     output_path, ai_generated, bad_ai)
        return output_path

    except Exception as e:
        logger.warning("Agnes Image API failed: %s, falling back to title-card cover", e)
        if cover_subject is not None:
            cover_subject["cover_source"] = "editorial_title_card"
        return _fallback_cover(news_list, date_str, output_path, cover_title, cover_subject)


# ═══════════════════════════════════════════════════════════════════
# 按故事类型的视觉 brief — 避免 generic AI network / glowing nodes
# ═══════════════════════════════════════════════════════════════════

_STORY_TYPE_VISUAL_BRIEF: dict[str, dict[str, str]] = {
    "personnel": {
        "scene": (
            "Professional figure silhouette standing at floor-to-ceiling office window, "
            "gazing at city skyline or glowing screens in distance. "
            "Modern glass office interior, warm sunset light streaming through windows. "
            "Subject positioned on left third of frame, facing right, leaving right side open for text."
        ),
        "mood": "Contemplative, sophisticated, corporate elegance. Warm amber and deep navy tones.",
        "avoid": "No faces visible, no UI screens with text, no company logos.",
    },
    "product": {
        "scene": (
            "Extreme close-up of sleek tech device edge - brushed metal surface with soft blue glow, "
            "abstract curved lines suggesting premium product design. "
            "Sharp focus on one corner, rest softly blurred. "
            "Cool teal and slate gray palette, studio lighting."
        ),
        "mood": "Premium, minimalist, Apple-like design aesthetic. Clean and futuristic.",
        "avoid": "No brand names, no interface elements, no readable screens.",
    },
    "model": {
        "scene": (
            "Floating translucent geometric layers in deep space - layered glass panels with subtle light passing through, "
            "forming abstract architectural structure. "
            "Dark indigo and violet background, soft purple accent lighting on edges. "
            "Isometric view, centered composition with generous margins."
        ),
        "mood": "Futuristic, precise, scientific blueprint aesthetic. Cool and ethereal.",
        "avoid": "No literal brain imagery, no glowing nodes/circuits, no data visualizations with numbers.",
    },
    "research": {
        "scene": (
            "Abstract laboratory workspace - clean white surface with geometric glass instruments, "
            "prism refracting light into subtle rainbow, molecular structure models as minimalist wireframes. "
            "Dark charcoal background with emerald accent lighting. "
            "Overhead flat-lay angle, asymmetric arrangement leaving space for text."
        ),
        "mood": "Scientific precision, editorial elegance, discovery and innovation.",
        "avoid": "No periodic table, no chemical formulas, no microscope images with labels.",
    },
    "business": {
        "scene": (
            "Modern glass skyscraper reflecting golden sunset sky, "
            "shot from ground level looking up at geometric window patterns. "
            "Warm gold and deep navy color palette, long exposure smooth clouds. "
            "Vertical composition with sky gradient at top for text overlay."
        ),
        "mood": "Ambitious, premium, corporate sophistication. Aspirational and powerful.",
        "avoid": "No company signage, no street signs, no visible people.",
    },
    "policy": {
        "scene": (
            "Government building facade abstracted into geometric forms - classical columns or modern civic architecture "
            "rendered with clean lines and symmetry. "
            "Muted steel blue and gray tones, overcast natural light, frontal symmetric view. "
            "Strong horizontal lines with clear space at top or bottom."
        ),
        "mood": "Authoritative, serious, institutional gravitas. Dignified and formal.",
        "avoid": "No flags with text, no visible signage, no recognizable monuments.",
    },
    "general": {
        "scene": (
            "Minimalist desk flatlay - clean white surface with abstract paper shapes, "
            "geometric wooden or metal objects casting soft shadows, "
            "subtle warm light from top-left creating depth. "
            "60% negative space on right side for text overlay. "
            "Warm beige and dark graphite color palette."
        ),
        "mood": "Editorial sophistication, curated daily briefing aesthetic, premium magazine quality.",
        "avoid": "No newspapers, no readable documents, no laptop screens.",
    },
}

# 禁止词清单 — 简洁有效
_AVOID_LIST = "No text, letters, words, numbers, characters, logos, or watermarks."


def _build_cover_prompt(cover_subject: dict) -> str:
    """
    构建封面图 prompt —— 简洁直观的画面描述。

    优化策略：
    - 用具体画面代替摄影术语
    - 纯英文 prompt，避免中文字符干扰
    - 精简禁止词，避免过度强调
    - 直接描述场景，不分段
    """
    mode = cover_subject.get("mode", "generic")
    story_type = cover_subject.get("story_type", "general")
    item = cover_subject.get("item")

    # 选择对应类型的视觉 brief
    if mode == "trusted" and story_type in _STORY_TYPE_VISUAL_BRIEF:
        brief = _STORY_TYPE_VISUAL_BRIEF[story_type]
    else:
        brief = _STORY_TYPE_VISUAL_BRIEF["general"]

    # 构建精简单段 prompt
    prompt = f"{brief['scene']} {brief['mood']} {brief['avoid']} {_AVOID_LIST}"

    # 限制长度，避免 API 拒绝
    if len(prompt) > 500:
        prompt = prompt[:500]

    return prompt


def _fallback_cover(
    news_list: list[dict],
    date_str: str,
    output_path: str = None,
    cover_title: str = "今日AI要闻",
    cover_subject: Optional[dict] = None,
) -> str:
    """降级：生成 Pillow 标题排版封面。"""
    if output_path is None:
        output_path = os.path.join("docs", "cover.jpg")
    story_type = "general"
    if cover_subject:
        story_type = cover_subject.get("story_type", "general")
    return _generate_title_card_cover(
        news_list, date_str, output_path,
        cover_title=cover_title,
        story_type=story_type,
        cover_subject=cover_subject,
    )


# ═══════════════════════════════════════════════════════════════════
# 封面主题选择
# ═══════════════════════════════════════════════════════════════════

_RUMOR_KEYWORDS = [
    "rumor", "ask hn", "speculation", "unconfirmed", "leak",
    "传闻", "疑似", "爆料", "谣言", "辟谣", "别信谣",
]


def _is_eligible_for_cover(item: dict) -> tuple[bool, str]:
    """检查单条新闻是否可作为封面主题。返回 (eligible, reason)。"""
    title = item.get("chinese_title") or item.get("title", "")
    title_lower = title.lower()

    if item.get("_cover_excluded"):
        return False, f"cover_excluded: {item['_cover_excluded']}"
    if item.get("_confidence_level") == "low":
        return False, "low confidence"
    bc = item.get("_brand_claim", {})
    if bc.get("confidence") == "low":
        return False, f"low confidence brand claim: {bc.get('brand', '')}"
    st = item.get("source_type", "")
    metrics = item.get("metrics", {}) or {}
    if st == "hn" and metrics.get("cross_source_count", 0) == 0 and metrics.get("hn_score", 0) < 20:
        return False, f"HN-only low heat (score={metrics.get('hn_score', 0)})"
    if any(kw in title_lower for kw in _RUMOR_KEYWORDS):
        return False, "rumor/speculation keyword in title"

    # 检查是否有视觉价值：纯 HN 讨论、纯代码仓库、无实体的论文标题
    # 如果标题是纯描述性/技术性的，没有实体（公司/产品/人物），视觉化难度高
    return True, "ok"


def _make_cover_headline(item: dict, story_type: str) -> str:
    """生成更适合封面的短标题，不改变正文标题。"""
    title = str(item.get("chinese_title") or item.get("title") or "").strip()
    if not title:
        return "今日AI要闻"

    # 清理信息噪声。
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"^(独家|重磅|快讯|突发)[:：]\s*", "", title)
    title = title.strip(" -_｜|")

    # 常见人事新闻，标题本身通常已经适合封面。
    if len(title) <= 24:
        return title

    # 对过长标题做保守压缩，不编造新事实。
    # 优先级1：寻找完整的前半段（冒号/逗号/分号分隔）
    for sep in ["：", ":", "，", ",", "；", ";"]:
        if sep in title:
            first = title.split(sep)[0].strip()
            if 8 <= len(first) <= 24:
                return first

    # 优先级2：如果有破折号或竖线，尝试保留前部
    for sep in [" - ", "｜", "|", " — "]:
        if sep in title:
            first = title.split(sep)[0].strip()
            if 8 <= len(first) <= 24:
                return first

    # 优先级3：智能截断 - 在词边界处截断，避免截断在"的/与/和"等连接词
    limit = 20 if story_type in ("personnel", "business", "policy") else 22
    if len(title) > limit:
        truncated = title[:limit]
        # 回退到最后一个完整词边界（避免在"的/与/和/及"等连接词处截断）
        for i in range(len(truncated) - 1, max(0, len(truncated) - 6), -1):
            if truncated[i] in "，。；：！？、,. 的与和及":
                truncated = truncated[:i]
                break
        return truncated.rstrip("，。；：！？、,. 的与和及") + "…"

    return title


_NO_VISUAL_VALUE_PATTERNS = [
    # 纯技术方法描述，没有故事性实体
    "show hn", "ask hn", "tell hn",
    "how to", "how i", "why i", "why we",
    "a survey of", "a review of",
]


def _has_visual_value(item: dict) -> tuple[bool, str]:
    """判断新闻是否有足够的视觉故事性来做封面。"""
    title = item.get("chinese_title") or item.get("title", "")
    title_lower = title.lower()
    summary = item.get("summary", "")

    # 无实体关键词的纯技术标题 → 无视觉价值
    for pat in _NO_VISUAL_VALUE_PATTERNS:
        if pat in title_lower:
            return False, f"no visual story: matches '{pat}'"

    # 检查是否包含实体关键词（公司/产品/人名/事件）
    entity_signals = [
        # 公司/品牌
        "openai", "google", "meta", "microsoft", "apple", "anthropic",
        "tesla", "nvidia", "amazon", "intel", "amd", "deepseek",
        "阿里", "腾讯", "百度", "字节", "华为", "小米",
        # 人物/职位
        "ceo", "cto", "离职", "加入", "任命", "卸任", "辞职",
        "resign", "hire", "appoint", "ceo",
        # 产品/发布
        "发布", "推出", "开源", "上线", "launch", "release",
        # 事件
        "融资", "收购", "上市", "诉讼", "版权", "监管",
        "fund", "acquire", "ipo", "lawsuit", "regulation",
    ]
    text = title + " " + summary
    has_entity = any(sig in text.lower() for sig in entity_signals)
    if not has_entity:
        return False, "no recognizable entity/product/event for visual storytelling"

    return True, "ok"


def select_cover_subject(news_list: list[dict]) -> dict:
    """
    从正文 Top 1-3 中选择封面主题。

    规则（按用户要求）：
    1. 默认绑定第 1 条新闻
    2. 只有第 1 条被 _cover_excluded、low confidence、传闻、无视觉价值时，
       才从 Top 2-3 中依次尝试
    3. 不能从 Top 3 以外选封面主题
    4. Top 3 全都不合格时使用 generic 中性封面

    Returns:
        {
            "mode": "trusted" | "generic",
            "item": dict or None,
            "cover_title": str,
            "visual_prompt_topic": str,
            "story_type": str,
            "reason": str,
            "excluded": list,
        }
    """
    if not news_list:
        return {
            "mode": "generic",
            "item": None,
            "cover_title": "今日AI要闻",
            "visual_prompt_topic": "a curated daily briefing about artificial intelligence",
            "story_type": "general",
            "reason": "empty news list",
        }

    top3 = news_list[:3]
    excluded_info: list[dict] = []

    for i, item in enumerate(top3):
        title = item.get("chinese_title") or item.get("title", "")

        # 检查资格
        eligible, reason = _is_eligible_for_cover(item)
        if not eligible:
            excluded_info.append({"title": title[:60], "reason": reason, "rank": i + 1})
            logger.info("Cover #%d excluded: %s — %s", i + 1, title[:40], reason)
            continue

        # 检查视觉价值
        has_value, v_reason = _has_visual_value(item)
        if not has_value:
            excluded_info.append({"title": title[:60], "reason": v_reason, "rank": i + 1})
            logger.info("Cover #%d no visual value: %s — %s", i + 1, title[:40], v_reason)
            continue

        # 合格！绑定此条为封面主题
        story_type = _classify_story_type(item)
        cover_headline = _make_cover_headline(item, story_type)
        item["cover_headline"] = cover_headline
        logger.info(
            "Cover subject: #%d trusted (%s) — '%s'",
            i + 1, story_type, title[:50],
        )
        return {
            "mode": "trusted",
            "item": item,
            "cover_title": cover_headline,
            "cover_headline": cover_headline,
            "visual_prompt_topic": _extract_topic_for_visual(item),
            "story_type": story_type,
            "reason": f"#{(i + 1)} in top 3, story_type={story_type}",
            "excluded": excluded_info,
        }

    # Top 3 全都不合格 — 使用中性抽象封面
    logger.warning(
        "Cover subject: generic — all top 3 excluded (%d reasons)",
        len(excluded_info),
    )
    return {
        "mode": "generic",
        "item": None,
        "cover_title": "今日AI要闻",
        "visual_prompt_topic": (
            "a curated daily briefing about artificial intelligence "
            "industry, research and developer tools"
        ),
        "story_type": "general",
        "reason": f"no eligible item in top 3 (all {len(excluded_info)} excluded)",
        "excluded": excluded_info,
    }


def _classify_story_type(item: dict) -> str:
    """
    根据新闻标题和摘要判断故事类型。

    Returns one of:
        personnel, product, model, research, business, policy, general
    """
    title = (item.get("chinese_title") or item.get("title", "")).lower()
    summary = (item.get("summary", "")).lower()
    combined = title + " " + summary

    # 人物变动
    personnel_kw = [
        "离职", "卸任", "加入", "任命", "辞职", "跳槽", "裁员",
        "ceo", "cto", "高管", "人事", "创始人",
        "resign", "step down", "leave", "join", "appoint",
        "hire", "chief", "executive", "depart",
    ]
    if any(kw in combined for kw in personnel_kw):
        return "personnel"

    # 模型发布
    model_kw = [
        "模型", "开源模型", "大模型", "参数", "权重",
        "checkpoint", "weights", "gpt-", "claude", "gemini", "llama",
        "deepseek", "qwen", "mistral", "diffusion",
    ]
    if any(kw in combined for kw in model_kw):
        return "model"

    # 产品发布
    product_kw = [
        "发布", "推出", "上线", "新功能", "更新", "升级", "插件",
        "launch", "release", "new feature", "update", "roll out",
        "app", "tool", "platform", "编辑器", "助手",
    ]
    if any(kw in combined for kw in product_kw):
        return "product"

    # 研究论文
    research_kw = [
        "论文", "arxiv", "研究", "实验", "benchmark", "基准",
        "survey", "综述", "paper", "study", "method",
        "approach", "framework", "novel",
    ]
    source_type = item.get("source_type", "")
    if source_type == "arxiv" or any(kw in combined for kw in research_kw):
        return "research"

    # 公司商业
    business_kw = [
        "融资", "收购", "财报", "营收", "ipo", "上市", "估值",
        "fund", "acquire", "revenue", "valuation", "invest",
        "startup", "股价", "商业",
    ]
    if any(kw in combined for kw in business_kw):
        return "business"

    # 政策监管
    policy_kw = [
        "监管", "政策", "法律", "诉讼", "版权", "合规",
        "regulation", "policy", "lawsuit", "copyright", "ban",
        "隐私", "隐私", "安全", "欧盟", "国会", "政府",
    ]
    if any(kw in combined for kw in policy_kw):
        return "policy"

    return "general"


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
