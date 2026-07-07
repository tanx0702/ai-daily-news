"""
HTML 日报生成器

使用 Jinja2 模板渲染美观的 HTML 日报页面，适配手机端浏览。
支持来源分组、暗色模式、响应式布局。
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from jinja2 import Environment, BaseLoader

logger = logging.getLogger(__name__)

# ==================== 内嵌 HTML 模板 ====================

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🤖 AI 日报 {{ date }}</title>
    <style>
        /* --- Reset & Base --- */
        *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
        html { font-size: 16px; -webkit-text-size-adjust: 100%; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                         "Helvetica Neue", Arial, "Noto Sans SC", "PingFang SC",
                         "Microsoft YaHei", sans-serif;
            background: var(--bg);
            color: var(--fg);
            line-height: 1.6;
            transition: background 0.3s, color 0.3s;
        }

        /* --- CSS Variables (Light / Dark) --- */
        :root {
            --bg: #f0f2f5;
            --card: #ffffff;
            --fg: #1a1a2e;
            --muted: #6b7280;
            --accent: #6366f1;
            --accent-light: #e0e7ff;
            --border: #e5e7eb;
            --tag-bg: #f3f4f6;
            --tag-fg: #374151;
            --header-start: #6366f1;
            --header-end: #8b5cf6;
            --shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
            --shadow-lg: 0 4px 12px rgba(0,0,0,0.08);
        }
        @media (prefers-color-scheme: dark) {
            :root {
                --bg: #0f172a;
                --card: #1e293b;
                --fg: #e2e8f0;
                --muted: #94a3b8;
                --accent: #818cf8;
                --accent-light: #1e1b4b;
                --border: #334155;
                --tag-bg: #334155;
                --tag-fg: #cbd5e1;
                --header-start: #4f46e5;
                --header-end: #7c3aed;
                --shadow: 0 1px 3px rgba(0,0,0,0.3);
                --shadow-lg: 0 4px 12px rgba(0,0,0,0.4);
            }
        }
        /* Force dark mode via data attribute */
        body[data-theme="dark"] {
            --bg: #0f172a; --card: #1e293b; --fg: #e2e8f0;
            --muted: #94a3b8; --accent: #818cf8; --accent-light: #1e1b4b;
            --border: #334155; --tag-bg: #334155; --tag-fg: #cbd5e1;
            --header-start: #4f46e5; --header-end: #7c3aed;
            --shadow: 0 1px 3px rgba(0,0,0,0.3); --shadow-lg: 0 4px 12px rgba(0,0,0,0.4);
        }
        body[data-theme="light"] {
            --bg: #f0f2f5; --card: #ffffff; --fg: #1a1a2e;
            --muted: #6b7280; --accent: #6366f1; --accent-light: #e0e7ff;
            --border: #e5e7eb; --tag-bg: #f3f4f6; --tag-fg: #374151;
            --header-start: #6366f1; --header-end: #8b5cf6;
            --shadow: 0 1px 3px rgba(0,0,0,0.06); --shadow-lg: 0 4px 12px rgba(0,0,0,0.08);
        }

        /* --- Layout --- */
        .wrapper {
            max-width: 680px;
            margin: 0 auto;
            padding: 16px;
        }
        @media (min-width: 768px) {
            .wrapper { padding: 24px; }
        }

        /* --- Header --- */
        .header {
            background: linear-gradient(135deg, var(--header-start) 0%, var(--header-end) 100%);
            color: #fff;
            padding: 28px 24px;
            border-radius: 16px;
            box-shadow: var(--shadow-lg);
            text-align: center;
            position: relative;
            margin-bottom: 16px;
        }
        .header h1 {
            font-size: 1.6em;
            font-weight: 700;
            margin-bottom: 6px;
            letter-spacing: -0.02em;
        }
        .header .date {
            font-size: 0.95em;
            opacity: 0.9;
        }
        .header .count {
            font-size: 0.85em;
            opacity: 0.75;
            margin-top: 6px;
            background: rgba(255,255,255,0.2);
            display: inline-block;
            padding: 2px 12px;
            border-radius: 20px;
        }
        .theme-toggle {
            position: absolute;
            top: 12px;
            right: 12px;
            background: rgba(255,255,255,0.2);
            border: none;
            color: #fff;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            cursor: pointer;
            font-size: 16px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.2s;
        }
        .theme-toggle:hover { background: rgba(255,255,255,0.35); }

        /* --- Section (grouped by source) --- */
        .section {
            background: var(--card);
            border-radius: 12px;
            box-shadow: var(--shadow);
            margin-bottom: 12px;
            overflow: hidden;
        }
        .section-header {
            padding: 12px 20px;
            font-size: 0.85em;
            font-weight: 600;
            color: var(--accent);
            background: var(--accent-light);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .section-header .flag {
            font-size: 1.1em;
        }

        /* --- News Item --- */
        .news-list { padding: 4px 0; }
        .news-item {
            padding: 14px 20px;
            border-bottom: 1px solid var(--border);
            transition: background 0.15s;
        }
        .news-item:hover { background: var(--bg); }
        .news-item:last-child { border-bottom: none; }

        .news-num {
            color: var(--accent);
            font-weight: 700;
            margin-right: 6px;
            font-size: 0.85em;
            flex-shrink: 0;
        }
        .news-title {
            font-size: 0.95em;
            font-weight: 600;
            line-height: 1.5;
            display: flex;
            gap: 4px;
        }
        .news-title a {
            color: var(--fg);
            text-decoration: none;
            word-break: break-word;
        }
        .news-title a:hover { color: var(--accent); }

        .news-meta {
            font-size: 0.78em;
            color: var(--muted);
            margin-top: 6px;
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
        }
        .news-source-tag {
            background: var(--tag-bg);
            color: var(--tag-fg);
            padding: 1px 8px;
            border-radius: 4px;
            font-size: 0.9em;
        }

        .news-summary {
            font-size: 0.88em;
            color: var(--muted);
            margin-top: 6px;
            line-height: 1.55;
            word-break: break-word;
        }

        /* --- Footer --- */
        .footer {
            padding: 20px;
            background: var(--card);
            border-radius: 12px;
            box-shadow: var(--shadow);
            text-align: center;
            margin-top: 8px;
        }
        .archive-label {
            font-size: 0.85em;
            color: var(--muted);
            margin-bottom: 8px;
        }
        .archive-links {
            display: flex;
            flex-wrap: wrap;
            justify-content: center;
            gap: 6px;
        }
        .archive-links a {
            color: var(--accent);
            text-decoration: none;
            font-size: 0.82em;
            background: var(--accent-light);
            padding: 3px 10px;
            border-radius: 6px;
            transition: background 0.2s;
        }
        .archive-links a:hover { opacity: 0.8; }

        .powered {
            margin-top: 14px;
            font-size: 0.75em;
            color: var(--muted);
        }
        .powered a { color: var(--accent); text-decoration: none; }
    </style>
</head>
<body>
    <div class="wrapper">
        <div class="header">
            <button class="theme-toggle" onclick="toggleTheme()" title="切换主题">🌓</button>
            <h1>🤖 AI 日报</h1>
            <div class="date">{{ date }}</div>
            <div class="count">今日 {{ news|length }} 条 AI 新闻</div>
        </div>

        {% for source_name, items in grouped_news.items() %}
        <div class="section">
            <div class="section-header">
                {% if source_name == "overseas" %}
                <span class="flag">🌍</span> 海外源
                {% elif source_name == "china" %}
                <span class="flag">🇨🇳</span> 国内源
                {% else %}
                <span class="flag">📡</span> {{ source_name }}
                {% endif %}
                <span style="margin-left:auto; opacity:0.7;">{{ items|length }} 条</span>
            </div>
            <div class="news-list">
                {% for item in items %}
                <div class="news-item">
                    <div class="news-title">
                        <span class="news-num">{{ loop.index }}</span>
                        <a href="{{ item.url }}" target="_blank" rel="noopener">{{ item.chinese_title or item.title }}</a>
                    </div>
                    <div class="news-meta">
                        <span class="news-source-tag">{{ item.source }}</span>
                        {% if item.published_at %}
                        <span>{{ item.published_at }}</span>
                        {% endif %}
                    </div>
                    {% if item.summary %}
                    <div class="news-summary">{{ item.summary }}</div>
                    {% endif %}
                </div>
                {% endfor %}
            </div>
        </div>
        {% endfor %}

        <div class="footer">
            <div class="archive-label">📅 历史日报：</div>
            <div class="archive-links">
                {% for link in archive_links %}
                <a href="{{ link }}">{{ link.split('/')[-1].replace('.html', '') }}</a>
                {% endfor %}
            </div>
            <div class="powered">
                Powered by <a href="https://github.com/{{ github_repo }}" target="_blank">AI Daily News Agent</a>
            </div>
        </div>
    </div>

    <script>
        function toggleTheme() {
            const body = document.body;
            const current = body.getAttribute('data-theme');
            body.setAttribute('data-theme', current === 'dark' ? 'light' : 'dark');
            localStorage.setItem('theme', current === 'dark' ? 'light' : 'dark');
        }
        // Restore saved theme
        (function() {
            const saved = localStorage.getItem('theme');
            if (saved) document.body.setAttribute('data-theme', saved);
        })();
    </script>
</body>
</html>"""


