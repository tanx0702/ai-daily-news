"""
AI 每日新闻推送 Agent - 主程序

每日定时流程：
1. 采集 RSS 新闻
2. LLM 生成摘要
3. 渲染 HTML 日报
4. 部署到 GitHub Pages
5. 微信推送图文消息
"""

import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main():
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info("=" * 50)
    logger.info("AI Daily News Agent - %s", date_str)
    logger.info("=" * 50)

    # === 1. 采集新闻 ===
    logger.info("[1/5] 采集新闻...")
    from src.collector import collect_news

    top_n = int(os.environ.get("DAILY_TOP_N", "15"))
    rss_timeout = int(os.environ.get("DAILY_RSS_TIMEOUT", "30"))
    news_list = collect_news(top_n=top_n, rss_timeout=rss_timeout)

    if not news_list:
        logger.error("No news collected! Aborting.")
        sys.exit(1)

    # Fix Windows console encoding for emoji output
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    logger.info("Collected %d news items", len(news_list))

    # === 2. LLM 摘要 ===
    logger.info("[2/5] 生成 LLM 摘要...")
    from src.summarizer import summarize_news

    api_key = os.environ.get("AGNES_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("AGNES_MODEL", os.environ.get("OPENAI_MODEL", "agnes-2.0-flash"))
    llm_timeout = int(os.environ.get("DAILY_LLM_TIMEOUT", "15"))

    if api_key:
        news_list = summarize_news(
            news_list,
            api_key=api_key,
            model=model,
            timeout=llm_timeout,
        )
    else:
        logger.info("AGNES_API_KEY not set, skipping LLM summary")

    # === 3. 生成 HTML 日报 ===
    logger.info("[3/5] 生成 HTML 日报...")
    from src.generator import render_daily_html, save_html

    # 尝试加载历史归档链接
    archive_links = []
    docs_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    if os.path.isdir(docs_dir):
        for fname in os.listdir(docs_dir):
            if fname.endswith(".html") and fname != "index.html":
                archive_links.append(f"https://{os.environ.get('GITHUB_USERNAME', '')}.github.io/{os.environ.get('GITHUB_REPO', '')}/{fname}")

    html = render_daily_html(
        news_list,
        date_str,
        archive_links,
        github_repo=os.environ.get("GITHUB_REPO", "unknown/ai-daily-news"),
    )

    # 保存 HTML
    docs_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    os.makedirs(docs_dir, exist_ok=True)
    save_html(html, os.path.join(docs_dir, "index.html"))

    # 保存归档
    archive_path = os.path.join(docs_dir, "archive", f"{date_str}.html")
    save_html(html, archive_path)

    pages_url = f"https://{os.environ.get('GITHUB_USERNAME', '')}.github.io/{os.environ.get('GITHUB_REPO', '')}/"

    # === 4. 生成封面图 ===
    logger.info("[4/6] 生成封面图...")
    from src.cover import generate_cover_from_news

    cover_image_path = ""
    cover_image_url = ""
    cover_key = os.environ.get("AGNES_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    cover_base_url = os.environ.get("AGNES_API_BASE", "https://apihub.agnes-ai.com")
    cover_save_path = os.path.join(docs_dir, "cover.jpg")

    if cover_key:
        cover_image_path = generate_cover_from_news(
            news_list,
            date_str,
            output_path=cover_save_path,
            api_key=cover_key,
            base_url=cover_base_url,
        )
        logger.info("Cover image saved to %s", cover_image_path)
    else:
        logger.info("No API key for cover generation, skipping")

    # === 5. 部署到 GitHub Pages ===
    logger.info("[5/6] 部署到 GitHub Pages...")
    # GitHub Actions 会自动检测 docs/ 目录变化并提交
    # 本地运行时跳过自动提交
    if os.environ.get("CI") == "true":
        import subprocess
        result = subprocess.run(
            ["git", "add", "docs/"],
            cwd=os.path.dirname(__file__),
        )
        if result.returncode == 0:
            logger.info("docs/ staged for commit")
    else:
        logger.info("Local run, skipping git commit")
        logger.info("HTML saved to docs/index.html")

    # === 6. 微信推送 ===
    logger.info("[6/6] 微信推送...")
    from src.wechat import send_daily_news

    wechat_result = send_daily_news(
        news_list,
        date_str,
        pages_url,
        cover_image_path=cover_image_path,
        cover_image_url=cover_image_url,
    )
    logger.info("WeChat push result: %s", wechat_result)

    logger.info("=" * 50)
    logger.info("Done! Today's report: %s", pages_url)
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
