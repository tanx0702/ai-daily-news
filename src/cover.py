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
    降级方案：用 Pillow 生成纯色背景封面图。
    使用动态中文封面标题。
    """
    # 配色方案
    PALETTES = [
        ("#6366f1", "#8b5cf6"),  # 紫蓝
        ("#3b82f6", "#06b6d4"),  # 蓝青
        ("#8b5cf6", "#ec4899"),  # 紫粉
        ("#10b981", "#3b82f6"),  # 绿蓝
        ("#f59e0b", "#ef4444"),  # 橙红
        ("#14b8a6", "#6366f1"),  # 青紫
    ]

    import hashlib
    day_hash = int(hashlib.md5(date_str.encode()).hexdigest()[:8], 16)
    palette = PALETTES[day_hash % len(PALETTES)]

    img = Image.new("RGB", (width, height), palette[0])
    draw = ImageDraw.Draw(img)

    # 渐变覆盖
    def hex_to_rgb(c):
        c = c.lstrip("#")
        return tuple(int(c[i:i+2], 16) for i in (0, 2, 4))

    r0, g0, b0 = hex_to_rgb(palette[0])
    r1, g1, b1 = hex_to_rgb(palette[1])
    for y in range(height):
        ratio = y / height
        r = int(r0 + (r1 - r0) * ratio)
        g = int(g0 + (g1 - g0) * ratio)
        b = int(b0 + (b1 - b0) * ratio)
        draw.rectangle([(0, y), (width, y + 1)], fill=f"#{r:02x}{g:02x}{b:02x}")

    # 叠加文字
    try:
        img = _draw_cover_text(img, cover_title, date_str, len(news_list))
    except Exception as e:
        logger.warning("Cover text overlay failed: %s, saving bare image", e)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path, "JPEG", quality=90)
    logger.info("Generated simple cover at %s", output_path)
    return output_path


def generate_cover_from_news(
    news_list: list[dict],
    date_str: str,
    output_path: str = None,
    api_key: Optional[str] = None,
    base_url: str = "https://apihub.agnes-ai.com",
    cover_title: str = "",
) -> Optional[str]:
    """
    根据新闻标题生成封面图。

    流程：
    1. 从新闻标题提取关键词
    2. 构建 prompt 调用 Agnes Image API
    3. 下载生成的图片，叠加中文封面标题
    4. 如果 API 失败，降级为 Pillow 生成

    Args:
        news_list: 新闻列表
        date_str: 日期字符串
        output_path: 输出图片路径
        api_key: Agnes API Key
        base_url: Agnes API 基础地址
        cover_title: 中文封面标题（12-20 字），为空则使用 "AI 日报"

    Returns:
        封面图路径，失败返回 None
    """
    cover_title = cover_title or "AI 日报"

    api_key = api_key or os.environ.get("AGNES_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("No API key for cover generation, falling back to simple cover")
        return _fallback_cover(news_list, date_str, output_path, cover_title)

    if output_path is None:
        output_path = os.path.join("docs", "cover.jpg")

    # 1. 以头条新闻为主题构建 prompt
    prompt = _build_cover_prompt(news_list, date_str)

    # 2. 调用 Agnes Image API
    # 确保 base_url 不以 / 结尾
    base_url = base_url.rstrip("/")
    # Images API 固定使用 /v1/images/generations，不与 chat completions 共享路径
    image_base = base_url.replace("/v1", "") if base_url.endswith("/v1") else base_url
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

        # 4. 叠加中文标题（失败时保留原图）
        try:
            img = Image.open(io.BytesIO(img_resp.content))
            img = _draw_cover_text(img, cover_title, date_str, len(news_list))
            img.save(output_path, "JPEG", quality=90)
        except Exception as e:
            logger.warning("Cover text overlay failed: %s, saving bare image", e)
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(img_resp.content)

        logger.info("Cover image generated and saved to %s", output_path)
        return output_path

    except Exception as e:
        logger.warning("Agnes Image API failed: %s, falling back to simple cover", e)
        return _fallback_cover(news_list, date_str, output_path, cover_title)


def _build_cover_prompt(news_list: list[dict], date_str: str) -> str:
    """
    以当天头条新闻为主题构建封面图 prompt。

    思路：取评分最高的第 1 条新闻作为画面主题，让每天的封面
    都贴合当日最重要的 AI 事件，而不是千篇一律的抽象图案。

    注意：图像模型只理解英文，prompt 必须全英文。
    中文标题通过 Pillow 后期叠加到图片上。
    """
    if not news_list:
        return (
            "A modern tech magazine cover, abstract AI neural network visualization, "
            "glowing data nodes connected by fine lines, deep blue and indigo gradient, "
            "cinematic lighting, clean composition, "
            "leave top third of image dark/empty for text overlay, "
            "16:9 aspect ratio, professional quality."
        )

    top = news_list[0]

    # 提取主题关键词（优先英文标题，中文稿从 summary 提取英文词）
    english_title = top.get("title", "")
    has_chinese = any('一' <= c <= '鿿' for c in english_title)
    if has_chinese:
        import re as _re
        summary = top.get("summary", "")
        # 提取英文专有名词和长单词
        en_words = _re.findall(r'[A-Z][a-z]+|[A-Z]{2,}|[a-z]{4,}', english_title + " " + summary)
        # 去重并过滤常见停用词
        stop = {'this', 'that', 'with', 'from', 'have', 'been', 'they', 'them', 'their',
                'your', 'will', 'would', 'could', 'about', 'which', 'when', 'where'}
        en_words = [w for w in en_words if w.lower() not in stop]
        english_title = " ".join(en_words[:10]) if en_words else "AI technology innovation"
        if not english_title.strip():
            english_title = "AI technology innovation"

    # 确保纯英文 prompt
    topic_line = (
        f"Visual theme inspired by today's top AI headline: \"{english_title[:150]}\". "
        if english_title else ""
    )

    return (
        f"A modern tech magazine cover illustration. "
        f"{topic_line}"
        f"Style: clean minimalist composition, cinematic lighting, "
        f"deep navy blue and indigo purple gradient background, "
        f"abstract geometric tech elements subtly related to the theme, "
        f"professional and eye-catching, suitable for a daily AI newsletter cover. "
        f"Leave the bottom third of the image dark or gradient-fade for text overlay. "
        f"Do NOT put any text, letters, typography, characters, words, or watermarks "
        f"anywhere in the image — text will be added separately. "
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