def render_daily_html(
    news_list: list[dict],
    date_str: Optional[str] = None,
    archive_links: Optional[list[str]] = None,
    github_repo: Optional[str] = None,
) -> str:
    """
    渲染 HTML 日报页面。

    Args:
        news_list: 新闻列表
        date_str: 日期字符串，如 "2025-06-26"
        archive_links: 历史归档链接列表
        github_repo: GitHub 仓库名，用于 footer 链接

    Returns:
        HTML 字符串
    """
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if archive_links is None:
        archive_links = []
    if github_repo is None:
        github_repo = "unknown/ai-daily-news"

    # 格式化新闻时间
    formatted_news = []
    for item in news_list:
        news_item = dict(item)
        pub = news_item.get("published_at")
        if isinstance(pub, datetime):
            news_item["published_at"] = pub.strftime("%m/%d %H:%M")
        formatted_news.append(news_item)

    # 按来源地区分组
    grouped = {}
    for item in formatted_news:
        source = item.get("source", "Unknown")
        region = _guess_region(source)
        if region not in grouped:
            grouped[region] = []
        grouped[region].append(item)

    env = Environment(loader=BaseLoader())
    template = env.from_string(HTML_TEMPLATE)
    return template.render(
        date=date_str,
        news=formatted_news,
        grouped_news=grouped,
        archive_links=archive_links[-7:],
        github_repo=github_repo,
    )


