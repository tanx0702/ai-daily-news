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
from PIL import Image, ImageDraw

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


# NOTE: _FONT_CANDIDATES / _load_font / _draw_cover_text / _draw_cover_text_impl 已移除。
# 封面图片本体必须是纯视觉图，不叠任何文字。
# 封面图片本体必须是纯视觉图，不叠任何日期、条数、标题、Logo、水印。
# 日期和标题在 HTML 页面的 header 区域展示（不属于封面图片本体）。


# ═══════════════════════════════════════════════════════════════════
# 故事类型色板 — 用于本地 fallback 封面
# ═══════════════════════════════════════════════════════════════════

_STORY_TYPE_PALETTE: dict[str, dict[str, tuple]] = {
    "personnel": {
        "bg": (26, 26, 46),         # deep navy
        "primary": (196, 134, 58),   # warm amber
        "secondary": (46, 40, 60),   # muted plum
        "accent": (220, 180, 120),   # light amber
    },
    "product": {
        "bg": (26, 35, 50),          # dark slate
        "primary": (58, 138, 138),   # cool teal
        "secondary": (35, 50, 55),   # deep teal-gray
        "accent": (140, 200, 200),   # light teal
    },
    "model": {
        "bg": (26, 16, 51),          # dark indigo
        "primary": (106, 74, 138),   # muted violet
        "secondary": (40, 30, 60),   # deep violet
        "accent": (170, 140, 200),   # light violet
    },
    "research": {
        "bg": (22, 30, 22),          # dark charcoal-green
        "primary": (58, 122, 90),    # emerald
        "secondary": (30, 45, 35),   # deep green
        "accent": (130, 190, 150),   # light sage
    },
    "business": {
        "bg": (26, 26, 48),          # dark navy
        "primary": (184, 160, 74),   # brass
        "secondary": (48, 42, 30),   # deep bronze
        "accent": (210, 190, 130),   # light gold
    },
    "policy": {
        "bg": (31, 31, 36),          # dark gray
        "primary": (74, 106, 138),   # muted blue
        "secondary": (40, 44, 52),   # deep blue-gray
        "accent": (150, 170, 200),   # light steel
    },
    "general": {
        "bg": (26, 31, 36),          # dark graphite
        "primary": (58, 122, 154),   # cyan
        "secondary": (45, 50, 58),   # deep gray
        "accent": (138, 106, 74),    # soft amber
    },
}


