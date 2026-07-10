"""
LLM 摘要模块

批量为新闻生成中文翻译标题 + 中文摘要。
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

# ── 幻觉检测：常见 AI 型号/产品名 pattern，用于校验 LLM 输出 ──
# 如果 LLM 生成的中文标题出现了原文没有的这些词，触发降级
_MODEL_PATTERN = re.compile(
    r'\b(GPT[-\s]?\d[\d.]*|Claude\s?\d[\d.]*|Gemini\s?\d[\d.]*|Llama\s?\d[\d.]*|'
    r'Grok[-\s]?\d[\d.]*|Mistral[-\s]?\d[\d.]*|Qwen[-\s]?\d[\d.]*|'
    r'DeepSeek[-\s]?[A-Za-z0-9.]*|Phi[-\s]?\d[\d.]*|Stable\s?Diffusion\s?\d[\d.]*|'
    r'DALL[-\s]?E\s?\d[\d.]*|Midjourney\s?\d[\d.]*|Sora[-\s]?\d[\d.]*|'
    r'Terra|Luna|Atlas|Helios|Nova|Orion|Falcon|Titan|Aurora)\b',
    re.IGNORECASE | re.ASCII,
)

# 低置信度 brand claim 标记 key（从 collector 传入）
_LOW_CONFIDENCE_KEY = "low_confidence_brand_claim"


def validate_summary_facts(chinese_title: str, original: dict) -> dict:
    """
    轻量后校验：检测 LLM 生成的中文标题是否编造了原文没有的型号/产品名。

    Args:
        chinese_title: LLM 生成的中文标题
        original: 原始新闻 dict，包含 title, summary, url, source 等

    Returns:
        {"valid": bool, "suspicious_terms": [str], "action": "keep"|"fallback"}
    """
    if not chinese_title or not original:
        return {"valid": True, "suspicious_terms": [], "action": "keep"}

    # 提取原文中出现的所有型号/产品名
    original_text = (
        original.get("title", "") + " " +
        original.get("summary", "") + " " +
        original.get("url", "") + " " +
        original.get("source", "")
    )
    original_matches = set(m.lower() for m in _MODEL_PATTERN.findall(original_text))

    # 提取中文标题中的型号/产品名
    title_matches = set(m.lower() for m in _MODEL_PATTERN.findall(chinese_title))

    # 找出新增的（标题有但原文没有的）
    suspicious = title_matches - original_matches

    if suspicious:
        logger.warning(
            "validate_summary_facts: suspicious terms %s found in title but not in original",
            suspicious,
        )
        return {
            "valid": False,
            "suspicious_terms": list(suspicious),
            "action": "fallback",
        }

    return {"valid": True, "suspicious_terms": [], "action": "keep"}


# Agnes API 配置
AGNES_BASE_URL = os.environ.get(
    "AGNES_API_BASE", "https://apihub.agnes-ai.com/v1"
)
DEFAULT_MODEL = "agnes-2.0-flash"

# 批量处理：每次最多处理 5 条新闻
BATCH_SIZE = 5


def _extract_json(text: str) -> Optional[dict]:
    """从 LLM 响应中提取 JSON 对象。"""
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
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def summarize_news(
    news_list: list[dict],
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    timeout: int = 30,
    base_url: str = AGNES_BASE_URL,
) -> list[dict]:
    """
    批量为新闻列表生成中文翻译标题和摘要。

    采用分批处理策略：每 BATCH_SIZE 条新闻合并为一次 LLM 调用，
    大幅降低 API 调用次数和超时概率。

    Args:
        news_list: 新闻列表，每条包含 title, url, source, summary 等
        api_key: Agnes API Key，默认从 AGNES_API_KEY 环境变量读取
        model: 模型名称，默认 agnes-2.0-flash
        timeout: 单次调用超时秒数（建议 >= 30）
        base_url: API 基础地址，默认 Agnes hub

    Returns:
        补充了 chinese_title 和 summary 的新闻列表
    """
    api_key = api_key or os.environ.get("AGNES_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("AGNES_API_KEY not set, skipping LLM summary")
        return news_list

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    # 找出需要处理的新闻索引
    need_summarize = []
    for i, news in enumerate(news_list):
        if news.get("chinese_title"):
            continue
        need_summarize.append(i)

    if not need_summarize:
        logger.info("All news already have summaries, skipping LLM")
        return news_list

    # 分批处理
    for batch_start in range(0, len(need_summarize), BATCH_SIZE):
        batch_indices = need_summarize[batch_start:batch_start + BATCH_SIZE]
        batch_news = [news_list[i] for i in batch_indices]

        logger.info("Processing batch %d-%d (%d items)",
                     batch_start + 1, batch_start + len(batch_indices), len(batch_indices))

        # 构建批量 prompt
        headlines = "\n".join(
            f"{idx+1}. {news['title']}"
            for idx, news in enumerate(batch_news, start=batch_start)
        )

        system_prompt = (
            "你是一个专业的 AI 新闻编辑，擅长撰写自然流畅的中文科技新闻。"
            "请将以下每条英文新闻标题改写成中文公众号标题，要求：\n"
            "1. 避免生硬直译，用中文读者习惯的表达方式\n"
            "2. 保留核心事实，不夸张不标题党\n"
            "3. 如果原标题是陈述句，可改为设问句或更有吸引力的表达\n"
            "4. 标题控制在 15-30 个中文字符\n"
            "5. 【重要】禁止编造原文没有的型号、版本号、时间、公司动作、融资金额\n"
            "   - 不要把社区讨论写成官方发布\n"
            "   - 不要把传闻写成事实\n"
            "   - 不确定时使用弱表述：「据社区讨论」「有报道称」「开发者讨论」\n"
            "   - 如果原文没有给出具体版本号（如 GPT-5.6），绝对不要添加\n"
            "6. 不要为了吸引眼球改写事实\n"
            "同时为每条新闻生成一段中文摘要（90-160 字，2-3 句），"
            "说明发生了什么、为什么重要、后续值得关注什么。\n"
            "严格按以下 JSON 数组格式回复，不要有其他内容："
            "[{\"chinese_title\": \"中文标题\", \"summary\": \"摘要内容\"}]"
            "数组中每个元素的顺序对应输入的标题顺序（第一条对应第一个标题）。"
        )

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": headlines},
                ],
                temperature=0.3,
                max_tokens=2000,
            )
            content = response.choices[0].message.content.strip()
            results = _extract_json(content)

            if isinstance(results, list):
                # 按顺序映射，不依赖 LLM 返回的 index（LLM 可能返回全局序号）
                for pos, item in enumerate(results):
                    if pos < len(batch_news):
                        c_title = item.get("chinese_title", "")
                        # 幻觉校验：检查是否编造了型号/产品名
                        if c_title:
                            validation = validate_summary_facts(c_title, batch_news[pos])
                            if validation["action"] == "fallback":
                                logger.warning(
                                    "  Batch summary #%d FALLBACK: suspicious terms %s in '%s'",
                                    pos + 1, validation["suspicious_terms"], c_title[:40],
                                )
                                # 标记但保留原 LLM 结果（避免因误判丢弃），记录到 debug 字段
                                batch_news[pos]["_summary_flagged"] = True
                                batch_news[pos]["_suspicious_terms"] = validation["suspicious_terms"]
                        batch_news[pos]["chinese_title"] = c_title or batch_news[pos]["title"]
                        batch_news[pos]["summary"] = item.get("summary", "")[:200]
                        logger.info("  Batch summary #%d: %s", pos + 1,
                                    batch_news[pos]["chinese_title"][:40])
            else:
                raise ValueError(f"Expected list, got {type(results).__name__}")

        except Exception as e:
            logger.warning("Batch failed for items %d-%d: %s",
                           batch_start + 1, batch_start + len(batch_indices), e)
            # 降级：逐条处理这批
            for news in batch_news:
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "请将以下英文新闻标题改写成自然的中文公众号标题，"
                                    "避免生硬直译，保留核心事实不标题党。"
                                    "禁止编造原文没有的型号、版本号、时间、金额。"
                                    "同时生成一段中文摘要（90-160 字，2-3 句），"
                                    "说明发生了什么、为什么重要、后续值得关注什么。"
                                    "按 JSON 格式回复：{\"chinese_title\": \"...\", \"summary\": \"...\"}"
                                ),
                            },
                            {"role": "user", "content": news["title"]},
                        ],
                        temperature=0.3,
                        max_tokens=350,
                    )
                    content = response.choices[0].message.content.strip()
                    result = _extract_json(content)
                    if result:
                        news["chinese_title"] = result.get("chinese_title", news["title"])
                        news["summary"] = result.get("summary", "")[:200]
                    else:
                        news["chinese_title"] = news["title"]
                        news["summary"] = ""
                except Exception as e2:
                    logger.warning("Fallback single summary failed for '%s': %s", news["title"][:30], e2)
                    news["chinese_title"] = news["title"]
                    news["summary"] = ""

    return news_list


def generate_highlights(
    news_list: list[dict],
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    base_url: str = AGNES_BASE_URL,
    timeout: int = 30,
) -> list[str]:
    """
    为当日新闻生成编辑式「今日重点」（25-45 字/条）。

    今日重点回答"今天 AI 圈最值得关注的变化是什么"，
    包含影响判断，不是简单列标题。

    低置信度大厂传闻不会进入今日重点候选。

    Args:
        news_list: 新闻列表（需已有 chinese_title + summary + _confidence_level）
        api_key: Agnes API Key
        model: 模型名称
        base_url: API 地址
        timeout: 超时秒数

    Returns:
        最多 3 条编辑摘要字符串列表，失败返回空列表
    """
    if not news_list:
        return []

    # 过滤：只从高置信度新闻中提取今日重点
    eligible = []
    for item in news_list:
        bc = item.get("_brand_claim", {})
        if bc.get("confidence") == "low":
            # 低置信度大厂传闻：跳过，不进入今日重点
            item["_highlight_excluded"] = f"低置信度品牌声明: {bc.get('reason', '')}"
            continue
        if item.get("_confidence_level") == "low":
            item["_highlight_excluded"] = "低置信度"
            continue
        eligible.append(item)

    if not eligible:
        logger.info("No eligible items for highlights (all low confidence)")
        return []

    top_items = eligible[:3]

    api_key = api_key or os.environ.get("AGNES_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.info("No API key for highlights, using chinese_title fallback")
        return [item.get("chinese_title") or item.get("title", "") for item in top_items]

    # 构建输入：标题 + 摘要 + 来源
    items_text = "\n".join(
        f"{i+1}. 标题：{item.get('chinese_title') or item.get('title', '')}\n"
        f"   摘要：{item.get('summary', '')}\n"
        f"   来源：{item.get('source', '')} ({item.get('source_type', '')})"
        for i, item in enumerate(top_items)
    )

    system_prompt = (
        "你是一个 AI 科技新闻主编，负责撰写每日「今日重点」。\n"
        "今日重点应该回答「今天 AI 圈最值得关注的变化是什么」，不是简单复述标题。\n"
        "要求：\n"
        "1. 每条重点是一句完整的话，控制在 25-45 个中文字符\n"
        "2. 尽量包含影响判断或趋势解读，例如：\n"
        "   - 「开源工具热度上升，开发者工作流仍是今日主线」\n"
        "   - 「大模型发布继续加速，但需优先确认官方来源」\n"
        "   - 「论文与模型社区动态补充了产业新闻之外的技术线索」\n"
        "3. 自然流畅的中文，适合公众号读者阅读\n"
        "4. 不要编造原文没有的事实、型号、版本号\n"
        "5. 不要把传闻写成官宣，不确定时使用弱表述\n"
        "6. 如果当天没有足够强的重点，宁可写得保守，不要硬凑爆点\n"
        "严格按 JSON 数组格式回复：[\"重点1\", \"重点2\", \"重点3\"]"
    )

    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": items_text},
            ],
            temperature=0.4,
            max_tokens=400,
        )
        content = response.choices[0].message.content.strip()
        results = _extract_json(content)

        if isinstance(results, list) and len(results) >= 1:
            highlights = []
            for i in range(len(top_items)):
                if i < len(results) and isinstance(results[i], str) and results[i].strip():
                    text = results[i].strip()
                    # 截断过长
                    if len(text) > 50:
                        text = text[:45] + "…"
                    highlights.append(text)
                else:
                    # 降级：使用 chinese_title
                    fallback = top_items[i].get("chinese_title") or top_items[i].get("title", "")
                    highlights.append(fallback)
            logger.info("Generated %d highlights", len(highlights))
            return highlights
        else:
            raise ValueError(f"Expected list, got {type(results).__name__}")

    except Exception as e:
        logger.warning("Highlights generation failed: %s, using fallback", e)
        return [item.get("chinese_title") or item.get("title", "") for item in top_items]


def generate_cover_title(
    news_list: list[dict],
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    base_url: str = AGNES_BASE_URL,
    timeout: int = 30,
) -> str:
    """
    从 Top 1 新闻生成 12-20 个中文字符的封面标题。

    Args:
        news_list: 新闻列表（取第 1 条，需已有 chinese_title + summary）
        api_key: Agnes API Key
        model: 模型名称
        base_url: API 地址
        timeout: 超时秒数

    Returns:
        封面标题字符串，失败时返回 "AI 日报"
    """
    if not news_list:
        return "AI 日报"

    top = news_list[0]

    # 可信度门禁：头条低置信度 → 使用通用标题
    bc = top.get("_brand_claim", {})
    if (bc.get("confidence") == "low"
            or top.get("_confidence_level") == "low"
            or top.get("_cover_excluded")):
        logger.info("Cover title: top item low confidence or excluded by quality gate, using generic title")
        return "今日 AI 热点速览"

    topic = top.get("chinese_title") or top.get("title", "")

    api_key = api_key or os.environ.get("AGNES_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        # 无 API：截取 chinese_title 的前 20 个字符
        short = topic[:20].rstrip("，。；：！？、")
        return short if len(short) >= 8 else "AI 日报"

    system_prompt = (
        "你是一个 AI 新闻封面编辑。"
        "请根据以下新闻标题和摘要，提炼出一个封面主标题。"
        "要求：\n"
        "1. 中文，12-20 个中文字符\n"
        "2. 概括核心主题，有阅读吸引力\n"
        "3. 不夸张不标题党\n"
        "4. 只返回标题文字，不要标点符号，不要引号\n"
        "严格按 JSON 格式回复：{\"cover_title\": \"封面标题\"}"
    )

    summary = top.get("summary", "")

    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"标题：{topic}\n摘要：{summary}"},
            ],
            temperature=0.4,
            max_tokens=100,
        )
        content = response.choices[0].message.content.strip()
        result = _extract_json(content)

        if isinstance(result, dict) and result.get("cover_title"):
            title = result["cover_title"].strip().strip("“”\"'")
            # 确保在合理长度范围内
            if 8 <= len(title) <= 30:
                logger.info("Cover title generated: %s", title)
                return title
            # 过长就截断
            if len(title) > 30:
                return title[:20].rstrip("，。；：！？、")

        raise ValueError(f"Invalid cover title response: {content[:80]}")

    except Exception as e:
        logger.warning("Cover title generation failed: %s, using fallback", e)
        short = topic[:20].rstrip("，。；：！？、")
        return short if len(short) >= 8 else "AI 日报"


def summarize_for_wechat(
    news_list: list[dict],
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    top_n: int = 5,
    base_url: str = AGNES_BASE_URL,
) -> str:
    """
    为微信推送生成摘要文本。

    Args:
        news_list: 新闻列表
        api_key: Agnes API Key
        model: 模型名称
        top_n: 取前 N 条新闻生成摘要
        base_url: API 基础地址

    Returns:
        格式化后的微信推送摘要文本
    """
    api_key = api_key or os.environ.get("AGNES_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        items = news_list[:top_n]
        titles = [item.get("chinese_title") or item["title"] for item in items]
        return "\n".join(f"  {i+1}. {t}" for i, t in enumerate(titles))

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=15)

    headlines = "\n".join(
        f"{i+1}. {item.get('chinese_title') or item['title']}（来源：{item['source']}）"
        for i, item in enumerate(news_list[:top_n])
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个 AI 新闻编辑。"
                        "以下为今日 AI 新闻标题列表，"
                        "请从中挑选最重要的 3-5 条，用简洁的语言生成一段微信推送摘要。"
                        "格式要求：每条一行，以 emoji 开头，不超过 80 字。"
                        "不要输出日期和条数统计。"
                    ),
                },
                {"role": "user", "content": headlines},
            ],
            temperature=0.5,
            max_tokens=300,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("Failed to generate WeChat summary: %s", e)
        titles = [item.get("chinese_title") or item["title"] for item in news_list[:top_n]]
        return "\n".join(f"  {i+1}. {t}" for i, t in enumerate(titles))
