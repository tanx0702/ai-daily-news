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
            "你是一个专业的 AI 新闻编辑。"
            "请将以下每条英文新闻标题翻译成中文，并为它生成一句中文摘要。"
            "严格按以下 JSON 数组格式回复，不要有其他内容："
            "[{\"index\": 1, \"chinese_title\": \"翻译后的标题\", \"summary\": \"摘要内容\"}]"
            "index 字段对应原始序号（从 1 开始）。"
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
                for item in results:
                    orig_idx = item.get("index", 0) - 1
                    if 0 <= orig_idx < len(batch_news):
                        batch_news[orig_idx]["chinese_title"] = item.get("chinese_title", batch_news[orig_idx]["title"])
                        batch_news[orig_idx]["summary"] = item.get("summary", "")[:200]
                        logger.info("  Batch summary #%d: %s", orig_idx + 1,
                                    batch_news[orig_idx]["chinese_title"][:40])
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
                                    "请将以下英文新闻标题翻译成中文，并为它生成一句摘要。"
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
