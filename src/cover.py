"""
封面图生成模块

使用可配置图片生成 API 根据当日新闻标题生成每日封面图。
"""

import io
import logging
import os
import re
from typing import Optional
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from src.llm_config import DEFAULT_IMAGE_MODEL, resolve_image_llm_config
from src.text_utils import clean_display_text

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
# 1. 优先使用可信封面主题绑定的真实原文图。
# 2. 没有可信原文图时，优先使用 AI 生图。
# 3. AI 生图失败或被质量检测拦截时，降级为极简本地无字背景。
# 4. 封面图片本体不再渲染大标题，避免自动海报感。


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
    # Linux / Docker (wqy-zenhei 是 Dockerfile 中安装的字体)
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
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
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ] + candidates

    for path in candidates:
        try:
            if os.path.exists(path):
                logger.debug("Using font: %s (size=%d, bold=%s)", path, size, bold)
                return ImageFont.truetype(path, size=size)
        except Exception as e:
            logger.debug("Failed to load font %s: %s", path, e)
            continue

    logger.warning("No suitable font found, falling back to PIL default (will show garbled Chinese)")
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


def _cover_kicker(date_str: str, item: Optional[dict] = None) -> str:
    source = ""
    if item:
        source = clean_display_text(item.get("source") or item.get("source_type") or "")
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

    title = clean_display_text(
        item.get("cover_headline") or item.get("chinese_title") or item.get("title") or "今日AI要闻"
    )
    cover = _crop_cover(img, width, height)
    cover = ImageEnhance.Color(cover).enhance(0.92)
    cover = ImageEnhance.Contrast(cover).enhance(0.96)
    cover = _draw_title_overlay(cover, str(title), date_str, item)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    cover.save(output_path, "JPEG", quality=92)
    logger.info("Generated cover from article image: %s", output_path)
    return output_path


def _blend_color(a: tuple[int, int, int], b: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    return tuple(int(a[i] * (1 - ratio) + b[i] * ratio) for i in range(3))


def _generate_minimal_background_cover(
    output_path: str,
    cover_subject: Optional[dict] = None,
    width: int = 900,
    height: int = 500,
) -> str:
    """本地兜底封面：只生成无字视觉背景，不再绘制标题卡片。"""
    story_type = "general"
    if cover_subject:
        story_type = cover_subject.get("story_type", "general")
    palette = _STORY_TYPE_PALETTE.get(story_type, _STORY_TYPE_PALETTE["general"])
    bg = palette["bg"]
    primary = palette["primary"]
    secondary = palette["secondary"]
    accent = palette["accent"]

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = _blend_color(bg, secondary, ratio * 0.72)
        draw.line([(0, y), (width, y)], fill=color)

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    # 细网格，保持“技术感”但不抢主体。
    for x in range(0, width, 72):
        od.line([(x, 0), (x, height)], fill=(*primary, 12), width=1)
    for y in range(0, height, 72):
        od.line([(0, y), (width, y)], fill=(*primary, 10), width=1)

    # 模型层 / 电路层视觉母题。
    center_x, center_y = int(width * 0.52), int(height * 0.50)
    layer_specs = [
        (0, 0, 240, 108, 54),
        (-34, -30, 194, 78, 38),
        (-70, -58, 160, 54, 26),
    ]
    for dx0, dy0, dx1, dy1, alpha in layer_specs:
        od.rounded_rectangle(
            [
                center_x - 200 + dx0,
                center_y - 78 + dy0,
                center_x + 120 + dx1,
                center_y + 78 + dy1,
            ],
            radius=24,
            outline=(*accent, alpha),
            width=2,
        )

    # 节点连线，暗示 AI 网络结构。
    nodes = [
        (int(width * 0.16), int(height * 0.72)),
        (int(width * 0.30), int(height * 0.58)),
        (int(width * 0.44), int(height * 0.64)),
        (int(width * 0.58), int(height * 0.46)),
        (int(width * 0.72), int(height * 0.56)),
        (int(width * 0.84), int(height * 0.34)),
    ]
    for left, right in zip(nodes, nodes[1:]):
        od.line([left, right], fill=(*secondary, 90), width=3)
        mx = (left[0] + right[0]) // 2
        my = (left[1] + right[1]) // 2
        od.line([(mx, my - 4), (mx + 18, my + 8)], fill=(*accent, 45), width=2)

    for px, py in nodes:
        od.ellipse((px - 7, py - 7, px + 7, py + 7), fill=(*accent, 190))
        od.ellipse((px - 16, py - 16, px + 16, py + 16), outline=(*accent, 42), width=2)

    overlay = overlay.filter(ImageFilter.GaussianBlur(2))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)

    veil = Image.new("RGBA", (width, height), (12, 18, 24, 0))
    vd = ImageDraw.Draw(veil)
    for x in range(width):
        alpha = int(18 * (x / max(1, width - 1)))
        vd.line([(x, 0), (x, height)], fill=(12, 18, 24, alpha))
    img = Image.alpha_composite(img, veil).convert("RGB")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path, "JPEG", quality=92)
    logger.info(
        "Generated minimal text-free fallback cover (type=%s) at %s",
        story_type, output_path,
    )
    return output_path


