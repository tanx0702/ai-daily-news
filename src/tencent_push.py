"""
腾讯云 SCF 推送客户端（历史方案）

历史上此模块用于把生成的新闻数据 POST 到腾讯云 SCF HTTP 触发器，
由 SCF 存入 COS 供微信客服消息读取使用。

当前生产链路已经迁移到 VPS + Docker Compose：
cron 每天执行 `python -m src.main`，nginx 托管 `docs/`，Flask 处理微信回调，
`src.wechat_draft` 只创建公众号草稿，后台手动发布。

环境变量（旧 SCF 方案）：
  NEWS_SCF_URL    - SCF HTTP 触发器地址，如 https://service-xxxxx.ap-guangzhou.myqcloud.com/release/

调用方式：
  from src.tencent_push import push_to_tencent_scf
  push_to_tencent_scf(news_list, date_str, pages_url)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def push_to_tencent_scf(
    news_list: list[dict],
    date_str: Optional[str] = None,
    pages_url: Optional[str] = None,
    scf_url: Optional[str] = None,
    cover_image_url: Optional[str] = None,
) -> dict:
    """
    将新闻数据推送到腾讯云 SCF。

    Args:
        news_list: 新闻列表，每项包含 title, chinese_title, summary, url, source 等字段
        date_str: 日期字符串，如 "2026-07-02"
        pages_url: 日报页面 URL
        scf_url: SCF HTTP 触发器地址，默认从环境变量 NEWS_SCF_URL 读取
        cover_image_url: 封面图 URL

    Returns:
        推送结果字典
    """
    scf_url = scf_url or os.environ.get("NEWS_SCF_URL", "")
    if not scf_url:
        logger.warning("NEWS_SCF_URL not set, skipping SCF push")
        return {"status": "skipped", "reason": "NEWS_SCF_URL not configured"}

    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    payload = {
        "date": date_str,
        "news": news_list,
        "html_url": pages_url or "",
        "cover_image_url": cover_image_url or "",
        "pushed_at": datetime.now(timezone.utc).isoformat(),
    }

    url = f"{scf_url}?source=github"

    try:
        resp = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info("SCF push result: %s", result)
        return {"status": "ok", "data": result}
    except requests.RequestException as e:
        logger.error("Failed to push to SCF: %s", e)
        return {"status": "error", "message": str(e)}
    except json.JSONDecodeError:
        logger.warning("SCF returned non-JSON response: %s", resp.text[:200])
        return {"status": "ok", "raw": resp.text[:200]}
