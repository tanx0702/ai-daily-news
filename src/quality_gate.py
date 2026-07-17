"""
LLM 发布前质量门禁

在日报进入 HTML 渲染、封面生成、微信草稿发布之前，对 LLM 生成内容做编辑质检。

职责：
- 检查中文标题/摘要/今日重点/封面标题是否忠于原始新闻
- 检查是否把社区讨论写成官宣
- 检查是否新增原文没有的型号/产品/公司动作
- 给出自动修正建议并写入 news_list

不负责（由 collector/ranking 负责）：
- 时间窗口、source_type、official domain、cross_source_count
- HN/HF/GitHub/arXiv metrics、_confidence_level、_brand_claim
- score / balance / cap
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from src.llm_config import resolve_text_llm_config

logger = logging.getLogger(__name__)

# ── 型号/产品名 pattern（与 summarizer 保持一致，额外增加一些编造型号） ──
_MODEL_PATTERN = re.compile(
    r'\b(GPT[-\s]?\d[\d.]*|Claude\s?\d[\d.]*|Gemini\s?\d[\d.]*|Llama\s?\d[\d.]*|'
    r'Grok[-\s]?\d[\d.]*|Mistral[-\s]?\d[\d.]*|Qwen[-\s]?\d[\d.]*|'
    r'DeepSeek[-\s]?[A-Za-z0-9.]*|Phi[-\s]?\d[\d.]*|Stable\s?Diffusion\s?\d[\d.]*|'
    r'DALL[-\s]?E\s?\d[\d.]*|Midjourney\s?\d[\d.]*|Sora[-\s]?\d[\d.]*|'
    r'Terra|Luna|Atlas|Helios|Nova|Orion|Falcon|Titan|Aurora)\b',
    re.IGNORECASE | re.ASCII,
)

# ── 大厂名单 ──
_MAJOR_BRANDS = [
    "openai", "anthropic", "google deepmind", "deepmind", "google",
    "microsoft", "meta", "xai", "nvidia", "apple", "amazon",
    "mistral", "perplexity",
]

# ── 高风险动词（中文） ──
_HIGH_RISK_VERBS_ZH = [
    "发布", "推出", "上线", "公开", "官宣", "收购", "解除限制",
    "正式开放", "全面开放", "融资", "裁员", "监管", "封禁",
    "拿下", "获批", "上市", "ipo",
]

# ── 社区源禁止的官宣措辞 ──
_COMMUNITY_BANNED_PHRASES = [
    "官方发布", "正式发布", "正式推出", "官方宣布", "官宣",
    "公司发布", "公司宣布", "今日发布", "今日上线",
]

# ── source_type 措辞规则 ──
_SOURCE_TYPE_WORDING: dict[str, dict] = {
    "hn": {
        "allowed": ["社区热议", "开发者讨论", "有用户分享", "Hacker News 讨论"],
        "banned": ["官方发布", "正式推出", "公司官宣"],
    },
    "github": {
        "allowed": ["开源项目", "仓库", "开发者工具", "GitHub 上新"],
        "banned": ["公司官宣", "正式发布", "官方发布"],
    },
    "huggingface": {
        "allowed": ["模型社区", "Hub 上新", "模型发布", "社区分享"],
        "banned": ["大厂官方发布", "公司官宣", "正式发布"],
    },
    "arxiv": {
        "allowed": ["论文提出", "研究团队尝试", "预印本", "学术研究"],
        "banned": ["产品上线", "官方发布", "公司推出"],
    },
}

# ── 标题软化模板 ──
_TITLE_SOFTEN_TEMPLATES = [
    "社区热议 {brand} 新动态",
    "开发者关注 {topic} 相关进展",
    "{source} 报道 {topic} 新动向",
    "{topic} 引发 AI 社区讨论",
]

# ── 摘要弱化模板 ──
_SUMMARY_SOFTEN_PREFIXES = [
    "据社区讨论，",
    "有报道称，",
    "开发者关注到，",
    "相关讨论提到，",
    "有消息称，",
]


def _env_enabled(name: str, default: bool = True) -> bool:
    """
    解析布尔型环境变量。

    支持: 1/true/yes/on 为 True，0/false/no/off 为 False（大小写不敏感）。
    """
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


# ═══════════════════════════════════════════════════════════════════
# Phase 5.1: 本地规则检测
# ═══════════════════════════════════════════════════════════════════


def _extract_model_terms(text: str) -> set[str]:
    """从文本中提取所有型号/产品名（小写）。"""
    if not text:
        return set()
    return set(m.lower() for m in _MODEL_PATTERN.findall(text))


def _check_unsupported_entities(news_list: list[dict]) -> list[dict]:
    """
    检查生成字段中是否新增了原文没有的型号/产品名。

    检查范围：chinese_title, summary, highlight_text
    """
    issues: list[dict] = []

    for i, item in enumerate(news_list):
        # 构建原文文本池
        original_text = " ".join([
            item.get("title", ""),
            item.get("summary", "") if isinstance(item.get("summary"), str) else "",
            item.get("url", ""),
            item.get("source", ""),
        ])
        original_terms = _extract_model_terms(original_text)

        # 检查各生成字段
        for field in ["chinese_title", "summary", "highlight_text"]:
            generated = item.get(field, "")
            if not generated:
                continue
            gen_terms = _extract_model_terms(str(generated))
            suspicious = gen_terms - original_terms

            if suspicious:
                # 去重：如果 item 已有同样的标记，不重复记录
                existing = set(item.get("_suspicious_terms", []))
                new_terms = suspicious - existing
                if not new_terms:
                    continue

                item["_summary_flagged"] = True
                item.setdefault("_suspicious_terms", []).extend(sorted(new_terms))

                issues.append({
                    "type": "unsupported_entity",
                    "severity": "high",
                    "item_index": i,
                    "field": field,
                    "message": f"生成字段包含原文未出现的型号/产品名: {', '.join(sorted(suspicious))}",
                    "evidence": f"field={field}, terms={sorted(suspicious)}",
                })
                logger.warning(
                    "QG entity check #%d: %s in %s — %s",
                    i, sorted(suspicious), field, str(generated)[:60],
                )

    return issues


def _find_brand_in_text(text: str) -> Optional[str]:
    """检测文本中是否包含大厂名称，返回命中的品牌名。"""
    if not text:
        return None
    text_lower = text.lower()
    for brand in _MAJOR_BRANDS:
        if brand in text_lower:
            return brand
    return None


def _find_risk_verb_zh(text: str) -> Optional[str]:
    """检测文本中是否包含高风险动词（中文）。"""
    if not text:
        return None
    for verb in _HIGH_RISK_VERBS_ZH:
        if verb in text:
            return verb
    return None


def _check_brand_claim_risk(news_list: list[dict]) -> list[dict]:
    """
    检测大厂官宣风险：

    同时满足以下条件 = high risk:
    - 生成的标题/摘要包含大厂名 + 高风险动词
    - source_type 是 hn / github / huggingface / arxiv
    - cross_source_count == 0
    - _confidence_level == low 或 _brand_claim.confidence == low
    """
    issues: list[dict] = []

    for i, item in enumerate(news_list):
        st = item.get("source_type", "")
        bc = item.get("_brand_claim", {})
        conf = item.get("_confidence_level", "high")
        metrics = item.get("metrics", {})
        cross = metrics.get("cross_source_count", 0) or 0

        # 只检查社区源
        if st not in ("hn", "github", "huggingface", "arxiv"):
            continue

        # 只检查低/中置信度
        if conf == "high" and bc.get("confidence") != "low":
            continue

        # 检查生成标题
        title = item.get("chinese_title") or item.get("title", "")
        summary = item.get("summary", "")

        brand_in_title = _find_brand_in_text(title)
        verb_in_title = _find_risk_verb_zh(title)
        brand_in_summary = _find_brand_in_text(str(summary))
        verb_in_summary = _find_risk_verb_zh(str(summary))

        title_has_claim = brand_in_title and verb_in_title
        summary_has_claim = brand_in_summary and verb_in_summary

        if title_has_claim or summary_has_claim:
            field = "chinese_title" if title_has_claim else "summary"
            brand = brand_in_title or brand_in_summary
            verb = verb_in_title or verb_in_summary

            severity = "high" if (conf == "low" and cross == 0) else "medium"

            issues.append({
                "type": "low_confidence_brand_claim",
                "severity": severity,
                "item_index": i,
                "field": field,
                "message": (
                    f"社区源({st})出现大厂官宣措辞: '{brand} {verb}'，"
                    f"置信度={conf}, cross={cross}"
                ),
                "evidence": (
                    f"source_type={st}, brand={brand}, verb={verb}, "
                    f"confidence_level={conf}, cross_source_count={cross}"
                ),
            })
            logger.warning(
                "QG brand claim risk #%d: %s %s in %s (conf=%s, cross=%d)",
                i, brand, verb, field, conf, cross,
            )

    return issues


def _check_community_wording(news_list: list[dict]) -> list[dict]:
    """
    检查社区源（hn/github/hf/arxiv）是否使用了官宣措辞。

    规则：
    - hn → 不能出现"官方发布""正式推出"
    - github → 不能出现"公司官宣""正式发布"
    - huggingface → 不能出现"大厂官方发布"
    - arxiv → 不能出现"产品上线""官方发布"
    """
    issues: list[dict] = []

    for i, item in enumerate(news_list):
        st = item.get("source_type", "")
        if st not in _SOURCE_TYPE_WORDING:
            continue

        rules = _SOURCE_TYPE_WORDING[st]
        banned = rules.get("banned", [])

        for field in ["chinese_title", "summary", "highlight_text"]:
            text = item.get(field, "")
            if not text:
                continue

            for phrase in banned:
                if phrase in str(text):
                    issues.append({
                        "type": "community_wording",
                        "severity": "medium",
                        "item_index": i,
                        "field": field,
                        "message": (
                            f"社区源({st})出现禁止措辞: '{phrase}'，"
                            f"应改为: {rules.get('allowed', [])}"
                        ),
                        "evidence": f"source_type={st}, phrase={phrase}, field={field}",
                    })
                    logger.warning(
                        "QG wording #%d: source=%s banned phrase '%s' in %s",
                        i, st, phrase, field,
                    )
                    break  # 每个字段只报告一次

    return issues


def _apply_auto_fix_title(item: dict, idx: int, reason: str) -> Optional[str]:
    """
    自动修正高风险标题，返回修正后的标题。

    优先使用软化模板，失败则使用通用保守标题。
    """
    title = item.get("chinese_title") or item.get("title", "")
    source = item.get("source", "")
    brand = _find_brand_in_text(title)

    # 尝试从标题提取主题词（去掉品牌名和动词后的部分）
    topic = title
    if brand:
        topic = topic.replace(brand, "").replace(brand.title(), "").replace(brand.upper(), "")
    for verb in _HIGH_RISK_VERBS_ZH:
        topic = topic.replace(verb, "")

    topic = topic.strip().strip("，。；：！？、").strip()
    if not topic or len(topic) < 3:
        topic = "AI 新动态"

    # 使用模板
    template = _TITLE_SOFTEN_TEMPLATES[idx % len(_TITLE_SOFTEN_TEMPLATES)]
    display_brand = (brand or "AI").title()
    source_display = source.split(" + ")[0].strip() if source else "媒体"

    softened = template.format(brand=display_brand, topic=topic, source=source_display)

    logger.info("QG auto-fix title #%d: '%s' → '%s'", idx, title[:40], softened)
    return softened


def _apply_auto_fix_summary(item: dict, idx: int) -> Optional[str]:
    """
    在摘要前添加弱表述前缀，使其更保守。
    """
    summary = item.get("summary", "")
    if not summary:
        return None

    prefix = _SUMMARY_SOFTEN_PREFIXES[idx % len(_SUMMARY_SOFTEN_PREFIXES)]

    # 避免重复添加前缀
    for existing_prefix in _SUMMARY_SOFTEN_PREFIXES:
        if summary.startswith(existing_prefix):
            return None

    softened = prefix + summary
    logger.info("QG auto-fix summary #%d: added prefix '%s'", idx, prefix)
    return softened


def _run_local_rules(news_list: list[dict]) -> tuple[list[dict], dict]:
    """
    执行所有本地规则检测，应用自动修正。

    Returns:
        (reviewed_news_list, partial_quality_report)
    """
    all_issues: list[dict] = []
    applied_fixes: list[dict] = []

    # 1. 检测不支持实体/型号
    entity_issues = _check_unsupported_entities(news_list)
    all_issues.extend(entity_issues)

    # 2. 检测大厂官宣风险
    brand_issues = _check_brand_claim_risk(news_list)
    all_issues.extend(brand_issues)

    # 3. 检测社区源措辞
    wording_issues = _check_community_wording(news_list)
    all_issues.extend(wording_issues)

    # ── 应用自动修正 ──
    # 收集需要修正的 item 索引（去重）
    fix_indices: set[int] = set()
    for issue in all_issues:
        if issue["severity"] in ("high", "medium"):
            fix_indices.add(issue["item_index"])

    for idx in sorted(fix_indices):
        item = news_list[idx]
        reasons = [
            iss["message"] for iss in all_issues
            if iss["item_index"] == idx and iss["severity"] in ("high", "medium")
        ]

        # 标记 _quality_gate
        item["_quality_gate"] = {
            "risk_level": "high" if any(
                iss["severity"] == "high" for iss in all_issues if iss["item_index"] == idx
            ) else "medium",
            "issues": [iss["type"] for iss in all_issues if iss["item_index"] == idx],
            "fixes": [],
        }

        # 高风险/中风险：排除出今日重点
        severity = item["_quality_gate"]["risk_level"]
        if severity in ("high", "medium"):
            if not item.get("_highlight_excluded"):
                item["_highlight_excluded"] = "quality_gate: " + "; ".join(
                    iss["type"] for iss in all_issues if iss["item_index"] == idx
                )
                item["_quality_gate"]["fixes"].append("exclude_from_highlights")

        # 高风险：排除出封面标题
        if severity == "high":
            if not item.get("_cover_excluded"):
                item["_cover_excluded"] = "quality_gate: high risk brand claim"
                item["_quality_gate"]["fixes"].append("exclude_from_cover")

        # 修正标题
        has_title_issue = any(
            iss["item_index"] == idx and iss["field"] == "chinese_title"
            and iss["severity"] in ("high", "medium")
            for iss in all_issues
        )
        if has_title_issue:
            old_title = item.get("chinese_title") or item.get("title", "")
            new_title = _apply_auto_fix_title(item, idx, "; ".join(reasons))
            if new_title and new_title != old_title:
                item["chinese_title"] = new_title
                item["_quality_gate"]["fixes"].append("title_softened")
                applied_fixes.append({
                    "item_index": idx,
                    "field": "chinese_title",
                    "before": old_title,
                    "after": new_title,
                    "reason": "; ".join(reasons),
                })

        # 修正摘要
        has_summary_issue = any(
            iss["item_index"] == idx and iss["field"] == "summary"
            and iss["severity"] in ("high", "medium")
            for iss in all_issues
        )
        if has_summary_issue:
            old_summary = str(item.get("summary", ""))
            new_summary = _apply_auto_fix_summary(item, idx)
            if new_summary and new_summary != old_summary:
                item["summary"] = new_summary
                item["_quality_gate"]["fixes"].append("summary_softened")
                applied_fixes.append({
                    "item_index": idx,
                    "field": "summary",
                    "before": old_summary[:80] + ("..." if len(old_summary) > 80 else ""),
                    "after": new_summary[:80] + ("..." if len(new_summary) > 80 else ""),
                    "reason": "; ".join(reasons),
                })

    # ── 构造 quality_report ──
    high_count = sum(1 for iss in all_issues if iss["severity"] == "high")
    medium_count = sum(1 for iss in all_issues if iss["severity"] == "medium")

    if high_count > 0:
        risk_level = "high"
    elif medium_count > 0:
        risk_level = "medium"
    else:
        risk_level = "low"

    report = {
        "enabled": True,
        "pass": high_count == 0,
        "risk_level": risk_level,
        "llm_reviewed": False,
        "issues": all_issues,
        "applied_fixes": applied_fixes,
        "summary": _build_summary(all_issues, applied_fixes),
    }

    return news_list, report


def _build_summary(issues: list[dict], fixes: list[dict]) -> str:
    """生成人类可读的质检摘要。"""
    if not issues and not fixes:
        return "未发现问题，日报可以安全发布。"

    parts = []
    if issues:
        high = sum(1 for i in issues if i["severity"] == "high")
        medium = sum(1 for i in issues if i["severity"] == "medium")
        low = sum(1 for i in issues if i["severity"] == "low")
        parts.append(f"发现 {len(issues)} 个问题（高风险 {high}，中风险 {medium}，低风险 {low}）")

    if fixes:
        parts.append(f"已应用 {len(fixes)} 处自动修正")

    return "；".join(parts)


# ═══════════════════════════════════════════════════════════════════
# Phase 5.2: LLM 质检
# ═══════════════════════════════════════════════════════════════════


def _build_llm_input(news_list: list[dict]) -> list[dict]:
    """构建质检 LLM 输入：精简版 news_list JSON。"""
    compact = []
    for i, item in enumerate(news_list):
        entry = {
            "index": i + 1,
            "original_title": item.get("title", "")[:200],
            "original_summary": (item.get("summary", "") if isinstance(item.get("summary"), str) else "")[:300],
            "generated_title": item.get("chinese_title", "")[:100],
            "generated_summary": (item.get("summary", "") if isinstance(item.get("summary"), str) else "")[:200],
            "highlight_text": item.get("highlight_text", "")[:60],
            "url": item.get("url", "")[:120],
            "source": item.get("source", ""),
            "source_type": item.get("source_type", ""),
            "confidence_level": item.get("_confidence_level", "high"),
            "brand_claim": item.get("_brand_claim", {}),
            "metrics": {
                "hn_score": (item.get("metrics", {}) or {}).get("hn_score", 0) or 0,
                "hn_comments": (item.get("metrics", {}) or {}).get("hn_comments", 0) or 0,
                "cross_source_count": (item.get("metrics", {}) or {}).get("cross_source_count", 0) or 0,
            },
        }
        compact.append(entry)
    return compact


_LLM_QUALITY_SYSTEM_PROMPT = """你是 AI 科技日报的发布前质检编辑。

