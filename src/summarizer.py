"""
LLM 摘要模块

为每条新闻生成中文翻译标题 + 中文摘要。
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

# Agnes API 配置
AGNES_BASE_URL = os.environ.get(
    "AGNES_API_BASE", "https://apihub.agnes-ai.com/v1"
)
DEFAULT_MODEL = "agnes-2.0-flash"


def summarize_news(
    news_list: list[dict],
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    timeout: int = 15,
    base_url: str = AGNES_BASE_URL,
) -> list[dict]:
    """
    为新闻列表生成中文翻译标题和摘要。

    Args:
        news_list: 新闻列表，每条包含 title, url, source, summary 等
        api_key: Agnes API Key，默认从 AGNES_API_KEY 环境变量读取
        model: 模型名称，默认 agnes-2.0-flash
        timeout: 单次调用超时秒数
        base_url: API 基础地址，默认 Agnes hub

    Returns:
        补充了 chinese_title 和 summary 的新闻列表
    """
    api_key = api_key or os.environ.get("AGNES_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("AGNES_API_KEY not set, skipping LLM summary")
        return news_list

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    for i, news in enumerate(news_list):
        # 已有中文标题的跳过
        if news.get("chinese_title"):
            continue

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个专业的 AI 新闻编辑。"
                            "请将以下英文新闻标题翻译成中文，并为它生成一句摘要。"
                            "请按 JSON 格式回复，不要有其他内容："
                            '{"chinese_title": "翻译后的标题", "summary": "摘要内容"}'
                        ),
                    },
                    {
                        "role": "user",
                        "content": news["title"],
                    },
                ],
                temperature=0.3,
                max_tokens=200,
            )
            content = response.choices[0].message.content.strip()
            # 解析 JSON 响应
            import json as _json
            try:
                result = _json.loads(content)
                news["chinese_title"] = result.get("chinese_title", news["title"])
                news["summary"] = result.get("summary", "")[:200]
            except _json.JSONDecodeError:
                # 降级：如果不是合法 JSON，尝试提取
                lines = content.split("\n")
                news["chinese_title"] = lines[0] if lines else news["title"]
                news["summary"] = lines[1] if len(lines) > 1 else ""[:200]

            logger.info("Translated & summarized news #%d: %s", i + 1, news["title"][:30])
        except Exception as e:
            logger.warning("Failed to generate summary for '%s': %s", news["title"][:30], e)
            # 降级：保留原标题
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
