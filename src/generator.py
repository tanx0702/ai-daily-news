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
    生成微信推文 HTML —— 确定性内联样式模板。

    设计原则：
    - 纯内联 style，不使用 <style> 或 <script>
    - 每个文本标签显式 color / font-size / line-height
    - 克制、清爽的中文科技媒体风格
    - 所有动态文本通过 html.escape() 处理
    """
    import html as _html

    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if pages_url is None:
        pages_url = os.environ.get("PAGES_URL", "https://tankex.xyz")

    # ── 色板 ──
    PAGE_BG = "#f7f8fa"
    CARD_BG = "#ffffff"
    TEXT_MAIN = "#111827"
    TEXT_BODY = "#374151"
    TEXT_MUTED = "#6b7280"
    ACCENT = "#2563eb"
    ACCENT_BG = "#eff6ff"
    BORDER = "#e5e7eb"
    DIVIDER = "#d1d5db"

    def esc(s: str) -> str:
        return _html.escape(s, quote=True)

    # 来源名称标准化表
    _source_name_map: dict[str, str] = {
        "hacker": "Hacker News",
        "hackernews": "Hacker News",
        "hn": "Hacker News",
    }

    def _normalize_source(raw: str, source_type: str = "") -> tuple[str, str]:
        """
        标准化来源名称，返回 (display_name, display_label)。

        规则：
        - 跨源合并取主源（+ 之前的部分）
        - 裁剪冗余后缀
        - 映射常见缩写到完整名称
        - label 用于显示格式 "来源：XXX · 阅读原文"
        """
        if not raw:
            if source_type == "hn":
                return "Hacker News", "来源：Hacker News"
            if source_type == "github":
                return "GitHub", "来源：GitHub"
            if source_type == "huggingface":
                return "Hugging Face", "来源：Hugging Face"
            if source_type == "arxiv":
                return "arXiv", "来源：arXiv"
            return "", ""

        # 取第一个 "+" 之前的部分作为主源
        main = raw.split(" + ")[0].strip()
        # 常见冗余后缀清理
        for suffix in [" AI", " News", " - AI", " - Tech", "-ai", ".ai"]:
            if main.lower().endswith(suffix.lower()):
                main = main[:-len(suffix)]

        # 小写标准化映射
        main_lower = main.lower().strip()
        if main_lower in _source_name_map:
            main = _source_name_map[main_lower]

        # 来源类型兜底
        if not main or len(main) < 2:
            type_map = {"hn": "Hacker News", "github": "GitHub",
                       "huggingface": "Hugging Face", "arxiv": "arXiv", "rss": "RSS"}
            main = type_map.get(source_type, main)

        label = f"来源：{main}"
        return main, label

    # ── 常见错别字/怪词修正 ──
    _typo_fixes: dict[str, str] = {
        "特朗普通": "特朗普",
        "特朗普通政府": "特朗普政府",
    }

    def _fix_typos(text: str) -> str:
        """修正常见错别字/怪词。"""
        for wrong, correct in _typo_fixes.items():
            text = text.replace(wrong, correct)
        return text

    # NOTE: _make_insight() 已移除 — 用户要求删除正文"看点：..."渲染逻辑

    parts: list[str] = []

    # ── Wrapper ──
    parts.append(
        f'<div style="background-color:{PAGE_BG};padding:0;max-width:680px;margin:0 auto;">'
    )

    # ── 封面图 ──
    if cover_image_url:
        parts.append(
            f'<img src="{esc(cover_image_url)}" '
            f'style="display:block;width:100%;height:auto;margin:0;padding:0;" '
            f'alt="" />'
        )

    # ── 头部 ──
    parts.append(
        f'<section style="text-align:center;padding:32px 20px 24px;">'
        f'<p style="margin:0 0 8px;color:{TEXT_MAIN};font-size:22px;'
        f'font-weight:700;line-height:1.4;">AI 日报</p>'
        f'<p style="margin:0;color:{TEXT_MUTED};font-size:14px;line-height:1.6;">'
        f'{esc(date_str)}  ·  今日精选 {len(news_list)} 条</p>'
        f'</section>'
    )

    # ── 今日重点（前 3 条编辑摘要，跳过低置信度/质检排除项）──
    highlight_candidates = [
        item for item in news_list
        if not item.get("_highlight_excluded")
        and item.get("highlight_text")
    ][:3]
    if highlight_candidates:
        parts.append(
            f'<section style="margin:0 16px 20px;padding:16px 18px;'
            f'background:{ACCENT_BG};border-radius:8px;">'
            f'<p style="margin:0 0 10px;color:{ACCENT};font-size:13px;'
            f'font-weight:600;line-height:1.4;">📌 今日重点</p>'
        )
        for i, item in enumerate(highlight_candidates):
            highlight_text = item.get("highlight_text", "")
            if not highlight_text:
                highlight_text = item.get("chinese_title") or item.get("title", "")
            parts.append(
                f'<p style="margin:0 0 6px;color:{TEXT_BODY};font-size:14px;'
                f'line-height:1.6;">{esc(str(i + 1))}. {esc(highlight_text)}</p>'
            )
        parts.append('</section>')

    # ── 新闻列表 ──
    ACCENT_BAR = "#3b82f6"  # 纯文字卡片左侧强调线颜色
    TEXT_ONLY_BG = "#f8fafc"  # 纯文字卡片微背景

    for idx, item in enumerate(news_list):
        title = item.get("chinese_title") or item.get("title", "")
        title = _fix_typos(title)
        summary = item.get("summary", "")
        summary = _fix_typos(summary)
        source_name = item.get("source", "")
        source_type = item.get("source_type", "")
        url = item.get("url", "")
        article_img = item.get("article_image_url", "")
        image_type = item.get("image_type", "")

        # 判断是否为图文卡片。
        # 防御性逻辑：只要最终没有可用的微信素材图片，统一走纯文字卡片样式。
        # image_type="original" 但 article_image_url 不可用时，wechat.py 的
        # _enrich_news_with_images() 会将其降级为 text_only，此处再次兜底。
        has_image = bool(
            article_img
            and image_type == "original"
            and article_img.strip() != ""
        )

        # 标准化来源名
        clean_source, source_label = _normalize_source(source_name, source_type)

        # 卡片样式选择
        if has_image:
            # ── 图文卡片 ──
            parts.append(
                f'<section style="margin:0 16px 16px;padding:0 0 16px 0;'
                f'border:1px solid {BORDER};border-radius:8px;background:{CARD_BG};overflow:hidden;">'
            )
            # 顶部配图
            parts.append(
                f'<img src="{esc(article_img)}" '
                f'style="display:block;width:100%;max-height:240px;'
                f'object-fit:cover;margin:0;border-radius:8px 8px 0 0;" alt="" />'
            )
            # 编号（图片下方）
            parts.append(
                f'<p style="margin:14px 16px 6px;color:{ACCENT};font-size:13px;'
                f'font-weight:600;line-height:1.4;">NEWS {idx + 1:02d}</p>'
            )
            # 标题
            parts.append(
                f'<p style="margin:0 16px 10px;color:{TEXT_MAIN};font-size:18px;'
                f'font-weight:700;line-height:1.45;">{esc(title)}</p>'
            )
            # 摘要
            if summary:
                parts.append(
                    f'<p style="margin:0 16px 8px;color:{TEXT_BODY};font-size:15px;'
                    f'line-height:1.8;">{esc(summary)}</p>'
                )
            # 来源
            parts.append(
                f'<p style="margin:0 16px 14px;color:{TEXT_MUTED};font-size:13px;line-height:1.6;">'
                f'{esc(source_label)}'
            )
            if url:
                parts.append(
                    f' · <a href="{esc(url)}" style="color:{ACCENT};text-decoration:none;">'
                    f'阅读原文</a>'
                )
            parts.append('</p></section>')

        else:
            # ── 纯文字卡片（无配图，紧凑布局，左侧强调线） ──
            parts.append(
                f'<section style="margin:0 16px 16px;padding:14px 16px 14px 20px;'
                f'border-left:3px solid {ACCENT_BAR};'
                f'border-top:1px solid {BORDER};border-right:1px solid {BORDER};'
                f'border-bottom:1px solid {BORDER};'
                f'border-radius:6px;background:{TEXT_ONLY_BG};">'
            )
            # 标签行：编号 + "快讯"（所有无图卡片统一标签）
            parts.append(
                f'<p style="margin:0 0 8px;color:{TEXT_MUTED};font-size:12px;'
                f'font-weight:500;line-height:1.4;letter-spacing:0.5px;">'
                f'NEWS {idx + 1:02d} · 快讯'
                f'</p>'
            )
            # 标题（略小于图文卡片）
            parts.append(
                f'<p style="margin:0 0 8px;color:{TEXT_MAIN};font-size:17px;'
                f'font-weight:700;line-height:1.45;">{esc(title)}</p>'
            )
            # 摘要（紧凑）
            if summary:
                parts.append(
                    f'<p style="margin:0 0 6px;color:{TEXT_BODY};font-size:14px;'
                    f'line-height:1.7;">{esc(summary)}</p>'
                )
            # 来源
            parts.append(
                f'<p style="margin:0;color:{TEXT_MUTED};font-size:13px;line-height:1.6;">'
                f'{esc(source_label)}'
            )
            if url:
                parts.append(
                    f' · <a href="{esc(url)}" style="color:{ACCENT};text-decoration:none;">'
                    f'阅读原文</a>'
                )
            parts.append('</p></section>')

    # ── 尾部 ──
    parts.append(
        f'<section style="margin:8px 16px 24px;padding:20px 0 0;'
        f'text-align:center;border-top:1px solid {DIVIDER};">'
        f'<p style="margin:0 0 8px;color:{TEXT_BODY};font-size:14px;line-height:1.6;">'
        f'<a href="{esc(pages_url)}" style="color:{ACCENT};font-weight:600;'
        f'text-decoration:none;">查看完整日报</a></p>'
        f'<p style="margin:0;color:{TEXT_MUTED};font-size:12px;line-height:1.6;">'
        f'AI Daily News Agent  ·  每日自动生成</p>'
        f'</section>'
    )

    # 关闭 wrapper
    parts.append('</div>')

    return "".join(parts)


def _news_to_markdown(
    news_list: list[dict],
    date_str: str,
    pages_url: str,
) -> str:
    """将新闻列表转换为 Markdown，供 md2wechat AI prompt 使用。"""
    grouped: dict[str, list[dict]] = {}
    for item in news_list:
        source = item.get("source", "Unknown")
        region = _guess_region(source)
        grouped.setdefault(region, []).append(item)

    lines: list[str] = []
    lines.append("# AI 日报")
    lines.append("")
    lines.append(f"{date_str} · 今日精选 {len(news_list)} 条")
    lines.append("")

    region_order = [r for r in ["overseas", "china"] if r in grouped]
    region_order += [r for r in grouped if r not in region_order]

    for region in region_order:
        items = grouped[region]
        if region == "china":
            label = "国内精选"
        elif region == "overseas":
            label = "海外精选"
        else:
            label = region

        lines.append(f"## {label}")
        lines.append("")

        for item in items:
            title = item.get("chinese_title") or item.get("title", "")
            summary = item.get("summary", "")
            source_name = item.get("source", "")
            url = item.get("url", "")
            img = item.get("article_image_url", "")

            lines.append(f"### {title}")
            lines.append("")
            if summary:
                lines.append(summary)
                lines.append("")
            meta = f"{source_name}"
            if url:
                meta += f" · [阅读原文]({url})"
            lines.append(meta)
            lines.append("")
            if img:
                lines.append(f"![配图]({img})")
                lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"👉 [查看完整日报（精美排版 + 暗色模式）]({pages_url})")
    lines.append("")
    lines.append("AI Daily News Agent · 每日自动生成")

    return "\n".join(lines)


# md2wechat Ocean Calm AI prompt 模板（精简核心部分）
# 来自 md2wechat v2.9.0 AI mode --theme ocean-calm
_MD2WECHAT_OCEAN_CALM_PROMPT = """【微信公众号排版指令】