你不能联网，也不能使用输入之外的信息判断新闻真假。
你只能检查"生成后的中文标题/摘要/今日重点"是否忠于输入给你的原始新闻字段。

重点检查：
1. 是否新增原文没有的型号、版本号、产品名、公司动作、发布时间、融资金额、监管结论。
2. 是否把 Hacker News、GitHub、Hugging Face、arXiv、社区讨论写成官方发布。
3. 低置信度大厂新闻是否进入头条、今日重点、封面标题。
4. 是否把传闻、讨论、猜测写成确定事实。
5. 中文表达是否存在明显错别字、怪词、营销腔。

处理原则：
- 对 unsupported claim 标 high risk。
- 对低置信度大厂官宣类表述，必须建议改成保守标题。
- 不确定时，使用"有报道称""社区讨论""开发者关注""尚待官方确认"等弱表述。
- 不要新增任何输入中没有的事实。

只返回 JSON，不要输出解释性文本。"""


def _extract_json_safe(text: str) -> Optional[dict]:
    """
    从 LLM 响应中安全提取 JSON 对象（容错逻辑）。

    尝试顺序：
    1. 直接解析整个响应
    2. 提取 ```json ... ``` 代码块
    3. 提取第一个 { } 对象
    """
    text = text.strip()

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 块
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试提取第一个 { } 块
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def _run_llm_review(
    news_list: list[dict],
    *,
    api_key: str,
    model: str,
    base_url: str,
    timeout: int,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    调用 LLM 做质检。

    Returns:
        (llm_issues, llm_fixes, global_notes)
    """
    from openai import OpenAI

    compact_input = _build_llm_input(news_list)

    user_prompt = json.dumps(
        {
            "news_items": compact_input,
            "instruction": (
                "请对以上 news_items 逐条检查。返回 JSON 格式：\n"
                '{"pass": true/false, "risk_level": "low|medium|high", '
                '"issues": [...], "fixes": [...], "global_notes": [...]}\n\n'
                "issues 每条包含: type, severity(high|medium|low), item_index, field, message, evidence\n"
                "fixes 每条包含: item_index, chinese_title(修正后), summary(修正后), "
                "highlight_text(修正后), exclude_from_highlights(bool), exclude_from_cover(bool), reason\n"
                "global_notes: 全局性备注，如当天的整体风险倾向"
            ),
        },
        ensure_ascii=False,
    )

    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _LLM_QUALITY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=int(os.environ.get("QUALITY_GATE_MAX_TOKENS", "4000")),
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content.strip()

        # 使用容错 JSON 提取
        result = _extract_json_safe(content)
        if result is None:
            raise ValueError(f"Failed to extract valid JSON from LLM response: {content[:200]}")

        if not isinstance(result, dict):
            raise ValueError(f"Expected dict, got {type(result).__name__}")

        llm_issues = result.get("issues", [])
        llm_fixes = result.get("fixes", [])
        global_notes = result.get("global_notes", [])
        # 标准化：如果 LLM 返回字符串而非列表
        if isinstance(global_notes, str):
            global_notes = [global_notes] if global_notes.strip() else []

        logger.info(
            "LLM quality review done: pass=%s, risk=%s, issues=%d, fixes=%d",
            result.get("pass"), result.get("risk_level"),
            len(llm_issues), len(llm_fixes),
        )

        return llm_issues, llm_fixes, global_notes

    except Exception as e:
        logger.warning("LLM quality review failed: %s, keeping local rule results only", e)
        return [], [], [f"LLM 质检请求失败: {e}"]