def _generate_simple_cover(
    news_list: list[dict],
    date_str: str,
    output_path: str,
    width: int = 900,
    height: int = 500,
    cover_title: str = "AI 日报",
    story_type: str = "general",
) -> str:
    """
    降级方案：Pillow 生成的编辑风程序化封面。

    设计：深色背景 + 非对称大色块 + 类型专属几何符号 + 杂志版式。
    不使用网格、节点连线、发光点、机器人 emoji、AI Daily News 英文。
    """
    palette = _STORY_TYPE_PALETTE.get(story_type, _STORY_TYPE_PALETTE["general"])
    bg_color = palette["bg"]
    primary = palette["primary"]
    secondary = palette["secondary"]
    accent = palette["accent"]

    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img, "RGBA")

    # ── 1. 大色块层（非对称几何形状）──
    # 主色块：右上角大矩形
    block_w = int(width * 0.55)
    block_h = int(height * 0.55)
    draw.rectangle(
        [(width - block_w, 0), (width, block_h)],
        fill=(*primary, 80),
    )
    # 副色块：左下角矩形
    block2_w = int(width * 0.40)
    block2_h = int(height * 0.35)
    draw.rectangle(
        [(0, height - block2_h), (block2_w, height)],
        fill=(*secondary, 100),
    )
    # 强调色块：中部偏左小矩形
    draw.rectangle(
        [(int(width * 0.08), int(height * 0.20)),
         (int(width * 0.22), int(height * 0.38))],
        fill=(*primary, 50),
    )

    # ── 2. 几何符号层（类型专属抽象图形）──
    if story_type == "personnel":
        # 圆形 — 象征肖像/人物
        cx, cy = int(width * 0.30), int(height * 0.30)
        r = int(height * 0.14)
        draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)],
                     fill=(*accent, 40), outline=(*accent, 120), width=2)
        # 小圆 — 象征头部
        draw.ellipse([(cx - r//2, cy - r), (cx + r//2, cy + r//3)],
                     fill=(*accent, 60))
    elif story_type == "product":
        # 圆角矩形 — 象征设备/界面
        rx0, ry0 = int(width * 0.58), int(height * 0.18)
        rx1, ry1 = int(width * 0.82), int(height * 0.42)
        draw.rounded_rectangle(
            [(rx0, ry0), (rx1, ry1)], radius=12,
            fill=(*accent, 35), outline=(*accent, 100), width=2,
        )
    elif story_type == "model":
        # 层叠菱形 — 象征架构/层
        for i, (dx, dy, s) in enumerate([
            (0, 0, 1.0), (-12, 18, 0.7), (12, 36, 0.45),
        ]):
            cx_m = int(width * 0.65) + dx
            cy_m = int(height * 0.28) + dy
            half_w = int(width * 0.12 * s)
            half_h = int(height * 0.10 * s)
            alpha = int(100 * s)
            draw.polygon([
                (cx_m, cy_m - half_h),
                (cx_m + half_w, cy_m),
                (cx_m, cy_m + half_h),
                (cx_m - half_w, cy_m),
            ], fill=(*accent, min(alpha, 80)), outline=(*accent, alpha), width=1)
    elif story_type == "research":
        # 水平线 + 小圆 — 象征实验/数据
        line_y = int(height * 0.30)
        draw.line([(int(width * 0.52), line_y), (int(width * 0.82), line_y)],
                  fill=(*accent, 120), width=1)
        for dot_x in [width * 0.55, width * 0.65, width * 0.75]:
            draw.ellipse([(int(dot_x) - 3, line_y - 3), (int(dot_x) + 3, line_y + 3)],
                         fill=(*accent, 180))
    elif story_type == "business":
        # 垂直柱状 — 象征建筑/财务
        for j, (bx, bh_ratio) in enumerate([
            (width * 0.55, 0.30), (width * 0.63, 0.42), (width * 0.71, 0.24),
        ]):
            bh = int(height * bh_ratio)
            by = int(height * 0.55) - bh
            bw = int(width * 0.06)
            draw.rectangle(
                [(int(bx), by), (int(bx) + bw, int(height * 0.55))],
                fill=(*accent, 70), outline=(*accent, 140), width=1,
            )
    elif story_type == "policy":
        # 矩形框架 — 象征文件/机构
        fx0, fy0 = int(width * 0.54), int(height * 0.16)
        fx1, fy1 = int(width * 0.80), int(height * 0.44)
        draw.rectangle(
            [(fx0, fy0), (fx1, fy1)],
            fill=(*accent, 25), outline=(*accent, 100), width=2,
        )
        # 内部横线（象征文字行）
        for line_i in range(3):
            ly = fy0 + int(height * 0.07) * (line_i + 1)
            draw.line([(fx0 + 16, ly), (fx1 - 16, ly)],
                      fill=(*accent, 80), width=1)
    else:  # general
        # 对角切分线 + 大圆
        draw.line([(int(width * 0.40), 0), (int(width * 0.75), height)],
                  fill=(*primary, 50), width=2)
        gc_x, gc_y = int(width * 0.52), int(height * 0.30)
        gc_r = int(height * 0.16)
        draw.ellipse(
            [(gc_x - gc_r, gc_y - gc_r), (gc_x + gc_r, gc_y + gc_r)],
            fill=(*accent, 30), outline=(*accent, 100), width=2,
        )

    # ── 3. 底部暗色渐变（为 HTML 标题留出干净区域）──
    for y_offset in range(int(height * 0.60), height):
        alpha = int(140 * (y_offset - int(height * 0.60)) / (height - int(height * 0.60)))
        draw.rectangle(
            [(0, y_offset), (width, y_offset + 1)],
            fill=(*bg_color, min(alpha, 140)),
        )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path, "JPEG", quality=92)
    logger.info(
        "Generated editorial fallback cover (type=%s, no text) at %s",
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
        return _fallback_cover(news_list, date_str, output_path, cover_title, cover_subject)

    if not api_key:
        logger.warning("No API key for cover generation, falling back to programmatic cover")
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
                return _fallback_cover(news_list, date_str, output_path, cover_title, cover_subject)
            # 否则继续使用 AI 图，但记录 warning

        # 5. 保存图片（不叠加任何文字 — 封面图片本体必须是纯视觉图）
        img.save(output_path, "JPEG", quality=90)
        ai_generated = True

        logger.info("Cover image generated and saved to %s (ai=%s, bad=%s)",
                     output_path, ai_generated, bad_ai)
        return output_path

    except Exception as e:
        logger.warning("Agnes Image API failed: %s, falling back to programmatic cover", e)
        return _fallback_cover(news_list, date_str, output_path, cover_title, cover_subject)


# ═══════════════════════════════════════════════════════════════════
# 按故事类型的视觉 brief — 避免 generic AI network / glowing nodes
# ═══════════════════════════════════════════════════════════════════

_STORY_TYPE_VISUAL_BRIEF: dict[str, dict[str, str]] = {
    "personnel": {
        "subject": (
            "a professional figure in a modern tech office or boardroom — "
            "seen from behind or in silhouette, facing a window or large display"
        ),
        "style": (
            "editorial portrait photography style, soft directional window light, "
            "shallow depth of field, muted navy and warm amber tones, subtle film grain"
        ),
        "composition": (
            "off-center subject, generous negative space on one side, "
            "architectural lines (glass walls, clean ceiling grid) framing the scene, "
            "dark gradient at bottom"
        ),
    },
    "product": {
        "subject": (
            "abstract close-up of a device screen or interface surface, "
            "shallow depth of field, product design photography style — "
            "no specific UI text, only abstract shapes suggesting an interface"
        ),
        "style": (
            "clean product photography, matte surfaces, cool slate and teal tones, "
            "soft studio lighting, premium tech product aesthetic"
        ),
        "composition": (
            "asymmetric product detail shot, large blurred negative space, "
            "sharp focus on one corner or edge, dark gradient at bottom"
        ),
    },
    "model": {
        "subject": (
            "abstract visualization of a neural architecture — "
            "layered translucent geometric planes, subtle data flow patterns, "
            "not literal neurons or brains; more like architectural blueprints"
        ),
        "style": (
            "architectural visualization style, dark indigo and violet tones, "
            "clean geometric precision, matte render, isometric or top-down view"
        ),
        "composition": (
            "layered depth with overlapping translucent forms, "
            "center-weighted composition, dark gradient at bottom"
        ),
    },
    "research": {
        "subject": (
            "abstract laboratory or research setting — "
            "scientific instruments, optical elements, or 3D visualization "
            "rendered as clean editorial graphics, not literal photos"
        ),
        "style": (
            "scientific editorial illustration, dark charcoal and emerald tones, "
            "clean line art quality, precision geometry, subtle texture"
        ),
        "composition": (
            "diagram-like arrangement of abstract research elements, "
            "off-center focal point, dark gradient at bottom"
        ),
    },
    "business": {
        "subject": (
            "modern corporate architecture or financial district skyline at dusk — "
            "glass towers, clean lines, abstracted to geometric forms"
        ),
        "style": (
            "architectural photography style, dark navy and warm gold/brass tones, "
            "long exposure aesthetic, smooth gradients, premium feel"
        ),
        "composition": (
            "low-angle or eye-level view of architecture, "
            "strong vertical lines, dark gradient at bottom"
        ),
    },
    "policy": {
        "subject": (
            "government or institutional building facade — "
            "classical or modern civic architecture, abstracted to geometric forms, "
            "flags or institutional symbols rendered as subtle shapes"
        ),
        "style": (
            "editorial documentary style, dark gray and muted blue tones, "
            "clean geometric composition, serious and authoritative mood"
        ),
        "composition": (
            "symmetrical or near-symmetrical composition, "
            "strong horizontal lines, dark gradient at bottom"
        ),
    },
    "general": {
        "subject": (
            "a curated daily briefing workspace — "
            "clean desk surface with abstract editorial elements, "
            "not literal news screens; more like design studio still life"
        ),
        "style": (
            "editorial still life photography, dark graphite and warm paper tones, "
            "soft directional light, premium magazine aesthetic, subtle texture"
        ),
        "composition": (
            "flat-lay or slight angle, asymmetric arrangement of abstract shapes, "
            "generous negative space, dark gradient at bottom"
        ),
    },
}

# 禁止词清单 — 每一条 prompt 都必须追加
_AVOID_LIST = (
    "Do NOT include: generic AI network, glowing nodes, glowing lines, "
    "robot mascot, blue-purple gradient, cyber brain, circuit board, "
    "fake UI text, fake headline, fake HUD, watermark, any text, any letters, "
    "any numbers, any logos."
)


def _build_cover_prompt(cover_subject: dict) -> str:
    """
    构建封面图 prompt —— 按故事类型使用结构化视觉 brief。

    结构：
    - Primary story
    - Story type
    - Visual subject
    - Editorial style
    - Composition
    - Avoid list
    """
    mode = cover_subject.get("mode", "generic")
    story_type = cover_subject.get("story_type", "general")
    item = cover_subject.get("item")

    # 选择 brief（trusted 模式用对应类型，generic 用 general）
    if mode == "trusted" and story_type in _STORY_TYPE_VISUAL_BRIEF:
        brief = _STORY_TYPE_VISUAL_BRIEF[story_type]
    else:
        brief = _STORY_TYPE_VISUAL_BRIEF["general"]

    # Primary story
    if item:
        primary = (item.get("chinese_title") or item.get("title", ""))[:100]
    else:
        primary = "AI industry daily briefing"

    return (
        f"Primary story: \"{primary}\"\n"
        f"Story type: {story_type}\n"
        f"Visual subject: {brief['subject']}\n"
        f"Editorial style: {brief['style']}\n"
        f"Composition: {brief['composition']}\n"
        f"Avoid: {_AVOID_LIST}\n"
        f"16:9 aspect ratio, high quality editorial illustration, no typography."
    )


def _fallback_cover(
    news_list: list[dict],
    date_str: str,
    output_path: str = None,
    cover_title: str = "AI 日报",
    cover_subject: Optional[dict] = None,
) -> str:
    """降级：生成 Pillow 封面图。"""
    if output_path is None:
        output_path = os.path.join("docs", "cover.jpg")
    story_type = "general"
    if cover_subject:
        story_type = cover_subject.get("story_type", "general")
    return _generate_simple_cover(
        news_list, date_str, output_path,
        cover_title=cover_title,
        story_type=story_type,
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
            "cover_title": "AI 日报",
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
        logger.info(
            "Cover subject: #%d trusted (%s) — '%s'",
            i + 1, story_type, title[:50],
        )
        return {
            "mode": "trusted",
            "item": item,
            "cover_title": title[:20].rstrip("，。；：！？、"),
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
        "cover_title": "AI 日报",
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