你是一位顶级网页设计师，专精微信公众号兼容排版。请将以下 Markdown 转换为纯内联样式的 HTML。

## 核心规则（违反会导致微信显示异常）

1. 必须在 <body> 之后立即创建一个主 <div> 包裹所有内容
2. 所有全局样式（background-color, padding 等）应用在这个主 <div> 上
3. 必须为每一个 <p> 标签明确添加 color 样式，防止微信强制重置为黑色
4. 仅使用纯 HTML 内联样式，禁止 <style> 标签和外部 CSS
5. 仅使用安全标签：section, p, span, strong, em, a, h2, h3, blockquote, img, br, hr

## 色彩方案

- 主容器背景: #f0f4f8
- 正文文字: #3a4150（深蓝灰）
- 主强调色: #4a7c9b（深海蔚蓝）
- 副强调色: #3d6a8a（静谧石蓝）
- 卡片/引用背景: #e8f0f8

## 卡片布局

- 每个 <section> 是一张卡片
- 卡片间距: 40px
- 卡片背景: #ffffff + 淡蓝网格纹理:
  background-image: linear-gradient(rgba(74,124,155,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(74,124,155,0.03) 1px, transparent 1px);
  background-size: 24px 24px;
- 卡片边框: 1px solid rgba(74,124,155,0.08)
- 卡片阴影: box-shadow: 0 8px 28px rgba(58,65,80,0.06), 0 0 16px rgba(74,124,155,0.15)
- 卡片圆角: border-radius: 14px
- 卡片内边距: padding: 25px
- 卡片最大宽度: max-width: 800px

## 排版规范

- 字体: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif
- 正文: font-size: 16px, line-height: 1.8, letter-spacing: 0.2px
- h2（大标题）: 由两个 span 组成——◆ 符号 span（color: #4a7c9b, text-shadow: 0 0 10px rgba(74,124,155,0.4)）+ 标题文字 span（color: #3d6a8a），底部 border-bottom: 1px dashed rgba(74,124,155,0.3)
- h3（小标题）: color: #3d6a8a, border-bottom: 2px solid #4a7c9b（短实线，长度与文字对齐）
- 加粗 strong: color: #3d6a8a（无 text-shadow）
- 引用 blockquote: background: #e8f0f8, border-left: 5px solid #4a7c9b
- 分割线 hr: border: none; height: 1px; background: linear-gradient(90deg, transparent, rgba(74,124,155,0.25), transparent)
- 链接 a: color: #4a7c9b, text-decoration: none, border-bottom: 1px dashed rgba(74,124,155,0.25)

## 输出要求

返回完整 HTML，用 Markdown 代码块包裹（```html ... ```）。
不要任何解释文字，只返回代码块。

---

以下是需要转换的 Markdown 内容：

{markdown_content}"""


def render_wechat_article_ai(
    news_list: list[dict],
    date_str: Optional[str] = None,
    pages_url: Optional[str] = None,
    cover_image_url: str = "",
    api_key: Optional[str] = None,
    model: str = "",
    base_url: str = "",
    timeout: int = 60,
) -> str:
    """
    使用 md2wechat Ocean Calm 主题 + LLM 生成微信推文 HTML。

    流程：news_list → Markdown → md2wechat AI prompt → LLM → HTML

    Args:
        news_list: 新闻列表
        date_str: 日期
        pages_url: 日报 URL
        cover_image_url: 封面图 URL
        api_key: Agnes API Key
        model: LLM 模型名
        base_url: API 地址
        timeout: LLM 超时秒数

    Returns:
        微信兼容的 HTML 字符串，失败返回空字符串
    """
    import re as _re

    api_key = api_key or os.environ.get("AGNES_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("No API key for AI HTML generation, falling back")
        return ""

    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if pages_url is None:
        pages_url = os.environ.get("PAGES_URL", "https://tankex.xyz")

    # 1. 生成 Markdown
    md_content = _news_to_markdown(news_list, date_str, pages_url)

    # 2. 注入封面图（如果有的话）到 Markdown 头部
    if cover_image_url:
        md_content = f"![封面]({cover_image_url})\n\n{md_content}"

    # 3. 拼接完整 prompt
    prompt = _MD2WECHAT_OCEAN_CALM_PROMPT.format(markdown_content=md_content)

    # 4. 调用 LLM
    model = model or os.environ.get("AGNES_MODEL", "agnes-2.0-flash")
    base_url = base_url or os.environ.get("AGNES_API_BASE", "https://apihub.agnes-ai.com/v1")

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

        logger.info("Calling LLM for WeChat HTML generation (%d chars prompt, model=%s)",
                     len(prompt), model)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=4096,
        )
        content = response.choices[0].message.content.strip()

        # 5. 提取 HTML 代码块
        match = _re.search(r'```(?:html)?\s*\n?(.*?)\n?```', content, _re.DOTALL)
        if match:
            html = match.group(1).strip()
        else:
            # 可能 LLM 直接返回了 HTML（没包裹代码块）
            if content.startswith('<'):
                html = content
            else:
                logger.warning("LLM response not HTML: %s...", content[:200])
                return ""

        logger.info("LLM generated WeChat HTML: %d chars", len(html))
        return html

    except Exception as e:
        logger.warning("AI HTML generation failed: %s, falling back to manual template", e)
        return ""


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
