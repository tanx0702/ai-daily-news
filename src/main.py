"""
AI 每日新闻推送 Agent - 主程序

每日定时流程：
1. 采集 RSS 新闻
2. LLM 生成摘要
3. 渲染 HTML 日报
4. AI 封面图生成
5. 保存 latest.json（供 Flask 读取）
6. 创建微信草稿（后台手动发布）
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

# 初始化日志系统(必须在其他模块导入前)
from src.logger_config import setup_logging
setup_logging()

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    """解析布尔型环境变量：1/true/yes/on → True，0/false/no/off → False。"""
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def main():
    docs_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    lock_path = os.environ.get(
        "DAILY_RUN_LOCK_PATH",
        os.path.join(docs_dir, ".daily_run.lock"),
    )
    lock_ttl = int(os.environ.get("DAILY_RUN_LOCK_TTL_SECONDS", str(6 * 60 * 60)))

    from src.run_guard import RunLockError, single_run_lock

    try:
        with single_run_lock(lock_path, ttl_seconds=lock_ttl):
            _run_pipeline()
    except RunLockError as e:
        logger.warning("Another daily run appears active, skipping: %s", e)
        sys.exit(2)


def _run_pipeline():
    from src.time_utils import report_date_str

    date_str = report_date_str()
    docs_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    pages_url = os.environ.get(
        "PAGES_URL",
        f"https://{os.environ.get('DOMAIN', 'tankex.xyz')}",
    )
    logger.info("=" * 50)
    logger.info("AI Daily News Agent - %s", date_str)
    logger.info("=" * 50)

    # === 1. 采集新闻 ===
    logger.info("[1/6] 采集 RSS 新闻...")
    from src.collector import collect_news

    top_n = int(os.environ.get("DAILY_TOP_N", "10"))
    rss_timeout = int(os.environ.get("DAILY_RSS_TIMEOUT", "30"))

    qg_enabled = _env_bool("ENABLE_LLM_QUALITY_GATE", True)
    qg_strict = _env_bool("QUALITY_GATE_STRICT", False)
    publish_filter_enabled = _env_bool("ENABLE_PUBLISH_SAFETY_FILTER", True)
    safety_reserve_n = int(os.environ.get("DAILY_SAFETY_RESERVE_N", "6"))
    collect_top_n = top_n
    if qg_enabled and publish_filter_enabled:
        collect_top_n = top_n + max(safety_reserve_n, 0)
        logger.info(
            "Publish safety filter enabled: collecting %d items (%d target + %d reserve)",
            collect_top_n, top_n, max(safety_reserve_n, 0),
        )

    news_list = collect_news(top_n=collect_top_n, rss_timeout=rss_timeout)

    if not news_list:
        logger.error("No news collected! Aborting.")
        sys.exit(1)

    from src.evidence import preserve_source_evidence

    for item in news_list:
        preserve_source_evidence(item)

    # Fix Windows console encoding for emoji output
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    logger.info("Collected %d news items", len(news_list))

    # === 2. LLM 摘要 ===
    logger.info("[2/6] 生成 LLM 摘要...")
    from src.llm_config import resolve_image_llm_config, resolve_text_llm_config
    from src.summarizer import summarize_news

    text_llm = resolve_text_llm_config()
    api_key = text_llm.api_key
    model = text_llm.model
    llm_base_url = text_llm.base_url
    llm_timeout = int(os.environ.get("DAILY_LLM_TIMEOUT", "15"))

    if api_key:
        news_list = summarize_news(
            news_list,
            api_key=api_key,
            model=model,
            base_url=llm_base_url,
            timeout=llm_timeout,
        )
    else:
        logger.info("LLM API key not set, skipping LLM summary")

    # === 2.5 质检门禁 ===
    quality_report = {}
    blocked_publish = False

    qg_timeout = int(os.environ.get("QUALITY_GATE_TIMEOUT", str(llm_timeout)))

    if qg_enabled and news_list:
        logger.info("[2.5/6] 发布前质检...")
        from src.quality_gate import review_daily

        news_list, quality_report = review_daily(
            news_list,
            api_key=api_key,
            model=model,
            base_url=llm_base_url,
            timeout=qg_timeout,
            strict=qg_strict,
            date_str=date_str,
            docs_dir=docs_dir,
            target_count=top_n,
            filter_high_risk=publish_filter_enabled,
        )
        blocked_publish = quality_report.get("blocked_publish", False)
        logger.info(
            "Quality gate: pass=%s, risk=%s, blocked=%s",
            quality_report.get("pass"),
            quality_report.get("risk_level"),
            blocked_publish,
        )
    elif not qg_enabled:
        logger.info("[2.5/6] 质检已禁用 (ENABLE_LLM_QUALITY_GATE=0)")
    else:
        logger.info("[2.5/6] 跳过质检 (无新闻数据)")

    # === 2.55 正文媒体资源解析 ===
    if _env_bool("ENABLE_ARTICLE_IMAGE_FETCH", True) and news_list:
        logger.info("[2.55/6] 解析正文配图...")
        from src.media_assets import resolve_article_media

        img_timeout = int(os.environ.get("ARTICLE_IMAGE_TIMEOUT", "8"))
        news_list, media_report = resolve_article_media(
            news_list,
            docs_dir=docs_dir,
            pages_url=pages_url,
            date_str=date_str,
            timeout=img_timeout,
        )
        logger.info(
            "Media: %d original images, %d text-only cards",
            media_report.get("with_original_image", 0),
            media_report.get("text_only", 0),
        )
    else:
        media_report = {}

    # === 2.6 生成今日重点编辑摘要 + 封面主题选择 ===
    cover_title = "今日AI要闻"
    cover_subject = None
    if api_key and news_list:
        logger.info("[2.6/6] 生成今日重点编辑摘要 + 封面标题...")
        from src.summarizer import generate_highlights, generate_cover_title

        # generate_highlights() 内部会跳过低置信度 item，
        # 返回的列表长度等于 eligible items 数量（最多 3）
        highlights = generate_highlights(
            news_list,
            api_key=api_key,
            model=model,
            base_url=llm_base_url,
            timeout=llm_timeout,
        )
        # 将 highlights 按顺序映射回符合条件的 news_list items
        hi = 0
        for item in news_list:
            # 检查是否被 quality gate 或 brand claim 排除
            is_excluded = bool(item.get("_highlight_excluded"))
            bc = item.get("_brand_claim", {})
            is_low_conf = (
                bc.get("confidence") == "low"
                or item.get("_confidence_level") == "low"
            )
            if is_excluded or is_low_conf:
                # 低置信度/质量门禁排除：不分配 highlight
                if not item.get("_highlight_excluded"):
                    item["_highlight_excluded"] = "低置信度品牌声明"
                continue
            if hi < len(highlights):
                item["highlight_text"] = highlights[hi]
                hi += 1
            if hi >= 3:
                break

        # 封面标题：检查是否被 quality gate 排除
        cover_excluded = False
        if news_list:
            top_item = news_list[0]
            if top_item.get("_cover_excluded"):
                cover_excluded = True
        if cover_excluded:
            cover_title = "今日AI要闻"
            logger.info("Cover title: top item excluded by quality gate, using generic title")
        else:
            cover_title = generate_cover_title(
                news_list,
                api_key=api_key,
                model=model,
                base_url=llm_base_url,
                timeout=llm_timeout,
            )
        logger.info("Cover title: %s", cover_title)
    else:
        logger.info("Skipping highlights/cover title (no API key)")

    # === 3. 生成 HTML 日报 ===
    logger.info("[3/6] 生成 HTML 日报...")
    from src.pipeline_artifacts import render_and_save_daily_html

    render_and_save_daily_html(
        news_list,
        date_str,
        docs_dir,
        pages_url,
        github_repo=os.environ.get("GITHUB_REPO", "tankex/ai-daily-news"),
    )

    # === 4. 生成封面图 ===
    logger.info("[4/6] 生成封面图...")
    from src.cover import generate_cover_from_news, select_cover_subject

    image_llm = resolve_image_llm_config()
    cover_key = image_llm.api_key
    cover_base_url = image_llm.base_url
    cover_model = image_llm.model
    cover_save_path = os.path.join(docs_dir, "cover.jpg")

    # 选择封面主题（从可信候选池中选择）
    if cover_subject is None:
        cover_subject = select_cover_subject(news_list)
    logger.info(
        "Cover subject: mode=%s, title=%s",
        cover_subject.get("mode"), cover_subject.get("cover_title", ""),
    )

    if cover_key:
        try:
            generate_cover_from_news(
                news_list,
                date_str,
                output_path=cover_save_path,
                api_key=cover_key,
                base_url=cover_base_url,
                model=cover_model,
                cover_title=cover_title,
                cover_subject=cover_subject,
            )
            logger.info("Cover image saved to %s", cover_save_path)
        except Exception as e:
            logger.warning("Cover generation failed (non-fatal): %s", e)
    else:
        logger.info("No image API key for cover generation, skipping")

    # 生成公众号推文预览
    from src.pipeline_artifacts import render_and_save_wechat_preview

    render_and_save_wechat_preview(news_list, date_str, docs_dir, pages_url)
    logger.info("WeChat preview saved to docs/wechat.html")

    # === 5. 保存新闻数据 + debug 报告 ===
    logger.info("[5/6] 保存新闻数据...")
    _annotate_reasons(news_list)

    from src.pipeline_artifacts import build_latest_data, json_serial, save_latest_data

    latest_data = build_latest_data(
        news_list,
        date_str,
        pages_url,
        generated_at=datetime.now(timezone.utc).isoformat(),
        quality_report=quality_report,
        cover_subject=cover_subject,
        media_report=media_report,
    )
    latest_path = save_latest_data(latest_data, docs_dir, default=json_serial)
    logger.info("News data saved to %s", latest_path)

    _generate_debug_reports(news_list, date_str, docs_dir, cover_subject=cover_subject)

    # === 6. 创建微信草稿 ===
    logger.info("[6/6] 创建微信草稿...")

    if blocked_publish:
        logger.warning(
            "QUALITY GATE BLOCKED: 严格模式下发现 high risk，跳过微信草稿发布。"
        )
        logger.warning(
            "HTML/debug/latest.json 已生成，请人工审核后手动发布。"
        )
        wechat_result = {"status": "blocked", "reason": "quality_gate_high_risk"}
    else:
        from src.wechat_draft import publish_daily_article

        cover_path = os.path.join(docs_dir, "cover.jpg")
        wechat_result = publish_daily_article(
            news_list,
            date_str,
            pages_url,
            cover_path=cover_path if os.path.isfile(cover_path) else "",
        )
    logger.info("WeChat publish result: %s", wechat_result)

    logger.info("=" * 50)
    logger.info("Done! Today's report: %s", pages_url)
    logger.info("=" * 50)


def _annotate_reasons(news_list: list[dict]):
    """为每条新闻生成 selected_reason（解释为什么入选）。"""
    for item in news_list:
        reasons = []
        st = item.get("source_type", "?")
        scores = item.get("scores", {})
        metrics = item.get("metrics", {})

        if st == "rss":
            reasons.append("RSS 权威源")
        elif st == "hn":
            hn_s = metrics.get("hn_score", 0) or 0
            hn_c = metrics.get("hn_comments", 0) or 0
            cross = metrics.get("cross_source_count", 0) or 0
            if cross > 0:
                reasons.append(f"HN 跨源 (cross={cross})")
            elif hn_s >= 10:
                reasons.append(f"HN 高热度 (score={hn_s})")
            elif hn_c >= 2:
                reasons.append(f"HN 高讨论 (comments={hn_c})")
            else:
                reasons.append("HN 精选")
        elif st == "huggingface":
            likes = metrics.get("hf_likes", 0) or 0
            downloads = metrics.get("hf_downloads", 0) or 0
            fallback = item.get("_hf_fallback")
            if fallback:
                reasons.append(f"HF 边缘 (likes={likes}<10, dl={downloads})")
            else:
                reasons.append(f"HF 模型 (likes={likes}, dl={downloads})")
            # 检查模型名称是否命中强主题
            title_lower = item.get("title", "").lower()
            strong_topics = []
            for kw in ["qwen", "llama", "mistral", "deepseek", "phi", "coder",
                        "agent", "rag", "multimodal", "vision", "diffusion"]:
                if kw in title_lower:
                    strong_topics.append(kw)
            if strong_topics:
                reasons.append(f"主题:{','.join(strong_topics)}")
        elif st == "arxiv":
            arxiv_signal = metrics.get("arxiv_signal", 0) or 0
            reasons.append(f"arXiv 论文 (signal={arxiv_signal})")
            # 检测核心 AI 关键词
            title_lower = item.get("title", "").lower()
            paper_kws = []
            for kw in ["llm", "agent", "multimodal", "rag", "diffusion",
                        "transformer", "vla", "3d", "reinforcement",
                        "alignment", "vision", "language model",
                        "generation", "understanding", "reasoning"]:
                if kw in title_lower:
                    paper_kws.append(kw)
            if paper_kws:
                reasons.append(f"关键词:{','.join(paper_kws[:4])}")
            arxiv_cat = (item.get("tags", []) or [])
            if arxiv_cat:
                reasons.append(f"分类:{arxiv_cat[0]}")
        elif st == "github":
            stars = metrics.get("github_stars", 0) or 0
            reasons.append(f"GitHub 项目 (stars={stars})")

        if item.get("_hn_low_quality"):
            reasons.append("[HN-LQ 降权]")
        if item.get("_hf_low_quality"):
            reasons.append("[HF-LQ 降权]")
        bc = item.get("_brand_claim", {})
        if bc.get("confidence") == "low":
            reasons.append(f"[低置信度品牌声明] {bc.get('reason', '')}")
        elif bc.get("confidence") == "medium":
            reasons.append(f"[品牌声明-中等置信度] {bc.get('reason', '')}")
        publish_risk = item.get("_publish_risk", {})
        if publish_risk:
            reasons.append(f"[发布风险] {publish_risk.get('reason', '')}")
        qg = item.get("_quality_gate", {})
        if qg:
            qg_risk = qg.get("risk_level", "")
            if qg_risk:
                reasons.append(f"[质检:{qg_risk}] {';'.join(qg.get('issues', []))}")
        freshness = scores.get("freshness", 0)
        reasons.append(f"新鲜度={freshness:.0f}")

        # 最终置信度
        conf = item.get("_confidence_level", "high")
        reasons.append(f"置信度={conf}")

        item["selected_reason"] = " | ".join(reasons) if reasons else "综合评分"


def _generate_debug_reports(
    news_list: list[dict],
    date_str: str,
    docs_dir: str,
    cover_subject: dict | None = None,
):
    """生成 debug 报告：candidates.json 和 ranking.md。"""
    debug_dir = os.path.join(docs_dir, "debug")
    os.makedirs(debug_dir, exist_ok=True)

    # ---- candidates.json ----
    candidates_path = os.path.join(debug_dir, f"{date_str}-candidates.json")

    # 精简输出：只保留关键字段
    compact = []
    for item in news_list:
        entry = {
            "title": (item.get("chinese_title") or item["title"])[:100],
            "url": item.get("url", "")[:100],
            "source": item.get("source", ""),
            "source_type": item.get("source_type", ""),
            "published_at": item.get("published_at"),
            "scores": item.get("scores", {}),
            "metrics": item.get("metrics", {}),
            "selected_reason": item.get("selected_reason", ""),
            "confidence_level": item.get("_confidence_level", "high"),
        }
        bc = item.get("_brand_claim", {})
        if bc:
            entry["brand_claim"] = {
                "brand": bc.get("brand", ""),
                "confidence": bc.get("confidence", ""),
                "reason": bc.get("reason", ""),
            }
        publish_risk = item.get("_publish_risk", {})
        if publish_risk:
            entry["publish_risk"] = {
                "category": publish_risk.get("category", ""),
                "severity": publish_risk.get("severity", ""),
                "reason": publish_risk.get("reason", ""),
            }
        if item.get("_highlight_excluded"):
            entry["highlight_excluded"] = item["_highlight_excluded"]
        if item.get("_diversity_swap"):
            entry["diversity_swap"] = item["_diversity_swap"]
        if item.get("_summary_flagged"):
            entry["summary_flagged"] = True
            entry["suspicious_terms"] = item.get("_suspicious_terms", [])
        qg = item.get("_quality_gate", {})
        if qg:
            entry["quality_gate"] = {
                "risk_level": qg.get("risk_level", ""),
                "issues": qg.get("issues", []),
                "fixes": qg.get("fixes", []),
            }
        if item.get("_cover_excluded"):
            entry["cover_excluded"] = item["_cover_excluded"]
        compact.append(entry)

    with open(candidates_path, "w", encoding="utf-8") as f:
        json.dump(compact, f, ensure_ascii=False, indent=2, default=str)
    logger.info("Debug candidates saved to %s", candidates_path)

    # ---- ranking.md: 人类可读的排名解释 ----
    ranking_path = os.path.join(debug_dir, f"{date_str}-ranking.md")

    lines = [
        f"# AI Daily News — Ranking Report ({date_str})",
        f"",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Total selected: {len(news_list)} items",
        f"",
        f"## Cover",
        f"",
    ]

    if cover_subject:
        cover_item = cover_subject.get("item") or {}
        cover_title = cover_item.get("chinese_title") or cover_item.get("title") or cover_subject.get("cover_title", "")
        matches_top1 = bool(cover_item is news_list[0]) if news_list and cover_item else False
        lines.extend([
            f"- **Source**: {cover_subject.get('cover_source', '') or 'unknown'}",
            f"- **Mode**: {cover_subject.get('mode', '')}",
            f"- **Story type**: {cover_subject.get('story_type', '')}",
            f"- **Headline**: {cover_subject.get('cover_headline', '')}",
            f"- **Story title**: {cover_title}",
            f"- **Matches Top 1**: {str(matches_top1).lower()}",
            f"- **Reason**: {cover_subject.get('reason', '')}",
            f"",
        ])
    else:
        lines.extend(["- No cover subject recorded.", ""])

    lines.extend([
        f"## Final Rankings",
        f"",
    ])

    for i, item in enumerate(news_list, 1):
        title = (item.get("chinese_title") or item["title"])[:80]
        st = item.get("source_type", "?")
        source = item.get("source", "")
        scores = item.get("scores", {})
        metrics = item.get("metrics", {})
        reason = item.get("selected_reason", "")
        highlight = item.get("highlight_text", "")
        diversity = item.get("_diversity_swap", "")
        conf = item.get("_confidence_level", "high")
        bc = item.get("_brand_claim", {})
        excluded = item.get("_highlight_excluded", "")
        cover_excluded = item.get("_cover_excluded", "")
        qg = item.get("_quality_gate", {})
        flagged = " ⚠️摘要可疑" if item.get("_summary_flagged") else ""
        qg_flag = f" 🔍质检:{qg.get('risk_level', '')}" if qg else ""

        lines.append(f"### {i}. {title}{flagged}{qg_flag}")
        lines.append(f"")
        lines.append(f"- **Type**: {st} | **Source**: {source}")
        lines.append(f"- **Score**: final={scores.get('final', 0):.1f}, fresh={scores.get('freshness', 0):.0f}, comm={scores.get('community', 0):.1f}")
        lines.append(f"- **Metrics**: HN={metrics.get('hn_score',0) or 0}/{metrics.get('hn_comments',0) or 0}c, GH={metrics.get('github_stars',0) or 0}*, HF={metrics.get('hf_likes',0) or 0}L/{metrics.get('hf_downloads',0) or 0}D, arxiv={metrics.get('arxiv_signal',0) or 0}")
        lines.append(f"- **置信度**: {conf}{' — ' + bc.get('reason', '') if bc.get('reason') else ''}")
        lines.append(f"- **Why**: {reason}")
        if highlight:
            lines.append(f"- **编辑摘要**: {highlight}")
        if excluded:
            lines.append(f"- **⚠️ 未入今日重点**: {excluded}")
        if cover_excluded:
            lines.append(f"- **⚠️ 未入选封面**: {cover_excluded}")
        if qg:
            lines.append(f"- **🔍 质检**: risk={qg.get('risk_level', '')}, issues={qg.get('issues', [])}, fixes={qg.get('fixes', [])}")
        if diversity:
            lines.append(f"- **⚠️ {diversity}**")
        lines.append(f"")

    with open(ranking_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info("Debug ranking saved to %s", ranking_path)


if __name__ == "__main__":
    main()
