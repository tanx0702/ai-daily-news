"""
微信公众号发布模块

流程（微信官方 free-publish API）：
1. 获取 access_token
2. 上传封面图到永久素材库 → thumb_media_id
3. 创建草稿（draft/add）→ draft_media_id
4. 发布草稿（freepublish/submit）→ 推送给所有关注者

注意：个人订阅号也支持此接口，每天限发 1 次。
"""

import json
import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# access_token 缓存文件（Docker 容器内 /tmp 可写）
TOKEN_CACHE = "/tmp/.wx_token_cache"
TOKEN_CACHE_TS = "/tmp/.wx_token_ts"


def _get_access_token(
    app_id: Optional[str] = None,
    app_secret: Optional[str] = None,
) -> Optional[str]:
    """获取微信 access_token，带文件缓存（5500 秒）。"""
    app_id = app_id or os.environ.get("WECHAT_APP_ID", "")
    app_secret = app_secret or os.environ.get("WECHAT_APP_SECRET", "")

    if not app_id or not app_secret:
        logger.warning("WECHAT_APP_ID or WECHAT_APP_SECRET not set")
        return None

    # 读取缓存
    try:
        if os.path.exists(TOKEN_CACHE_TS):
            with open(TOKEN_CACHE_TS) as f:
                cached_ts = int(f.read().strip())
            if time.time() - cached_ts < 5500:
                with open(TOKEN_CACHE) as f:
                    return f.read().strip()
    except Exception:
        pass

    url = (
        "https://api.weixin.qq.com/cgi-bin/token"
        f"?grant_type=client_credential&appid={app_id}&secret={app_secret}"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "access_token" in data:
            token = data["access_token"]
            with open(TOKEN_CACHE, "w") as f:
                f.write(token)
            with open(TOKEN_CACHE_TS, "w") as f:
                f.write(str(int(time.time())))
            logger.info("Got WeChat access_token, expires in %ds", data.get("expires_in", 0))
            return token
        logger.error("WeChat token API error: %s", data)
        return None
    except Exception as e:
        logger.error("Failed to get access_token: %s", e)
        return None


def _upload_permanent_image(
    access_token: str,
    image_path: str,
) -> Optional[str]:
    """
    上传图片到微信永久素材库。

    Returns:
        media_id，失败返回 None
    """
    url = (
        "https://api.weixin.qq.com/cgi-bin/material/add_material"
        f"?access_token={access_token}&type=image"
    )
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
        files = {"media": ("cover.jpg", image_data, "image/jpeg")}
        resp = requests.post(url, files=files, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "media_id" in data:
            logger.info("Uploaded cover image, media_id=%s", data["media_id"])
            return data["media_id"]
        logger.error("Upload image failed: %s", data)
        return None
    except FileNotFoundError:
        logger.warning("Cover image not found at %s", image_path)
        return None
    except Exception as e:
        logger.error("Upload image failed: %s", e)
        return None


def _create_draft(
    access_token: str,
    title: str,
    content: str,
    thumb_media_id: str,
    digest: str = "",
    source_url: str = "",
) -> Optional[str]:
    """
    创建微信草稿。

    API: POST /cgi-bin/draft/add

    Returns:
        草稿 media_id，失败返回 None
    """
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={access_token}"

    payload = {
        "articles": [{
            "title": title,
            "author": "AI Daily News",
            "digest": digest or title,
            "content": content,
            "content_source_url": source_url,
            "thumb_media_id": thumb_media_id,
            "need_open_comment": 0,
            "only_fans_can_comment": 0,
        }]
    }

    try:
        resp = requests.post(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if "media_id" in data:
            logger.info("Draft created, media_id=%s", data["media_id"])
            return data["media_id"]
        logger.error("Create draft failed: %s", data)
        return None
    except Exception as e:
        logger.error("Create draft failed: %s", e)
        return None


def _publish_draft(access_token: str, media_id: str) -> dict:
    """
    发布草稿，推送给所有关注者。

    API: POST /cgi-bin/freepublish/submit

    Returns:
        发布结果，成功包含 publish_id
    """
    url = f"https://api.weixin.qq.com/cgi-bin/freepublish/submit?access_token={access_token}"

    try:
        resp = requests.post(url, json={"media_id": media_id}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "errcode" not in data or data["errcode"] == 0:
            logger.info("Published successfully, publish_id=%s", data.get("publish_id"))
            return {"status": "success", "data": data}
        logger.error("Publish failed: %s", data)
        return {"status": "failed", "error": data}
    except Exception as e:
        logger.error("Publish failed: %s", e)
        return {"status": "failed", "error": str(e)}


def publish_daily_article(
    news_list: list[dict],
    date_str: str,
    pages_url: str,
    cover_path: str = "",
    retry: int = 2,
) -> dict:
    """
    发布每日 AI 新闻推文。

    Args:
        news_list: 新闻列表（已含 chinese_title 和 summary）
        date_str: 日期字符串
        pages_url: 完整日报页面 URL
        cover_path: 本地封面图路径
        retry: 失败重试次数

    Returns:
        发布结果字典
    """
    app_id = os.environ.get("WECHAT_APP_ID", "")
    app_secret = os.environ.get("WECHAT_APP_SECRET", "")

    if not app_id or not app_secret:
        logger.info("WECHAT not configured, skipping article publish")
        return {"status": "skipped", "reason": "credentials not set"}

    # 1. 获取 token
    access_token = _get_access_token(app_id, app_secret)
    if not access_token:
        return {"status": "failed", "reason": "cannot_get_access_token"}

    # 2. 上传封面图
    thumb_media_id = ""
    if cover_path and os.path.isfile(cover_path):
        thumb_media_id = _upload_permanent_image(access_token, cover_path) or ""
    if not thumb_media_id:
        logger.warning("No cover image media_id, article will have no cover")

    # 3. 生成微信推文 HTML
    from src.generator import render_wechat_article
    content = render_wechat_article(news_list, date_str, pages_url)

    # 4. 构建标题和摘要（digest 限制 ~120 字节，中文取前 40 字）
    title = f"🤖 AI 日报 {date_str}"
    highlights = news_list[:3]
    digest_parts = [
        f"{i+1}. {item.get('chinese_title') or item['title']}"
        for i, item in enumerate(highlights)
    ]
    digest = (" · ".join(digest_parts))[:40]

    # 5. 创建草稿（个人订阅号不支持 API 发布，需手动去后台点发布）
    for attempt in range(retry + 1):
        draft_media_id = _create_draft(
            access_token, title, content,
            thumb_media_id=thumb_media_id,
            digest=digest,
            source_url=pages_url,
        )
        if draft_media_id:
            logger.info("Draft ready! Go to mp.weixin.qq.com → 草稿箱 → 发布")
            return {"status": "draft_created", "media_id": draft_media_id}

        logger.warning("Create draft attempt %d/%d failed", attempt + 1, retry + 1)
        if attempt < retry:
            time.sleep(5)
            access_token = _get_access_token(app_id, app_secret)

    return {"status": "failed", "reason": "all_retries_exhausted"}
