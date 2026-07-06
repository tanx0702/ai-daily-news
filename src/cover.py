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


def _generate_simple_cover(
    news_list: list[dict],
    date_str: str,
    output_path: str,
    width: int = 900,
    height: int = 500,
) -> str:
    """
    降级方案：用 Pillow 生成纯色背景封面图。
    取第一条新闻的主色调关键词决定背景色。
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

    # 标题文字
    try:
        font_title = ImageFont.truetype("msyh.ttc", 48)
        font_date = ImageFont.truetype("msyh.ttc", 28)
        font_count = ImageFont.truetype("msyh.ttc", 22)
    except IOError:
        font_title = ImageFont.load_default()
        font_date = font_title
        font_count = font_title

    title_text = "AI 日报"
    date_text = date_str
    count_text = f"今日 {len(news_list)} 条 AI 新闻"

    # 居中计算
    _, _, tw, _ = draw.textbbox((0, 0), title_text, font=font_title)
    _, _, dw, _ = draw.textbbox((0, 0), date_text, font=font_date)
    _, _, cw, _ = draw.textbbox((0, 0), count_text, font=font_count)

    draw.text(((width - tw) // 2, 160), title_text, fill="#ffffff", font=font_title)
    draw.text(((width - dw) // 2, 220), date_text, fill="#cccccc", font=font_date)
    draw.text(((width - cw) // 2, 270), count_text, fill="#999999", font=font_count)

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
) -> Optional[str]:
    """
    根据新闻标题生成封面图。

    流程：
    1. 从新闻标题提取关键词
    2. 构建 prompt 调用 Agnes Image API
    3. 下载生成的图片保存到 output_path
    4. 如果 API 失败，降级为 Pillow 生成

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
    if not api_key:
        logger.warning("No API key for cover generation, falling back to simple cover")
        return _fallback_cover(news_list, date_str, output_path)

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
        with open(output_path, "wb") as f:
            f.write(img_resp.content)

        logger.info("Cover image generated and saved to %s", output_path)
        return output_path

    except Exception as e:
        logger.warning("Agnes Image API failed: %s, falling back to simple cover", e)
        return _fallback_cover(news_list, date_str, output_path)


def _build_cover_prompt(news_list: list[dict], date_str: str) -> str:
    """
    以当天头条新闻为主题构建封面图 prompt。

    思路：取评分最高的第 1 条新闻作为画面主题，让每天的封面
    都贴合当日最重要的 AI 事件，而不是千篇一律的抽象图案。
    """
    if not news_list:
        return (
            "A modern tech magazine cover, abstract AI neural network visualization, "
            "glowing data nodes connected by fine lines, deep blue and indigo gradient, "
            "cinematic lighting, clean composition, NO text NO letters NO typography, "
            "16:9 aspect ratio, professional quality."
        )

    top = news_list[0]

    # 用英文标题作为主题线索（海外源英文标题，国内源 chinese_title 是中文 → 过滤掉）
    english_title = top.get("title", "")
    # 标题是否包含中文（图像模型看不懂中文，会乱画）
    has_chinese = any('一' <= c <= '鿿' for c in english_title)
    if has_chinese:
        # 国内源：从摘要提取英文关键词，或使用通用 AI 主题
        summary = top.get("summary", "")
        # 简单提取英文单词
        import re as _re
        en_words = _re.findall(r'[A-Za-z][a-z]{3,}', english_title + " " + summary)
        english_title = " ".join(en_words[:8]) if en_words else "artificial intelligence technology"

    # 确保纯英文 prompt，图像模型不会试图画中文字
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
        f"CRITICAL: absolutely NO text, NO letters, NO typography, NO characters, "
        f"NO words, NO watermarks anywhere in the image. "
        f"16:9 aspect ratio, high quality."
    )


def _fallback_cover(
    news_list: list[dict],
    date_str: str,
    output_path: str = None,
) -> str:
    """降级：生成 Pillow 封面图。"""
    if output_path is None:
        output_path = os.path.join("docs", "cover.jpg")
    return _generate_simple_cover(news_list, date_str, output_path)