def generate_cover_from_news(
    news_list: list[dict],
    date_str: str,
    output_path: str = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    cover_title: str = "",
    cover_subject: Optional[dict] = None,
) -> Optional[str]:
    """
    根据新闻标题生成封面图。

    流程：
    1. 从正文 Top 1-3 选择与头条一致的封面主题。
    2. 优先使用该新闻的真实原文图生成封面。
    3. 没有可信原文图时，使用 AI 生图。
    4. AI 生图失败时，降级为极简本地无字背景。

    Args:
        news_list: 新闻列表
        date_str: 日期字符串
        output_path: 输出图片路径
        api_key: Image generation API Key
        base_url: Image generation API 基础地址
        cover_title: 中文封面标题（12-20 字）
        cover_subject: select_cover_subject() 的结果，为空则内部生成

    Returns:
        封面图路径，失败返回 None
    """
    cover_title = clean_display_text(cover_title or "今日AI要闻")

    # 0. 封面主题选择（如果未外部传入）
    if cover_subject is None:
        cover_subject = select_cover_subject(news_list)
    if cover_subject.get("cover_title") and not cover_title.startswith("今日 AI 热点速览"):
        # 使用 select_cover_subject 返回的标题（可信模式）
        ct = cover_subject.get("cover_title", "")
        if ct and len(ct) >= 4:
            cover_title = ct
    cover_subject["cover_title"] = clean_display_text(cover_title)
    if not cover_subject.get("visual_prompt_topic"):
        cover_subject["visual_prompt_topic"] = cover_subject["cover_title"]

    image_config = resolve_image_llm_config(api_key=api_key, model=model, base_url=base_url)
    api_key = image_config.api_key
    base_url = image_config.base_url
    image_model = image_config.model
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

    # 2. 没有可信原文图时，优先使用 AI 生图。
    ai_cover_enabled = _env_enabled_cover("ENABLE_AI_COVER_GENERATION", True)

    if not ai_cover_enabled:
        logger.info("AI cover generation disabled; using minimal text-free fallback cover")
        return _fallback_cover(news_list, date_str, output_path, cover_title, cover_subject)

    # 没有 API Key，降级
    if not api_key:
        logger.warning("No API key for AI cover generation, using minimal text-free fallback cover")
        return _fallback_cover(news_list, date_str, output_path, cover_title, cover_subject)

    # 开始 AI 生图
    logger.info("AI cover generation enabled, attempting to generate...")
    prompt = _build_cover_prompt(cover_subject)
    base_url = base_url.rstrip("/")
    image_base = base_url.replace("/v1", "") if base_url.endswith("/v1") else base_url

    # 免费 API 不稳定，带指数退避重试（1s → 2s → 4s ...）
    max_retries = int(os.environ.get("AI_COVER_MAX_RETRIES", "5"))
    img = _generate_ai_cover_image(
        image_base,
        api_key,
        prompt,
        max_retries,
        model=image_model,
    )

    if img is None:
        logger.warning(
            "AI cover generation failed after %d attempts, using minimal text-free fallback cover",
            max_retries,
        )
        return _fallback_cover(news_list, date_str, output_path, cover_title, cover_subject)

    # 检测图片质量
    is_bad, bad_reason = _looks_like_bad_cover(img)
    if is_bad:
        logger.warning("AI cover image looks bad: %s", bad_reason)
        if force_local_on_bad:
            logger.info("FORCE_LOCAL_COVER_ON_BAD_IMAGE=1, using minimal text-free fallback cover")
            return _fallback_cover(news_list, date_str, output_path, cover_title, cover_subject)
        # 否则继续使用 AI 图，但记录 warning

    # 保存图片（不叠加任何文字 — 封面图片本体必须是纯视觉图）
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path, "JPEG", quality=90)
    if cover_subject is not None:
        cover_subject["cover_source"] = "ai_generated"

    logger.info("Cover image generated and saved to %s (ai=True, bad=%s)", output_path, is_bad)
    return output_path


