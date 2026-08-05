"""Deterministic local renderer for editorial daily-news covers."""

import os
import re
from datetime import date

from PIL import Image, ImageDraw, ImageFont


CANVAS_SIZE = (900, 500)
PALETTE_ROTATION_EPOCH = date(2026, 1, 1)

EDITORIAL_PALETTES = (
    {
        "palette_id": "terracotta",
        "background": (221, 119, 86),
        "ink": (47, 39, 35),
        "muted": (99, 64, 51),
        "line": (93, 66, 55),
        "accent": (246, 232, 211),
    },
    {
        "palette_id": "charcoal",
        "background": (29, 30, 29),
        "ink": (244, 239, 228),
        "muted": (184, 179, 169),
        "line": (126, 128, 123),
        "accent": (230, 97, 52),
    },
    {
        "palette_id": "sage",
        "background": (190, 209, 195),
        "ink": (45, 52, 47),
        "muted": (86, 105, 91),
        "line": (113, 135, 120),
        "accent": (215, 91, 50),
    },
    {
        "palette_id": "paper",
        "background": (224, 221, 211),
        "ink": (48, 49, 45),
        "muted": (106, 105, 97),
        "line": (151, 151, 143),
        "accent": (109, 160, 195),
    },
    {
        "palette_id": "ink_blue",
        "background": (52, 75, 99),
        "ink": (244, 239, 228),
        "muted": (190, 203, 211),
        "line": (137, 157, 173),
        "accent": (238, 166, 75),
    },
    {
        "palette_id": "deep_teal",
        "background": (47, 106, 103),
        "ink": (241, 238, 224),
        "muted": (190, 211, 201),
        "line": (163, 197, 188),
        "accent": (232, 134, 70),
    },
    {
        "palette_id": "burgundy",
        "background": (132, 74, 75),
        "ink": (246, 235, 222),
        "muted": (219, 190, 180),
        "line": (190, 134, 130),
        "accent": (238, 181, 99),
    },
    {
        "palette_id": "mustard",
        "background": (198, 154, 60),
        "ink": (48, 47, 39),
        "muted": (89, 76, 40),
        "line": (126, 102, 56),
        "accent": (238, 231, 210),
    },
)

_FONT_CANDIDATES = {
    "regular": (
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ),
    "bold": (
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ),
}

_STORY_TYPE_DIAGRAMS = {
    "product": "growth",
    "business": "growth",
    "model": "funnel",
    "research": "network",
    "policy": "blueprint",
    "personnel": "neutral",
    "general": "neutral",
}


def select_editorial_palette(date_str: str) -> dict:
    """Select one deterministic colour profile from an ISO date."""
    current_date = date.fromisoformat(date_str)
    palette_index = (current_date - PALETTE_ROTATION_EPOCH).days % len(EDITORIAL_PALETTES)
    palette = dict(EDITORIAL_PALETTES[palette_index])
    palette["palette_index"] = palette_index
    return palette


def diagram_type_for_story(story_type: str) -> str:
    """Map a selected news story type to a factual-neutral diagram."""
    return _STORY_TYPE_DIAGRAMS.get((story_type or "").lower(), "neutral")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _FONT_CANDIDATES["bold" if bold else "regular"]:
        if os.path.isfile(candidate):
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _wrap_title(text: str, max_width: int, size: int) -> list[str]:
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    font = _font(size, bold=True)
    tokens = re.findall(r"[A-Za-z0-9.+#-]+|[^\s]", text.strip())
    lines: list[str] = []
    current = ""

    for token in tokens:
        separator = " " if current and token[0].isascii() and current[-1].isascii() else ""
        candidate = current + separator + token
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = token
        else:
            current = candidate

    if current:
        lines.append(current)
    return lines[:3]


def _draw_grid(draw: ImageDraw.ImageDraw, palette: dict) -> None:
    left, top, cell = 696, 142, 34
    for index in range(5):
        offset = index * cell
        draw.line((left + offset, top, left + offset, top + cell * 4), fill=palette["line"], width=2)
        draw.line((left, top + offset, left + cell * 4, top + offset), fill=palette["line"], width=2)
    draw.ellipse((764, 228, 772, 236), fill=palette["accent"])


def _draw_growth_diagram(draw: ImageDraw.ImageDraw, palette: dict) -> None:
    nodes = [(555, 242), (594, 213), (628, 248), (662, 201)]
    for start, end in zip(nodes, nodes[1:]):
        draw.line((start, end), fill=palette["line"], width=2)
    for x, y in nodes:
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), outline=palette["ink"], width=2)
    draw.line((662, 201, 696, 201), fill=palette["line"], width=2)
    _draw_grid(draw, palette)
    draw.arc((604, 285, 744, 425), start=195, end=314, fill=palette["line"], width=2)