def _apply_llm_fixes(news_list: list[dict], llm_fixes: list[dict]) -> list[dict]:
    """
    应用 LLM 返回的修正。

    LLM 输入使用 1-based index（与用户 prompt 一致），
    这里需要转换为 0-based Python list index。

    与本地规则不同，LLM 的修正优先级更高（可能会覆盖本地修正）。
    """
    applied: list[dict] = []

    for fix in llm_fixes:
        raw_idx = fix.get("item_index", -1)
        # LLM 使用 1-based index，转换为 0-based
        idx = raw_idx - 1 if raw_idx > 0 else -1
        if idx < 0 or idx >= len(news_list):
            logger.warning("QG LLM fix: invalid item_index %d (0-based %d), skipping", raw_idx, idx)
            continue

        item = news_list[idx]
        reason = fix.get("reason", "LLM quality review")

        # 初始化 _quality_gate
        qg = item.setdefault("_quality_gate", {"risk_level": "medium", "issues": [], "fixes": []})

        # 修正标题（带安全守卫：验证新旧标题至少共享 2 个中文字符或关键词）
        new_title = fix.get("chinese_title", "")
        if new_title and new_title != item.get("chinese_title", ""):
            old = item.get("chinese_title", "")
            # 安全守卫：检查新旧标题是否相关（共享一定数量的字符）
            old_chars = set(old)
            new_chars = set(new_title)
            shared = old_chars & new_chars
            if len(shared) < 2 and len(old) > 5:
                logger.warning(
                    "QG LLM fix title #%d SKIPPED: insufficient overlap "
                    "(shared=%d chars) between '%s' and '%s'",
                    idx, len(shared), old[:30], new_title[:30],
                )
            else:
                item["chinese_title"] = new_title
                qg["fixes"].append("title_softened_by_llm")
                applied.append({
                    "item_index": idx,
                    "field": "chinese_title",
                    "before": old,
                    "after": new_title,
                    "reason": reason,
                })
                logger.info("QG LLM fix title #%d: '%s' → '%s'", idx, old[:40], new_title[:40])

        # 修正摘要（带安全守卫）
        new_summary = fix.get("summary", "")
        if new_summary and new_summary != item.get("summary", ""):
            old = str(item.get("summary", ""))
            old_chars = set(old)
            new_chars = set(new_summary)
            shared = old_chars & new_chars
            if len(shared) < 3 and len(old) > 10:
                logger.warning(
                    "QG LLM fix summary #%d SKIPPED: insufficient overlap "
                    "(shared=%d chars)", idx, len(shared),
                )
            else:
                item["summary"] = new_summary
                qg["fixes"].append("summary_softened_by_llm")
                applied.append({
                    "item_index": idx,
                    "field": "summary",
                    "before": old[:80],
                    "after": new_summary[:80],
                    "reason": reason,
                })

        # 修正今日重点
        new_highlight = fix.get("highlight_text", "")
        if new_highlight and new_highlight != item.get("highlight_text", ""):
            old = item.get("highlight_text", "")
            item["highlight_text"] = new_highlight
            qg["fixes"].append("highlight_fixed_by_llm")
            applied.append({
                "item_index": idx,
                "field": "highlight_text",
                "before": old,
                "after": new_highlight,
                "reason": reason,
            })

        # 排除今日重点
        if fix.get("exclude_from_highlights"):
            if not item.get("_highlight_excluded"):
                item["_highlight_excluded"] = f"quality_gate (LLM): {reason}"
                qg["fixes"].append("exclude_from_highlights_by_llm")

        # 排除封面
        if fix.get("exclude_from_cover"):
            if not item.get("_cover_excluded"):
                item["_cover_excluded"] = f"quality_gate (LLM): {reason}"
                qg["fixes"].append("exclude_from_cover_by_llm")

    return applied


