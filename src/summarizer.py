"""
LLM 摘要模块

为每条新闻生成中文摘要。
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


def summarize_news(
    news_list: list[dict],
    api_key: Optional[str] = None,
    model: str = "gpt-4o-mini",
    timeout: int = 15,
) -> list[dict]:
    """
    为新闻列表生成中文摘要。

    Args:
        news_list: 新闻列表，每条包含 title, url, source, summary 等
        api_key: OpenAI API Key，默认从 OPENAI_API_KEY 环境变量读取
        model: 模型名称，默认 gpt-4o-mini
        timeout: 单次调用超时秒数

    Returns:
        补充了 summary 的新闻列表（原 summary 为空时才会生成）
    """
    api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, skipping LLM summary")
        return news_list

    client = OpenAI(api_key=api_key, timeout=timeout)

    for i, news in enumerate(news_list):
        # 已有摘要的跳过
        if news.get("summary"):
            continue

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个专业的 AI 新闻编辑。"
                            "请用中文为以下新闻标题生成一句简洁的摘要，不超过 100 字。"
                            "只输出摘要内容，不要输出标题或其他多余信息。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": news["title"],
                    },
                ],
                temperature=0.3,
                max_tokens=150,
            )
            summary = response.choices[0].message.content.strip()
            news["summary"] = summary
            logger.info("Generated summary for news #%d: %s", i + 1, news["title"][:30])
        except Exception as e:
            logger.warning("Failed to generate summary for '%s': %s", news["title"][:30], e)
            # 降级：保留原标题作为摘要
            news["summary"] = news["title"]

    return news_list


def summarize_for_wechat(
    news_list: list[dict],
    api_key: Optional[str] = None,
    model: str = "gpt-4o-mini",
    top_n: int = 5,
) -> str:
    """
    为微信推送生成摘要文本。

    Args:
        news_list: 新闻列表
        api_key: OpenAI API Key
        model: 模型名称
        top_n: 取前 N 条新闻生成摘要

    Returns:
        格式化后的微信推送摘要文本
    """
    api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        # 没有 API Key，直接用标题
        items = news_list[:top_n]
        return "\n".join(f"  {i+1}. {item['title']}" for i, item in enumerate(items))

    client = OpenAI(api_key=api_key, timeout=15)

    # 取前 top_n 条新闻标题
    headlines = "\n".join(
        f"{i+1}. {item['title']}（来源：{item['source']}）"
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
        return headlines