def _generate_ai_cover_image(
    image_base: str,
    api_key: str,
    prompt: str,
    max_retries: int = 3,
    model: str = DEFAULT_IMAGE_MODEL,
) -> Optional["Image.Image"]:
    """
    调用图片生成 API 生成封面图，带指数退避重试。

    每次失败都记录具体原因（HTTP 状态码 + API error code + message），便于排查：
    - content_policy_violation（关键词命中审核）→ 重试无效，立即放弃
    - 超时 / 429 / 5xx 等临时故障 → 重试（最多 max_retries 次）

    成功返回 PIL Image，全部失败返回 None。
    """
    import time

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "Generating cover image (attempt %d/%d) with prompt: %s",
                attempt, max_retries, prompt[:80],
            )
            resp = requests.post(
                f"{image_base}/v1/images/generations",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "prompt": prompt,
                    "size": "900x500",
                },
                timeout=120,
            )

            # 非 2xx：解析错误体，区分「关键词拦截」和「临时故障」
            if resp.status_code >= 400:
                err_code, err_msg = _parse_api_error(resp)
                logger.warning(
                    "Image generation API attempt %d/%d failed: HTTP %d | code=%s | %s",
                    attempt, max_retries, resp.status_code, err_code, err_msg,
                )
                # 内容审核拦截：重试无效（同样的 prompt 永远被拦），立即放弃
                if err_code == "content_policy_violation":
                    logger.warning(
                        "Prompt 命中内容审核，重试无效。请检查 prompt 是否含敏感词"
                        "（如 'skyscraper'）。prompt=%s",
                        prompt,
                    )
                    return None
                # 其他错误：走下面的重试逻辑
                if attempt < max_retries:
                    backoff = min(2 ** (attempt - 1), 8)  # 1s,2s,4s,8s,8s...
                    logger.info("Retrying in %ds...", backoff)
                    time.sleep(backoff)
                continue

            data = resp.json()
            image_url = data.get("data", [{}])[0].get("url")
            if not image_url:
                raise ValueError(f"No image URL in response: {data}")

            # 下载图片
            img_resp = requests.get(image_url, timeout=30)
            img_resp.raise_for_status()
            return Image.open(io.BytesIO(img_resp.content))

        except Exception as e:
            logger.warning(
                "Image generation API attempt %d/%d failed: %s: %s",
                attempt, max_retries, type(e).__name__, e,
            )
            if attempt < max_retries:
                backoff = min(2 ** (attempt - 1), 8)  # 1s,2s,4s,8s,8s...
                logger.info("Retrying in %ds...", backoff)
                time.sleep(backoff)

    return None


def _parse_api_error(resp: requests.Response) -> tuple[str, str]:
    """
    从 API 错误响应中解析出 error code 和 message，便于日志排查。

    Returns:
        (code, message)，解析失败时返回 ("unknown", 原始文本片段)
    """
    try:
        err = resp.json().get("error", {})
        if isinstance(err, dict):
            return err.get("code", "unknown"), err.get("message", "")
        return "unknown", str(err)
    except Exception:
        return "unknown", resp.text[:200]


# ═══════════════════════════════════════════════════════════════════
# 按故事类型的视觉 brief — 避免 generic AI network / glowing nodes
# ═══════════════════════════════════════════════════════════════════