_RISK_RANK = {"low": 0, "medium": 1, "high": 2}


def _max_risk(a: str, b: str) -> str:
    """Return the higher of two risk labels."""
    return a if _RISK_RANK.get(a, 0) >= _RISK_RANK.get(b, 0) else b


def _apply_llm_issue_marks(news_list: list[dict], llm_issues: list[dict]) -> None:
    """
    Copy LLM issue severity back onto individual news items.

    LLM issue indexes are 1-based, matching the LLM prompt and _apply_llm_fixes().
    High-risk items are excluded from publishing, highlights, and cover selection.
    """
    for issue in llm_issues:
        raw_idx = issue.get("item_index", -1)
        try:
            raw_idx = int(raw_idx)
        except (TypeError, ValueError):
            raw_idx = -1
        idx = raw_idx - 1 if raw_idx > 0 else -1
        if idx < 0 or idx >= len(news_list):
            logger.warning("QG LLM issue: invalid item_index %s (0-based %d), skipping", raw_idx, idx)
            continue

        severity = str(issue.get("severity", "low")).lower()
        if severity not in _RISK_RANK:
            severity = "low"
        issue_type = issue.get("type") or "llm_quality_issue"
        reason = issue.get("message") or issue_type

        item = news_list[idx]
        qg = item.setdefault("_quality_gate", {"risk_level": "low", "issues": [], "fixes": []})
        qg["risk_level"] = _max_risk(qg.get("risk_level", "low"), severity)
        if issue_type not in qg["issues"]:
            qg["issues"].append(issue_type)

        if severity == "high":
            publish_reason = f"quality_gate (LLM): {reason}"
            item["_publish_excluded"] = publish_reason
            if not item.get("_highlight_excluded"):
                item["_highlight_excluded"] = publish_reason
            if not item.get("_cover_excluded"):
                item["_cover_excluded"] = publish_reason
            if "exclude_from_publish_by_llm" not in qg["fixes"]:
                qg["fixes"].append("exclude_from_publish_by_llm")