def render_wechat_article(
    news_list: list[dict],
    date_str: Optional[str] = None,
    pages_url: Optional[str] = None,
    cover_image_url: str = "",
) -> str:
    """
    生成微信推文 HTML，遵循 md2wechat 专业排版规范。

    关键规则（来自 md2wechat skill）：
    - 所有全局样式在 wrapper <div> 上，非 <body>
    - 每个 <p> 必须显式 color，防止微信编辑器重置为黑色
    - 海洋静谧主题：深邃蓝灰色调，理性专业
    """
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if pages_url is None:
        pages_url = os.environ.get("PAGES_URL", "https://tankex.xyz")

    # 按来源分组
    grouped: dict[str, list[dict]] = {}
    for item in news_list:
        source = item.get("source", "Unknown")
        region = _guess_region(source)
        grouped.setdefault(region, []).append(item)

    # ── Ocean Calm 色板（来自 md2wechat 主题规范）──
    BG = "#ffffff"
    TEXT = "#3a4150"
    TITLE_C = "#2c3e50"
    ACCENT = "#3d6a8a"
    ACCENT_LIGHT = "#edf3f8"
    MUTED = "#8a94a0"
    DIVIDER = "rgba(58,65,80,0.12)"
    GLOW = "0 0 10px rgba(74,124,155,0.35)"

    p: list[str] = []

    # ══════════════════════════════════════
    # Wrapper — md2wechat 规范：全局样式必须在此
    # 精致网格纹理背景（ocean-calm 风格）
    # ══════════════════════════════════════
    p.append(
        f'<div style="background-color:{BG};'
        f'background-image:linear-gradient(rgba(74,124,155,0.03) 1px,transparent 1px),'
        f'linear-gradient(90deg,rgba(74,124,155,0.03) 1px,transparent 1px);'
        f'background-size:24px 24px;padding:0;">'
    )

    # ══════════════════════════════════════
    # Hero 封面图
    # ══════════════════════════════════════
    if cover_image_url:
        p.append(
            f'<img src="{cover_image_url}" '
            f'style="display:block;width:100%;height:auto;margin:0;padding:0;" '
            f'alt="AI 日报封面" />'
        )

    # ══════════════════════════════════════
    # 头部 — 居中标题 + 下方短装饰线
    # ══════════════════════════════════════
    p.append(
        f'<section style="text-align:center;padding:44px 16px 32px;">'
        f'<p style="margin:0 0 8px;font-size:30px;font-weight:700;'
        f'color:{TITLE_C};letter-spacing:1px;">'
        f'AI 日报</p>'
        f'<p style="margin:0 0 16px;font-size:14px;color:{MUTED};">'
        f'{date_str}  ·  今日精选 {len(news_list)} 条</p>'
        f'<span style="display:inline-block;width:36px;'
        f'border-bottom:3px solid {ACCENT};border-radius:2px;">'
        f'</span>'
        f'</section>'
    )

    # ══════════════════════════════════════
    # 新闻列表（按地区分组）
    # ══════════════════════════════════════
    global_index = 0
    region_order = [r for r in ["china", "overseas"] if r in grouped]
    region_order += [r for r in grouped if r not in region_order]

    is_first_section = True
    for region in region_order:
        items = grouped[region]
        if not items:
            continue

        if region == "china":
            label = "国内精选"
        elif region == "overseas":
            label = "海外精选"
        else:
            label = region

        margin_top = "36px" if is_first_section else "32px"
        is_first_section = False

        # 分组标题 — ◆ 符号 + text-shadow 发光效果
        p.append(
            f'<section style="margin:{margin_top} 20px 14px;">'
            f'<p style="margin:0;font-size:13px;font-weight:600;'
            f'color:{ACCENT};letter-spacing:2px;">'
            f'<span style="color:{ACCENT};text-shadow:{GLOW};">◆</span>'
            f'  {label}'
            f'<span style="font-weight:400;font-size:12px;color:{MUTED};">'
            f'  ·  {len(items)} 条</span>'
            f'</p></section>'
        )

        for item in items:
            global_index += 1
            title = item.get("chinese_title") or item.get("title", "")
            summary = item.get("summary", "")
            source_name = item.get("source", "")
            url = item.get("url", "")
            article_img = item.get("article_image_url", "")

            # 序号：前3名用强调色 + 发光，其余灰色
            if global_index <= 3:
                num_style = f'color:{ACCENT};font-weight:700;text-shadow:{GLOW};'
            else:
                num_style = f'color:{MUTED};font-weight:600;'

            p.append(
                f'<section style="margin:0 20px 28px;">'
            )

            # 文章配图
            if article_img:
                p.append(
                    f'<img src="{article_img}" '
                    f'style="display:block;width:100%;height:auto;'
                    f'margin:0 0 12px;border-radius:4px;" '
                    f'alt="" />'
                )

            # 标题行
            p.append(
                f'<p style="margin:0 0 8px;line-height:1.5;">'
                f'<span style="font-size:14px;{num_style}'
                f'margin-right:8px;">{global_index:02d}</span>'
                f'<span style="font-size:16px;font-weight:600;'
                f'color:{TITLE_C};">{title}</span>'
                f'</p>'
            )

            # 摘要
            if summary:
                p.append(
                    f'<p style="margin:0 0 8px;font-size:14px;'
                    f'color:{TEXT};line-height:1.7;'
                    f'padding-left:24px;">{summary}</p>'
                )

            # 来源 + 链接
            p.append(
                f'<p style="margin:0;padding-left:24px;font-size:13px;'
                f'color:{MUTED};">{source_name}'
            )
            if url:
                p.append(
                    f' <span style="color:{MUTED};">·</span> '
                    f'<a href="{url}" style="color:{ACCENT};'
                    f'text-decoration:none;font-size:13px;">阅读原文</a>'
                )
            p.append('</p></section>')

    # ══════════════════════════════════════
    # 尾部
    # ══════════════════════════════════════
    p.append(
        f'<section style="margin:32px 20px 24px;padding:24px 0 0;'
        f'text-align:center;border-top:1px solid {DIVIDER};">'
        f'<p style="margin:0 0 8px;font-size:14px;color:{TEXT};">'
        f'<a href="{pages_url}" style="color:{ACCENT};font-weight:600;'
        f'text-decoration:none;">查看完整日报（精美排版 + 暗色模式）</a>'
        f'</p>'
        f'<p style="margin:0;font-size:12px;color:{MUTED};">'
        f'AI Daily News Agent  ·  每日自动生成'
        f'</p></section>'
    )

    # 关闭 wrapper div
    p.append('</div>')

    return "".join(p)


def _guess_region(source: str) -> str:
    """根据来源名称猜测地区分类。"""
    china_keywords = ["机器之心", "量子位", "36氪", "虎嗅", "钛媒体", "品玩"]
    for kw in china_keywords:
        if kw in source:
            return "china"
    return "overseas"


def save_html(html: str, output_path: str) -> None:
    """将 HTML 保存到文件。"""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("Saved HTML to %s", output_path)
