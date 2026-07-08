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
            "同时为每条新闻生成一句中文摘要（40-80 字）。"
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
                max_tokens=1000,
            )
            content = response.choices[0].message.content.strip()
            results = _extract_json(content)

            if isinstance(results, list):
                # 按顺序映射，不依赖 LLM 返回的 index（LLM 可能返回全局序号）
                for pos, item in enumerate(results):
                    if pos < len(batch_news):
                        batch_news[pos]["chinese_title"] = item.get("chinese_title", batch_news[pos]["title"])
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
                                    "同时生成一句中文摘要（40-80 字）。"
                                    "按 JSON 格式回复：{\"chinese_title\": \"...\", \"summary\": \"...\"}"
                                ),
                            },
                            {"role": "user", "content": news["title"]},
                        ],
                        temperature=0.3,
                        max_tokens=200,
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
    为 Top 3 新闻生成编辑式一句话摘要（今日重点）。

    每条摘要格式类似：
    "Anthropic 扩展 Claude Cowork 到移动端和网页端，继续补齐多端协作场景。"

    Args:
        news_list: 新闻列表（取前 3 条，需已有 chinese_title + summary）
        api_key: Agnes API Key
        model: 模型名称
        base_url: API 地址
        timeout: 超时秒数

    Returns:
        3 条编辑摘要字符串列表，失败时返回 chinese_title 作为降级
    """
    top3 = news_list[:3]
    if not top3:
        return []

    api_key = api_key or os.environ.get("AGNES_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.info("No API key for highlights, using chinese_title fallback")
        return [item.get("chinese_title") or item.get("title", "") for item in top3]

    # 构建输入：标题 + 摘要
    items_text = "\n".join(
        f"{i+1}. 标题：{item.get('chinese_title') or item.get('title', '')}\n"
        f"   摘要：{item.get('summary', '')}"
        for i, item in enumerate(top3)
    )

    system_prompt = (
        "你是一个专业的 AI 新闻主编。"
        "请根据以下 3 条新闻的标题和摘要，为每条新闻写一句编辑推荐语。"
        "要求：\n"
        "1. 每条推荐语是一句完整的话，不是标题复读\n"
        "2. 自然流畅的中文，适合公众号读者阅读\n"
        "3. 每条控制在 25-50 个中文字符\n"
        "4. 包含核心事实 + 一句话点出意义或看点\n"
        "示例格式：\"Anthropic 扩展 Claude Cowork 到移动端，继续补齐多端协作场景。\"\n"
        "严格按 JSON 数组格式回复：[\"推荐语1\", \"推荐语2\", \"推荐语3\"]"
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
            for i in range(3):
                if i < len(results) and isinstance(results[i], str) and results[i].strip():
                    highlights.append(results[i].strip()[:80])
                else:
                    # 降级：使用 chinese_title
                    fallback = top3[i].get("chinese_title") or top3[i].get("title", "")
                    highlights.append(fallback)
            logger.info("Generated %d highlights", len(highlights))
            return highlights
        else:
            raise ValueError(f"Expected list, got {type(results).__name__}")

    except Exception as e:
        logger.warning("Highlights generation failed: %s, using fallback", e)
        return [item.get("chinese_title") or item.get("title", "") for item in top3]


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
