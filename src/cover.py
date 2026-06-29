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
    for y in range(height):
        ratio = y / height
        r = int(palette[0][:2], 16) + int(int(palette[1][:2], 16) - int(palette[0][:2], 16)) * ratio
        g = int(palette[0][2:4], 16) + int(int(palette[1][2:4], 16) - int(palette[0][2:4], 16)) * ratio
        b = int(palette[0][4:6], 16) + int(int(palette[1][4:6], 16) - int(palette[0][4:6], 16)) * ratio
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
    draw.text(((width - dw) // 2, 220), date_text, fill="rgba(255,255,255,0.8)", font=font_date)
    draw.text(((width - cw) // 2, 270), count_text, fill="rgba(255,255,255,0.6)", font=font_count)

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

    # 1. 提取新闻标题关键词构建 prompt
    headlines = [item["title"] for item in news_list[:5]]
    prompt = _build_cover_prompt(headlines, date_str)

    # 2. 调用 Agnes Image API
    try:
        logger.info("Generating cover image with prompt: %s", prompt[:80])
        resp = requests.post(
            f"{base_url}/v1/images/generations",
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


def _build_cover_prompt(headlines: list[str], date_str: str) -> str:
    """从新闻标题构建封面图 prompt。"""
    # 把标题翻译成英文关键词（简单拼接，让图像模型更好理解）
    news_summary = ", ".join(headlines[:5])
    return (
        f"A modern minimalist tech magazine cover for 'AI Daily News {date_str}', "
        f"featuring abstract artificial intelligence concepts: neural networks, "
        f"digital circuits, data streams, glowing nodes. "
        f"Color scheme: deep blue and purple gradient, clean professional layout, "
        f"high information density, cinematic lighting, "
        f"no text or typography, "
        f"related topics: {news_summary}"
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