def _draw_funnel_diagram(draw: ImageDraw.ImageDraw, palette: dict) -> None:
    start_x, center_y = 548, 226
    for index, offset in enumerate((-72, -42, -12, 18, 48)):
        draw.line((start_x, center_y + offset, 657, center_y + offset // 3), fill=palette["line"], width=2)
        if index in (1, 3):
            draw.ellipse(
                (start_x - 5, center_y + offset - 5, start_x + 5, center_y + offset + 5),
                fill=palette["accent"],
            )
    draw.line((657, 202, 696, 202), fill=palette["line"], width=2)
    _draw_grid(draw, palette)
    draw.rectangle((700, 321, 790, 405), outline=palette["line"], width=2)
    draw.line((718, 348, 772, 348), fill=palette["line"], width=2)


def _draw_network_diagram(draw: ImageDraw.ImageDraw, palette: dict) -> None:
    nodes = [(553, 266), (590, 218), (629, 260), (668, 212), (696, 262)]
    for start, end in zip(nodes, nodes[1:]):
        draw.line((start, end), fill=palette["line"], width=2)
    for index, (x, y) in enumerate(nodes):
        draw.ellipse(
            (x - 5, y - 5, x + 5, y + 5),
            fill=palette["accent"] if index == 2 else palette["ink"],
        )
    for offset in (0, 16, 32):
        draw.rectangle((704 + offset, 164 - offset, 800 + offset, 260 - offset), outline=palette["line"], width=2)
    _draw_grid(draw, palette)
    draw.ellipse((735, 330, 813, 408), outline=palette["line"], width=2)
    draw.ellipse((759, 354, 789, 384), outline=palette["accent"], width=2)


def _draw_blueprint_diagram(draw: ImageDraw.ImageDraw, palette: dict) -> None:
    for y in (165, 207, 249, 291, 333):
        draw.line((536, y, 658, y), fill=palette["line"], width=2)
    for x in (562, 601, 639):
        draw.line((x, 147, x, 350), fill=palette["line"], width=1)
    draw.rectangle((694, 144, 830, 286), outline=palette["line"], width=2)
    for offset in (22, 48, 74, 100):
        draw.line((694 + offset, 144, 694 + offset, 286), fill=palette["line"], width=1)
        draw.line((694, 144 + offset, 830, 144 + offset), fill=palette["line"], width=1)
    _draw_grid(draw, palette)
    draw.rectangle((688, 338, 782, 401), outline=palette["accent"], width=3)


def _draw_neutral_diagram(draw: ImageDraw.ImageDraw, palette: dict) -> None:
    points = [(548, 276), (582, 238), (616, 271), (650, 221), (685, 248)]
    for start, end in zip(points, points[1:]):
        draw.line((start, end), fill=palette["line"], width=2)
    for x, y in points:
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=palette["accent"])
    _draw_grid(draw, palette)
    draw.arc((576, 305, 730, 420), start=200, end=330, fill=palette["line"], width=2)


def _draw_diagram(draw: ImageDraw.ImageDraw, palette: dict, diagram_type: str) -> None:
    if diagram_type == "growth":
        _draw_growth_diagram(draw, palette)
    elif diagram_type == "funnel":
        _draw_funnel_diagram(draw, palette)
    elif diagram_type == "network":
        _draw_network_diagram(draw, palette)
    elif diagram_type == "blueprint":
        _draw_blueprint_diagram(draw, palette)
    else:
        _draw_neutral_diagram(draw, palette)


def render_editorial_cover(
    title: str,
    date_str: str,
    source_label: str,
    story_type: str,
) -> tuple[Image.Image, dict]:
    """Render a date-themed local editorial cover and return its metadata."""
    palette = select_editorial_palette(date_str)
    diagram_type = diagram_type_for_story(story_type)
    image = Image.new("RGB", CANVAS_SIZE, palette["background"])
    draw = ImageDraw.Draw(image)

    draw.text((52, 48), "AI DAILY NEWS", font=_font(17, bold=True), fill=palette["ink"])
    draw.text((52, 78), date_str.replace("-", "."), font=_font(16), fill=palette["muted"])
    draw.line((52, 110, 124, 110), fill=palette["accent"], width=5)

    y = 138
    for line in _wrap_title(title or "今日 AI 要闻", 390, 40):
        draw.text((52, y), line, font=_font(40, bold=True), fill=palette["ink"])
        y += 56

    draw.line((52, 372, 400, 372), fill=palette["line"], width=2)
    draw.text((52, 392), (source_label or "AI Daily News").upper(), font=_font(16, bold=True), fill=palette["ink"])
    draw.text((52, 423), "TODAY'S CURATED AI SIGNAL", font=_font(14), fill=palette["muted"])
    _draw_diagram(draw, palette, diagram_type)

    return image, {
        "palette_id": palette["palette_id"],
        "palette_index": palette["palette_index"],
        "diagram_type": diagram_type,
    }