def _publish_exclusion_reason(item: dict) -> str:
    if item.get("_publish_excluded"):
        return str(item["_publish_excluded"])
    qg = item.get("_quality_gate", {}) or {}
    if qg.get("risk_level") == "high":
        return "quality_gate: high risk"
    return ""


def _remaining_risk_level(news_list: list[dict]) -> str:
    risk = "low"
    for item in news_list:
        qg = item.get("_quality_gate", {}) or {}
        risk = _max_risk(risk, qg.get("risk_level", "low"))
    return risk


def _filter_publishable_items(news_list: list[dict], target_count: int) -> tuple[list[dict], dict]:
    """Remove high-risk items and keep the first target_count publishable candidates."""
    selected: list[dict] = []
    removed: list[dict] = []

    for item in news_list:
        reason = _publish_exclusion_reason(item)
        if reason:
            removed.append({
                "title": item.get("chinese_title") or item.get("title", ""),
                "source": item.get("source", ""),
                "source_type": item.get("source_type", ""),
                "reason": reason,
            })
            continue
        if len(selected) < target_count:
            selected.append(item)

    filter_report = {
        "enabled": True,
        "target_count": target_count,
        "selected_count": len(selected),
        "removed_count": len(removed),
        "removed_items": removed,
        "insufficient_publishable_items": len(selected) < target_count,
    }
    return selected, filter_report


