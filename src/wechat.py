"""
微信公众号推送模块

调用微信公众号 API 群发图文消息。
"""

import io
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def _get_access_token(app_id: str, app_secret: str) -> Optional[str]:
    """获取微信 access_token。"""
    url = (
        "https://api.weixin.qq.com/cgi-bin/token"
        f"?grant_type=client_credential&appid={app_id}&secret={app_secret}"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "access_token" in data:
            return data["access_token"]
        logger.error("Failed to get access_token: %s", data)
        return None
    except Exception as e:
        logger.error("Failed to get access_token: %s", e)
        return None


def _upload_thumb_image(
    access_token: str,
    image_source: str,
    source_type: str = "path",
) -> Optional[str]:
    """
    上传封面图素材，返回 thumb_media_id。

    Args:
        access_token: 微信 access_token
        image_source: 封面图片来源，可以是本地路径或 URL
        source_type: 来源类型 — "path"（本地文件）或 "url"（网络链接）

    Returns:
        thumb_media_id，失败返回 None
    """
    url = (
        "https://api.weixin.qq.com/cgi-bin/material/add_material"
        f"?access_token={access_token}&type=image"
    )
    try:
        if source_type == "url":
            img_resp = requests.get(image_source, timeout=30)
            img_resp.raise_for_status()
            files = {"media": ("thumb.jpg", io.BytesIO(img_resp.content), "image/jpeg")}
        else:
            with open(image_source, "rb") as f:
                files = {"media": ("thumb.jpg", f, "image/jpeg")}
        resp = requests.post(url, files=files, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "media_id" in data:
            return data["media_id"]
        logger.error("Failed to upload thumb image: %s", data)
        return None
    except FileNotFoundError:
        logger.warning("Cover image not found at %s, using placeholder", image_source)
        return None
    except Exception as e:
        logger.error("Failed to upload thumb image: %s", e)
        return None


def _upload_news(
    access_token: str,
    title: str,
    author: str,
    digest: str,
    content: str,
    url: str,
    thumb_media_id: str,
) -> Optional[str]:
    """
    上传图文消息素材，返回 media_id。

    Args:
        access_token: 微信 access_token
        title: 标题
        author: 作者
        digest: 摘要（100字以内）
        content: 正文 HTML
        url: 阅读原文链接
        thumb_media_id: 封面图 media_id

    Returns:
        发布任务的 media_id，失败返回 None
    """
    publish_url = f"https://api.weixin.qq.com/cgi-bin/freepublish/submit?access_token={access_token}"

    payload = {
        "title": title,
        "thumb_media_id": thumb_media_id,
        "author": author,
        "digest": digest,
        "content": content,
        "show_cover_pic": 1,
        "url": url,
    }

    try:
        resp = requests.post(publish_url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "media_id" in data:
            return data["media_id"]
        logger.error("Failed to upload news: %s", data)
        return None
    except Exception as e:
        logger.error("Failed to upload news: %s", e)
        return None


def _mass_send(
    access_token: str,
    media_id: str,
) -> dict:
    """
    群发图文消息给所有关注者。

    Args:
        access_token: 微信 access_token
        media_id: 图文消息 media_id

    Returns:
        群发结果
    """
    send_url = (
        "https://api.weixin.qq.com/cgi-bin/message/mass/sendall"
        f"?access_token={access_token}"
    )

    payload = {
        "filter": {"is_to_all": True},
        "mpnews": {"media_id": media_id},
        "msgtype": "mpnews",
    }

    try:
        resp = requests.post(send_url, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("Failed to mass send: %s", e)
        return {"error": str(e)}


def send_daily_news(
    news_list: list[dict],
    date_str: str,
    pages_url: str,
    app_id: Optional[str] = None,
    app_secret: Optional[str] = None,
    thumb_media_id: str = "",
    cover_image_path: str = "",
    cover_image_url: str = "",
    retry: int = 2,
) -> dict:
    """
    发送每日新闻图文消息。

    Args:
        news_list: 新闻列表
        date_str: 日期字符串
        pages_url: GitHub Pages 日报页面 URL
        app_id: 公众号 AppID
        app_secret: 公众号 AppSecret
        thumb_media_id: 封面图 media_id（公众号后台上传获取）
        cover_image_path: 本地封面图片路径（用于自动上传）
        cover_image_url: 封面图 URL（从 Agnes Image API 生成）
        retry: 失败重试次数

    Returns:
        推送结果字典
    """
    app_id = app_id or os.environ.get("WECHAT_APP_ID", "")
    app_secret = app_secret or os.environ.get("WECHAT_APP_SECRET", "")

    if not app_id or not app_secret:
        logger.warning("WECHAT_APP_ID or WECHAT_APP_SECRET not set, skipping push")
        return {"status": "skipped", "reason": "credentials not set"}

    # 获取 access_token
    access_token = _get_access_token(app_id, app_secret)
    if not access_token:
        return {"status": "failed", "reason": "cannot_get_access_token"}

    # 处理封面图
    if not thumb_media_id and cover_image_path:
        thumb_media_id = _upload_thumb_image(access_token, cover_image_path, source_type="path")
        if not thumb_media_id:
            logger.warning("No valid thumb_media_id from local path, trying URL")
    if not thumb_media_id and cover_image_url:
        thumb_media_id = _upload_thumb_image(access_token, cover_image_url, source_type="url")
        if not thumb_media_id:
            logger.warning("No valid thumb_media_id, push may fail")

    # 构造图文内容
    title = f"🤖 AI 日报 {date_str}"
    author = "AI Daily News Agent"

    # 摘要：前 5 条新闻标题
    highlights = news_list[:5]
    digest_parts = [f"{i+1}. {item['title']}" for i, item in enumerate(highlights)]
    digest = " ".join(digest_parts)[:100]

    # 正文 HTML
    content_items = []
    for i, item in enumerate(news_list[:10]):  # 正文最多展示 10 条
        summary = item.get("summary", "") or item["title"]
        content_items.append(f"<p><strong>{i+1}. {item['title']}</strong></p>")
        if summary and summary != item["title"]:
            content_items.append(f"<p>{summary}</p>")
        content_items.append(f'<p><a href="{item["url"]}" target="_blank">阅读原文 →</a></p>')
        content_items.append("<hr style='border:1px solid #eee;margin:12px 0;'>")

    content = "".join(content_items) + f'<p style="text-align:center;color:#888;">' \
             f'<a href="{pages_url}" target="_blank">👉 查看完整日报（含全部 {len(news_list)} 条新闻）</a></p>'

    # 上传图文并群发（重试）
    for attempt in range(retry + 1):
        media_id = _upload_news(access_token, title, author, digest, content, pages_url, thumb_media_id)
        if not media_id:
            logger.warning("Upload news failed, attempt %d/%d", attempt + 1, retry + 1)
            if attempt < retry:
                import time
                time.sleep(5)
                # 重新获取 access_token
                access_token = _get_access_token(app_id, app_secret)
            continue

        result = _mass_send(access_token, media_id)
        if "errcode" not in result or result["errcode"] == 0:
            logger.info("WeChat push successful: %s", result)
            return {"status": "success", "data": result}
        else:
            logger.warning("Mass send failed: %s", result)
            if attempt < retry:
                import time
                time.sleep(5)

    return {"status": "failed", "reason": "all_retries_exhausted"}
