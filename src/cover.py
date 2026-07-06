"""
封面图生成模块

双路径策略：
1. AI 封面：Agnes Image API 生成抽象背景 → Pillow 叠加文字
2. 降级：纯 Pillow 生成几何风格封面（无 API 依赖，零成本）
"""

import io
import logging
import os
import re
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

# 配色方案（6 套，按日期 hash 轮换）
PALETTES = [
    # (主色, 辅色, 强调色)
    ("#1E1B4B", "#312E81", "#6366F1"),  # 深紫蓝
    ("#0F172A", "#1E3A5F", "#3B82F6"),  # 深海蓝
    ("#1A0A2E", "#2D1B69", "#8B5CF6"),  # 深紫
    ("#0A1628", "#0F3460", "#06B6D4"),  # 深青蓝
    ("#1A1A2E", "#16213E", "#10B981"),  # 深绿蓝
    ("#1F1A2E", "#3B1F5E", "#EC4899"),  # 深粉紫
]


def _find_font(size: int) -> ImageFont.FreeTypeFont:
    """
    查找可用的中文字体，多路径尝试。
    优先 Noto Sans CJK（Docker），其次微软雅黑（Windows），最后默认字体。
    """
    FONT_CANDIDATES = [
        # Docker / Linux（WenQuanYi Micro Hei，小体积）
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy-microhei/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        # Windows
        "msyh.ttc", "msyhbd.ttc",
        "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ]
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    # 最终降级
    return ImageFont.load_default()


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _draw_gradient_bg(
    draw: ImageDraw.Draw,
    width: int, height: int,
    start_color: str, end_color: str,
) -> None:
    """纵向渐变背景。"""
    r0, g0, b0 = _hex_to_rgb(start_color)
    r1, g1, b1 = _hex_to_rgb(end_color)
    for y in range(height):
        t = y / height
        r = int(r0 + (r1 - r0) * t)
        g = int(g0 + (g1 - g0) * t)
        b = int(b0 + (b1 - b0) * t)
        draw.rectangle([(0, y), (width, y + 1)], fill=f"#{r:02x}{g:02x}{b:02x}")


def _draw_decorative_circles(
    draw: ImageDraw.Draw,
    width: int, height: int,
    accent: str,
) -> None:
    """绘制装饰性半透明圆形。"""
    from PIL import Image as PILImage
    r, g, b = _hex_to_rgb(accent)

    circles = [
        (int(width * 0.78), int(height * -0.10), int(width * 0.55)),   # 右上大圆
        (int(width * 0.12), int(height * 0.85), int(width * 0.25)),   # 左下小圆
        (int(width * 0.92), int(height * 0.70), int(width * 0.12)),   # 右中小圆
        (int(width * 0.05), int(height * 0.20), int(width * 0.08)),   # 左上极小
    ]

    for cx, cy, radius in circles:
        # 绘制带透明度的圆形（通过 alpha 混合模拟）
        for offset_y in range(max(0, cy - radius), min(height, cy + radius)):
            for offset_x in range(max(0, cx - radius), min(width, cx + radius)):
                dist_sq = (offset_x - cx) ** 2 + (offset_y - cy) ** 2
                if dist_sq <= radius ** 2:
                    # 距边缘越近越透明
                    edge_dist = radius - (dist_sq ** 0.5)
                    alpha = min(0.12, edge_dist / radius * 0.12)
                    # 读取当前像素
                    pixel = draw._image.getpixel((offset_x, offset_y))
                    pr = int(pixel[0] * (1 - alpha) + r * alpha)
                    pg = int(pixel[1] * (1 - alpha) + g * alpha)
                    pb = int(pixel[2] * (1 - alpha) + b * alpha)
                    draw.point((offset_x, offset_y), fill=(pr, pg, pb))


def _draw_dot_grid(
    draw: ImageDraw.Draw,
    width: int, height: int,
    spacing: int = 30, alpha: float = 0.06,
) -> None:
    """绘制点阵纹理。"""
    color = (255, 255, 255)
    for y in range(spacing, height, spacing):
        for x in range(spacing, width, spacing):
            # 随机跳过一些点，避免过于规律
            if (x // spacing + y // spacing) % 3 == 0:
                draw.ellipse([(x - 1, y - 1), (x + 1, y + 1)], fill=color)
            # 在跳过位置画更小的点
            elif (x // spacing + y // spacing) % 3 == 2:
                draw.point((x, y), fill=color)


def _overlay_text_on_image(
    img: Image.Image,
    date_str: str,
    news_count: int,
) -> Image.Image:
    """
    在现有图片上叠加文字（用于 AI 生成图的后处理）。
    底部加半透明暗色渐变条，确保白色文字可读。
    """
    width, height = img.size
    draw = ImageDraw.Draw(img)

    # 底部半透明遮罩（从透明到半透明黑）
    overlay_height = height // 3
    for y in range(height - overlay_height, height):
        t = (y - (height - overlay_height)) / overlay_height
        alpha = int(t * 140)  # 0 → 140
        if alpha > 0:
            draw.rectangle(
                [(0, y), (width, y + 1)],
                fill=(0, 0, 0, alpha) if img.mode == "RGBA" else (0, 0, 0),
            )

    # 文字
    font_title = _find_font(52)
    font_sub = _find_font(22)

    title = "AI 日报"
    date_text = date_str
    count_text = f"今日精选 {news_count} 条 AI 新闻"

    # 居中
    bbox_t = draw.textbbox((0, 0), title, font=font_title)
    bbox_d = draw.textbbox((0, 0), date_text, font=font_sub)
    bbox_c = draw.textbbox((0, 0), count_text, font=font_sub)

    title_x = (width - (bbox_t[2] - bbox_t[0])) // 2
    date_x = (width - (bbox_d[2] - bbox_d[0])) // 2
    count_x = (width - (bbox_c[2] - bbox_c[0])) // 2

    y_base = height - overlay_height + 20

    draw.text((title_x, y_base), title, fill=(255, 255, 255), font=font_title)
    draw.text((date_x, y_base + 58), date_text, fill=(200, 200, 220), font=font_sub)
    draw.text((count_x, y_base + 84), count_text, fill=(180, 180, 200), font=font_sub)

    return img


def _generate_pillow_cover(
    news_list: list[dict],
    date_str: str,
    output_path: str,
    width: int = 900,
    height: int = 500,
) -> str:
    """
    纯 Pillow 封面生成 — 几何抽象风格。

    设计元素：
    - 深色渐变背景
    - 装饰性半透明圆形
    - 点阵纹理
    - 粗体标题 + 阴影
    - 日期、条数统计
    """
    import hashlib

    day_hash = int(hashlib.md5(date_str.encode()).hexdigest()[:8], 16)
    palette = PALETTES[day_hash % len(PALETTES)]
    primary, secondary, accent = palette

    img = Image.new("RGB", (width, height), primary)
    draw = ImageDraw.Draw(img)

    # 1. 渐变背景
    _draw_gradient_bg(draw, width, height, primary, secondary)

    # 2. 装饰性圆形
    _draw_decorative_circles(draw, width, height, accent)

    # 3. 点阵纹理
    _draw_dot_grid(draw, width, height)

    # 4. 文字
    font_title = _find_font(60)
    font_date = _find_font(26)
    font_count = _find_font(22)
    font_small = _find_font(16)

    title = "AI 日报"
    date_text = date_str
    count_text = f"今日精选 {len(news_list)} 条 AI 新闻"
    tagline = "AI DAILY NEWS"

    # 文字阴影（标题）
    bbox_t = draw.textbbox((0, 0), title, font=font_title)
    tw, th = bbox_t[2] - bbox_t[0], bbox_t[3] - bbox_t[1]
    tx = (width - tw) // 2
    ty = int(height * 0.24)

    shadow_color = _hex_to_rgb(accent)
    # 绘制发光阴影效果（多层偏移）
    for offset, alpha in [(4, 60), (2, 100)]:
        sr = int(shadow_color[0] * alpha / 255)
        sg = int(shadow_color[1] * alpha / 255)
        sb = int(shadow_color[2] * alpha / 255)
        draw.text((tx + offset, ty + offset), title, fill=(sr, sg, sb), font=font_title)

    # 主标题
    draw.text((tx, ty), title, fill=(255, 255, 255), font=font_title)

    # 日期
    bbox_d = draw.textbbox((0, 0), date_text, font=font_date)
    dw = bbox_d[2] - bbox_d[0]
    draw.text(((width - dw) // 2, ty + th + 20), date_text, fill=(180, 185, 210), font=font_date)

    # 装饰线
    line_y = ty + th + 58
    line_w = 60
    lx = (width - line_w) // 2
    draw.rectangle([(lx, line_y), (lx + line_w, line_y + 2)], fill=accent)

    # 条数
    bbox_c = draw.textbbox((0, 0), count_text, font=font_count)
    cw = bbox_c[2] - bbox_c[0]
    draw.text(((width - cw) // 2, line_y + 20), count_text, fill=(140, 145, 170), font=font_count)

    # 英文 tagline（右下角小字）
    bbox_s = draw.textbbox((0, 0), tagline, font=font_small)
    sw = bbox_s[2] - bbox_s[0]
    draw.text((width - sw - 30, height - 36), tagline, fill=(100, 105, 130), font=font_small)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path, "JPEG", quality=92)
    logger.info("Generated pillow cover at %s", output_path)
    return output_path


def _build_cover_prompt(news_list: list[dict], date_str: str) -> str:
    """
    构建 AI 封面图 prompt。

    策略：让 AI 生成抽象科技背景（不包含文字），
    之后用 Pillow 叠加清晰的中文标题。
    """
    # 取前 3 条新闻的关键词给 AI 做主题参考
    keywords: list[str] = []
    for item in news_list[:3]:
        title = item.get("chinese_title") or item.get("title", "")
        # 提取中文关键词（简单按标点分割取前几个字）
        parts = re.split(r'[，,。；;！!、\s]', title)
        for p in parts:
            p = p.strip().strip('"').strip("'")
            if len(p) >= 4 and len(p) <= 15:
                keywords.append(p)
                break

    theme_hint = ", ".join(keywords[:3]) if keywords else "artificial intelligence technology"

    return (
        f"Abstract digital art background for a technology magazine, "
        f"theme: {theme_hint}. "
        f"Style: minimalist geometric composition, flowing data streams, "
        f"soft glowing nodes connected by thin lines, "
        f"deep navy blue and indigo color palette with subtle purple accents, "
        f"smooth gradients, clean and modern aesthetic, "
        f"no text, no letters, no typography, no watermarks, "
        f"high resolution, 16:9 aspect ratio, professional quality"
    )


def generate_cover_from_news(
    news_list: list[dict],
    date_str: str,
    output_path: str = None,
    api_key: Optional[str] = None,
    base_url: str = "https://apihub.agnes-ai.com",
) -> Optional[str]:
    """
    生成每日封面图。

    流程：
    1. 尝试 AI 生成抽象背景 + Pillow 叠加文字
    2. 失败则降级为纯 Pillow 几何风格封面

    Args:
        news_list: 新闻列表
        date_str: 日期字符串
        output_path: 输出图片路径
        api_key: Agnes API Key
        base_url: Agnes API 基础地址

    Returns:
        封面图路径，失败返回 None
    """
    api_key = api_key or os.environ.get("AGNES_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if output_path is None:
        output_path = os.path.join("docs", "cover.jpg")

    news_count = len(news_list)

    # 尝试 AI 生成
    if api_key:
        prompt = _build_cover_prompt(news_list, date_str)
        base_url_clean = base_url.rstrip("/")
        image_base = base_url_clean.replace("/v1", "") if base_url_clean.endswith("/v1") else base_url_clean

        try:
            logger.info("AI cover: generating background image...")
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

            if image_url:
                img_resp = requests.get(image_url, timeout=30)
                img_resp.raise_for_status()

                ai_img = Image.open(io.BytesIO(img_resp.content)).convert("RGBA")
                ai_img = ai_img.resize((900, 500), Image.LANCZOS)

                # Pillow 叠加文字
                final_img = _overlay_text_on_image(ai_img, date_str, news_count)

                os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
                final_img.convert("RGB").save(output_path, "JPEG", quality=92)
                logger.info("AI cover saved to %s", output_path)
                return output_path

        except Exception as e:
            logger.warning("AI cover failed (%s), falling back to pillow", e)

    # 降级：纯 Pillow 几何封面
    logger.info("Using pillow-generated cover")
    return _generate_pillow_cover(news_list, date_str, output_path)