# ═══════════════════════════════════════════════════════════════════
# Debug 报告
# ═══════════════════════════════════════════════════════════════════


def _save_quality_report(
    quality_report: dict,
    date_str: str,
    docs_dir: str,
) -> None:
    """保存 quality.json 和 quality.md 到 docs/debug/。"""
    debug_dir = os.path.join(docs_dir, "debug")
    os.makedirs(debug_dir, exist_ok=True)

    # ── quality.json ──
    json_path = os.path.join(debug_dir, f"{date_str}-quality.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(quality_report, f, ensure_ascii=False, indent=2, default=str)
    logger.info("Quality report JSON saved to %s", json_path)

    # ── quality.md ──
    md_path = os.path.join(debug_dir, f"{date_str}-quality.md")
    lines = _build_quality_md(quality_report, date_str)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info("Quality report MD saved to %s", md_path)


def _build_quality_md(report: dict, date_str: str) -> list[str]:
    """构建人类可读的 quality.md。"""
    lines = [
        f"# AI Daily News — Quality Gate Report ({date_str})",
        "",
        "## Summary",
        "",
        f"- **Enabled**: {report.get('enabled', False)}",
        f"- **Pass**: {report.get('pass', False)}",
        f"- **Risk Level**: {report.get('risk_level', 'unknown')}",
        f"- **LLM Reviewed**: {report.get('llm_reviewed', False)}",
        f"- **Strict**: {report.get('strict', False)}",
        f"- **Blocked Publish**: {report.get('blocked_publish', False)}",
        f"- **Issues**: {len(report.get('issues', []))}",
        f"- **Applied Fixes**: {len(report.get('applied_fixes', []))}",
        "",
    ]

    summary_text = report.get("summary", "")
    if summary_text:
        lines.append(f"> {summary_text}")
        lines.append("")

    # Issues 详情
    issues = report.get("issues", [])
    if issues:
        lines.append("## Issues")
        lines.append("")
        for j, iss in enumerate(issues, 1):
            lines.append(f"### {j}. {iss.get('type', '?')} / {iss.get('severity', '?')}")
            lines.append("")
            lines.append(f"- **Item**: {iss.get('item_index', '?')}")
            lines.append(f"- **Field**: {iss.get('field', '?')}")
            lines.append(f"- **Message**: {iss.get('message', '')}")
            lines.append(f"- **Evidence**: {iss.get('evidence', '')}")
            lines.append("")

    # Applied fixes
    fixes = report.get("applied_fixes", [])
    if fixes:
        lines.append("## Applied Fixes")
        lines.append("")
        for k, fix in enumerate(fixes, 1):
            lines.append(f"### {k}. Item {fix.get('item_index', '?')} — {fix.get('field', '?')}")
            lines.append("")
            lines.append(f"- **Before**: {fix.get('before', '')}")
            lines.append(f"- **After**: {fix.get('after', '')}")
            lines.append(f"- **Reason**: {fix.get('reason', '')}")
            lines.append("")

    # LLM global notes
    global_notes = report.get("global_notes", [])
    # 防御：如果 LLM 返回的是字符串而非列表
    if isinstance(global_notes, str):
        global_notes = [global_notes] if global_notes.strip() else []
    if global_notes:
        lines.append("## LLM Global Notes")
        lines.append("")
        for note in global_notes:
            if isinstance(note, str) and note.strip():
                lines.append(f"- {note.strip()}")
        lines.append("")

    # 阻断信息
    if report.get("blocked_publish"):
        lines.append("## ⚠️ WeChat Publish Blocked")
        lines.append("")
        lines.append("Strict mode 下发现 high risk，已跳过微信草稿发布。")
        lines.append("HTML、latest.json、debug 报告已正常生成。")
        lines.append("")

    return lines


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════


def review_daily(
    news_list: list[dict],
    *,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    timeout: int = 30,
    strict: bool = False,
    date_str: str = "",
    docs_dir: str = "",
    target_count: int | None = None,
    filter_high_risk: bool = False,
) -> tuple[list[dict], dict]:
    """
    对当日入选新闻做编辑质检。

    流程：
    1. 本地规则检测（实体/型号、大厂官宣风险、社区源措辞）
    2. 自动修正高风险标题和摘要
    3. [可选] LLM 质检
    4. 生成 quality report

    Args:
        news_list: 入选新闻列表（需已有 chinese_title + summary）
        api_key: LLM API Key，为空只做本地规则
        model: LLM 模型名
        base_url: API 地址
        timeout: LLM 超时秒数
        strict: 严格模式
        date_str: 日期字符串
        docs_dir: docs 目录路径
        target_count: 发布目标条数；配合 filter_high_risk 从后备候选回填
        filter_high_risk: 是否从发布列表移除 high risk 单条

    Returns:
        (reviewed_news_list, quality_report)
    """
    if not news_list:
        return news_list, {
            "enabled": False,
            "pass": True,
            "risk_level": "low",
            "strict": strict,
            "blocked_publish": False,
            "issues": [],
            "applied_fixes": [],
            "summary": "空新闻列表，跳过质检。",
        }

    logger.info("Quality gate: reviewing %d items (strict=%s)", len(news_list), strict)

    # 1. 本地规则检测 + 自动修正
    news_list, report = _run_local_rules(news_list)
    report["strict"] = strict
    report["blocked_publish"] = False

    # 2. LLM 质检（如果可用）
    text_config = resolve_text_llm_config(api_key=api_key, model=model, base_url=base_url)
    if text_config.api_key:
        logger.info("Quality gate: running LLM review...")
        llm_issues, llm_fixes, global_notes = _run_llm_review(
            news_list,
            api_key=text_config.api_key,
            model=text_config.model,
            base_url=text_config.base_url,
            timeout=timeout,
        )

        report["issues"].extend(llm_issues)
        report["global_notes"] = global_notes
        report["llm_reviewed"] = True
        report["llm_review_failed"] = any(
            isinstance(note, str) and "LLM 质检请求失败" in note
            for note in global_notes
        )
        _apply_llm_issue_marks(news_list, llm_issues)

        # 应用 LLM 修正
        llm_applied = _apply_llm_fixes(news_list, llm_fixes)
        report["applied_fixes"].extend(llm_applied)

        # LLM 可能降低/提高风险等级
        llm_risk = "low"
        if any(iss.get("severity") == "high" for iss in llm_issues):
            llm_risk = "high"
        elif any(iss.get("severity") == "medium" for iss in llm_issues):
            llm_risk = "medium"

        # 取最高风险
        if report["risk_level"] == "low" and llm_risk != "low":
            report["risk_level"] = llm_risk
        elif report["risk_level"] == "medium" and llm_risk == "high":
            report["risk_level"] = "high"

    else:
        report["llm_reviewed"] = False
        report["global_notes"] = []
        report["llm_review_failed"] = False

    # 3. 发布安全过滤：移除 high risk 单条并从后备候选回填。
    publish_filter_report = None
    if filter_high_risk and target_count is not None:
        news_list, publish_filter_report = _filter_publishable_items(news_list, target_count)
        report["publish_filter"] = publish_filter_report
        if publish_filter_report["removed_count"] > 0:
            logger.info(
                "Quality gate publish filter: removed=%d selected=%d/%d",
                publish_filter_report["removed_count"],
                publish_filter_report["selected_count"],
                publish_filter_report["target_count"],
            )

    # 4. 更新 pass 和 blocked_publish
    if publish_filter_report:
        remaining_risk = _remaining_risk_level(news_list)
        if publish_filter_report["insufficient_publishable_items"]:
            report["risk_level"] = "high"
        else:
            report["risk_level"] = remaining_risk

    if report.get("llm_review_failed") and report["risk_level"] == "low":
        report["risk_level"] = "medium"

    report["pass"] = (report["risk_level"] != "high")
    report["blocked_publish"] = strict and (report["risk_level"] == "high")
    report["summary"] = _build_summary(report["issues"], report["applied_fixes"])
    if report.get("llm_review_failed"):
        report["summary"] = (
            "LLM 质检失败，已按本地规则和发布过滤结果降级为 medium；"
            + report["summary"]
        )
    if publish_filter_report:
        report["summary"] = (
            f"{report['summary']}；发布过滤移除 "
            f"{publish_filter_report['removed_count']} 条高风险候选，"
            f"最终可发布 {publish_filter_report['selected_count']}/"
            f"{publish_filter_report['target_count']} 条。"
        )

    # 5. 保存 debug 报告
    if docs_dir:
        try:
            _save_quality_report(report, date_str, docs_dir)
        except Exception as e:
            logger.warning("Failed to save quality report: %s", e)

    if report["blocked_publish"]:
        logger.warning(
            "QUALITY GATE BLOCKED PUBLISH: risk=%s, issues=%d",
            report["risk_level"], len(report["issues"]),
        )

    logger.info(
        "Quality gate done: pass=%s, risk=%s, issues=%d, fixes=%d",
        report["pass"], report["risk_level"],
        len(report["issues"]), len(report["applied_fixes"]),
    )

    return news_list, report