_STORY_TYPE_VISUAL_BRIEF: dict[str, dict[str, str]] = {
    "personnel": {
        "scene": (
            "Editorial portrait silhouette inside a modern AI company office, "
            "glass walls, boardroom energy, and soft screens in the background. "
            "Warm sunset light, refined corporate mood, subject on the left third of the frame."
        ),
        "mood": "Contemplative, sophisticated, corporate elegance. Warm amber and deep navy tones.",
        "avoid": "No faces visible, no readable screens, no company logos, no stock-photo handshake scenes.",
    },
    "product": {
        "scene": (
            "AI product launch scene with sleek interface panels, prompt windows, assistant cards, "
            "and a premium device edge integrated into the composition. "
            "Soft blue glow, layered UI depth, studio lighting, sharp focal point with negative space."
        ),
        "mood": "Premium, minimalist, clean and futuristic. Strongly product-led and clearly AI-related.",
        "avoid": "No brand names, no readable screens, no generic device-only close-up, no logos.",
    },
    "model": {
        "scene": (
            "Layered model architecture in deep space, with translucent compute slabs, attention-like light traces, "
            "luminous nodes, and thin circuit paths connecting the layers. "
            "Dark indigo and violet background, isometric view, centered composition with generous margins."
        ),
        "mood": "Futuristic, precise, scientific blueprint aesthetic. Cool, ethereal, and unmistakably AI.",
        "avoid": "No literal brain imagery, no charts with numbers, no text blocks, no logos or watermarks.",
    },
    "research": {
        "scene": (
            "AI research workspace with clean diagram grids, geometric glass instruments, "
            "thin light beams, and transparent paper-like layers suggesting papers and experiments. "
            "Dark charcoal background with emerald accent lighting, overhead editorial composition."
        ),
        "mood": "Scientific precision, editorial elegance, discovery and innovation.",
        "avoid": "No periodic table, no chemical formulas, no microscope images with labels, no dense equations.",
    },
    "business": {
        "scene": (
            "AI business landscape with modern glass towers, subtle dashboard overlays, and faint circuit traces. "
            "Shot from ground level looking up at geometric window patterns, warm gold and deep navy palette, "
            "long-exposure smooth clouds, vertical composition."
        ),
        "mood": "Ambitious, premium, corporate sophistication. Aspirational, powerful, and clearly linked to AI industry.",
        "avoid": "No company signage, no street signs, no visible people, no generic stock skyline.",
    },
    "policy": {
        "scene": (
            "AI policy briefing scene with symmetrical civic architecture, document-like panels, "
            "and restrained legal atmosphere. Clean lines, cool steel blue and gray tones, "
            "frontal view, strong horizontal structure and broad negative space."
        ),
        "mood": "Authoritative, serious, institutional gravitas. Dignified, formal, and policy-oriented.",
        "avoid": "No flags with text, no visible signage, no recognizable monuments, no courtroom scenes.",
    },
    "general": {
        "scene": (
            "Minimal AI briefing cover with translucent layers, faint circuit traces, "
            "subtle model blocks, and prompt-window shapes on a dark editorial background. "
            "Broad negative space, restrained geometry, no literal text."
        ),
        "mood": "Editorial sophistication, curated daily briefing aesthetic, premium magazine quality, clearly AI-related.",
        "avoid": "No newspapers, no readable documents, no laptop screens, no paper-craft stock art.",
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
    cover_title = clean_display_text(cover_subject.get("cover_title") or "今日AI要闻")
    visual_topic = clean_display_text(cover_subject.get("visual_prompt_topic") or "")

    # 选择对应类型的视觉 brief
    if mode == "trusted" and story_type in _STORY_TYPE_VISUAL_BRIEF:
        brief = _STORY_TYPE_VISUAL_BRIEF[story_type]
    else:
        brief = _STORY_TYPE_VISUAL_BRIEF["general"]

    ai_anchor = (
        f"Editorial cover for a daily AI news briefing. "
        f"Main headline concept: {cover_title}. "
        f"Visual topic: {visual_topic or cover_title}. "
        "The image must clearly read as artificial intelligence / machine learning news, "
        "not generic abstract art."
    )

    type_hint_map = {
        "personnel": (
            "Use corporate AI leadership cues, boardroom atmosphere, and a restrained portrait-like composition."
        ),
        "product": (
            "Use product-launch energy, sleek UI layers, prompt surfaces, and a polished consumer-tech feel."
        ),
        "model": (
            "Use layered model architecture, translucent compute planes, attention-like light traces, and circuit paths."
        ),
        "research": (
            "Use research and paper cues, clean diagram structure, and a scientific editorial feeling."
        ),
        "business": (
            "Use market, company, and growth cues around AI industry developments."
        ),
        "policy": (
            "Use civic and regulatory cues around AI governance and policy."
        ),
        "general": (
            "Use unmistakable AI motifs such as prompt windows, model blocks, node graphs, chip traces, or data layers."
        ),
    }

    prompt = f"{ai_anchor} {type_hint_map.get(story_type, type_hint_map['general'])} {brief['scene']} {brief['mood']} {brief['avoid']} {_AVOID_LIST} Avoid plain decorative paper-craft shapes, stock wallpaper, or unrelated geometric art."

    # 限制长度，避免 API 拒绝
    if len(prompt) > 900:
        prompt = prompt[:900]

    return prompt


def _fallback_cover(
    news_list: list[dict],
    date_str: str,
    output_path: str = None,
    cover_title: str = "今日AI要闻",
    cover_subject: Optional[dict] = None,
) -> str:
    """降级：生成极简本地无字背景。"""
    if output_path is None:
        output_path = os.path.join("docs", "cover.jpg")
    if cover_subject is not None:
        cover_subject["cover_source"] = "minimal_text_free_background"
    return _generate_minimal_background_cover(output_path, cover_subject=cover_subject)


def _flattened_pixels(img: Image.Image) -> list[int]:
    pixels = img.get_flattened_data() if hasattr(img, "get_flattened_data") else img.getdata()
    return list(pixels)


# ═══════════════════════════════════════════════════════════════════
# 封面主题选择
# ═══════════════════════════════════════════════════════════════════

_RUMOR_KEYWORDS = [
    "rumor", "ask hn", "speculation", "unconfirmed", "leak",
    "传闻", "疑似", "爆料", "谣言", "辟谣", "别信谣",
]


def _is_eligible_for_cover(item: dict) -> tuple[bool, str]:
    """检查单条新闻是否可作为封面主题。返回 (eligible, reason)。"""
    title = clean_display_text(item.get("chinese_title") or item.get("title", ""))
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
    title = clean_display_text(item.get("chinese_title") or item.get("title") or "")
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
    title = clean_display_text(item.get("chinese_title") or item.get("title", ""))
    title_lower = title.lower()
    summary = clean_display_text(item.get("summary", ""))

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
        title = clean_display_text(item.get("chinese_title") or item.get("title", ""))

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
    title = clean_display_text(item.get("chinese_title") or item.get("title", "")).lower()
    summary = clean_display_text(item.get("summary", "")).lower()
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
    english_title = clean_display_text(item.get("title", ""))
    has_chinese = any('一' <= c <= '鿿' for c in english_title)
    if has_chinese:
        import re as _re
        en_words = _re.findall(
            r'[A-Z][a-z]+|[A-Z]{2,}|[a-z]{4,}',
            english_title + " " + clean_display_text(item.get("summary", "")),
        )
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
    pixels = _flattened_pixels(gray)
    avg_brightness = sum(pixels) / len(pixels)
    if avg_brightness < 25:
        return True, f"image too dark (avg brightness={avg_brightness:.0f})"

    # 中央区域高对比检测（疑似文字/logo）
    # 检查中央 60% 区域是否存在高对比度像素块
    cx, cy = w // 2, h // 2
    region_w, region_h = int(w * 0.5), int(h * 0.4)
    x0, y0 = cx - region_w // 2, cy - region_h // 2
    region = gray.crop((x0, y0, x0 + region_w, y0 + region_h))
    rp = _flattened_pixels(region)

    if len(rp) > 0:
        r_min, r_max = min(rp), max(rp)
        if r_max - r_min > 200:
            # 存在极高对比度 —— 进一步检查是否是分散的小块（文字）还是大面积渐变
            high_contrast_pixels = sum(1 for p in rp if p > 230 or p < 25)
            high_ratio = high_contrast_pixels / len(rp)
            if high_ratio > 0.15:
                return True, f"suspected text/logo in center (high contrast ratio={high_ratio:.2f})"

    return False, ""
