"""
微信公众号草稿发布模块。

边界说明：
- 本模块负责将当日新闻生成微信公众号草稿，供后台手动发布。
- Flask 回调和客服消息由根目录 `app.py` 负责。
- 已废弃的 SCF/旧群发逻辑不应再从主流程调用。

流程（微信官方 draft/free-publish API）：
1. 获取 access_token
2. 上传封面图到永久素材库 → thumb_media_id
3. 创建草稿（draft/add）→ draft_media_id

注意：当前主流程只创建草稿，不自动群发。
"""

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# access_token 缓存文件（Docker 容器内 /tmp 可写）
TOKEN_CACHE = "/tmp/.wx_token_cache"
TOKEN_CACHE_TS = "/tmp/.wx_token_ts"
DEFAULT_DRAFT_AUTHOR = "要闻编辑室"
DEFAULT_DRAFT_TITLE_PREFIX = "今日要闻"


def _draft_author() -> str:
    """返回公众号草稿作者署名，避免顶部展示显得像机器模板。"""
    author = os.environ.get("WECHAT_DRAFT_AUTHOR", DEFAULT_DRAFT_AUTHOR).strip()
    return author or DEFAULT_DRAFT_AUTHOR


def _format_draft_date(date_str: str) -> str:
    try:
        date_value = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return date_str
    return f"{date_value.month}月{date_value.day}日"


def _build_draft_title(date_str: str) -> str:
    prefix = os.environ.get("WECHAT_DRAFT_TITLE_PREFIX", DEFAULT_DRAFT_TITLE_PREFIX).strip()
    prefix = prefix or DEFAULT_DRAFT_TITLE_PREFIX
    return f"{prefix}｜{_format_draft_date(date_str)}"


def _build_draft_digest(news_list: list[dict], limit: int = 80) -> str:
    headlines = []
    for item in news_list[:3]:
        title = str(item.get("chinese_title") or item.get("title") or "").strip()
        if title:
            headlines.append(title)
    return "；".join(headlines)[:limit].rstrip("；，、 ")


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
) -> Optional[dict]:
    """
    上传图片到微信永久素材库。

    Returns:
        {"media_id": "...", "url": "..."} ，失败返回 None
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
            logger.info("Uploaded cover image, media_id=%s, url=%s",
                        data["media_id"], data.get("url", "")[:60])
            return {"media_id": data["media_id"], "url": data.get("url", "")}
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
            "author": _draft_author(),
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


def _fetch_og_image(article_url: str, timeout: int = 6) -> Optional[str]:
    """
    抓取文章页面的 og:image 或 twitter:image。

    只读前 100KB HTML，不下载整页。
    """
    try:
        # 完整请求头，模拟真实浏览器，绕过防盗链
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0",
        }

        resp = requests.get(
            article_url,
            timeout=timeout,
            headers=headers,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return None

        html = resp.text[:100000]

        # og:image（优先）
        m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        if not m:
            # twitter:image（备选）
            m = re.search(
                r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
                html, re.IGNORECASE,
            )
        if m:
            img_url = m.group(1)
            # 过滤掉明显太小的图标/logo
            if any(kw in img_url.lower() for kw in ["favicon", "icon-32", "logo-"]):
                return None
            return img_url
    except Exception:
        pass
    return None


def _upload_image_from_url(
    access_token: str,
    image_url: str,
    timeout: int = 10,
    max_size_mb: int = 5,
) -> Optional[str]:
    """
    下载外部图片并上传到微信素材库。

    Returns:
        微信图片 URL，失败返回 None
    """
    try:
        # 构造完整的请求头，绕过防盗链检测
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }

        # 尝试从 URL 中提取源站作为 Referer（绕过防盗链）
        from urllib.parse import urlparse
        parsed = urlparse(image_url)
        if parsed.scheme and parsed.netloc:
            headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

        resp = requests.get(image_url, timeout=timeout, headers=headers)
        resp.raise_for_status()

        content = resp.content
        if len(content) > max_size_mb * 1024 * 1024:
            logger.warning("Image too large (%d bytes), skipping", len(content))
            return None
        if len(content) < 1024:
            logger.warning("Image too small (%d bytes), likely invalid", len(content))
            return None

        # 判断文件类型
        ct = resp.headers.get("Content-Type", "").lower()
        ext = "jpg"
        if "png" in ct:
            ext = "png"
        elif "webp" in ct:
            ext = "webp"
        elif "gif" in ct:
            ext = "gif"

        upload_url = (
            "https://api.weixin.qq.com/cgi-bin/material/add_material"
            f"?access_token={access_token}&type=image"
        )
        files = {"media": (f"article.{ext}", content, f"image/{ext}")}
        upload_resp = requests.post(upload_url, files=files, timeout=30)
        data = upload_resp.json()

        if "url" in data:
            logger.info("Uploaded article image: %s", data["url"][:60])
            return data["url"]
        logger.warning("Upload article image failed: %s", data)
        return None
    except Exception as e:
        logger.warning("Failed to process image %s: %s", image_url[:60], e)
        return None


def _enrich_news_with_images(
    access_token: str,
    news_list: list[dict],
    max_workers: int = 5,
) -> list[dict]:
    """
    为每条新闻的配图下载并上传到微信素材库。

    关键规则（按用户要求）：
    1. 预览 HTML 可用外部 URL；正式草稿前必须全部上传到微信素材库
    2. 已有微信素材 URL（mmbiz.qpic.cn）：跳过
    3. 已有外部图片 URL：必须下载 → 上传微信 → 替换为微信 URL
    4. 上传成功：替换 article_image_url 为微信素材 URL
    5. 上传失败：降级为 image_type="text_only"，清空 article_image_url
    6. 不能出现"卡片按有图样式渲染，但图片被微信吞掉"的情况
    7. text_only 条目不上传占位图
    """
    def _process_one(item: dict) -> None:
        title_short = (item.get("chinese_title") or item.get("title", ""))[:40]

        # 已是 text_only：清空 URL，不尝试上传占位图
        if item.get("image_type") == "text_only":
            item["article_image_url"] = ""
            return

        existing_url = item.get("article_image_url", "")

        # 如果已经是微信素材 URL，无需再上传
        if existing_url and "mmbiz.qpic.cn" in existing_url:
            item["image_upload_status"] = "already_uploaded"
            return

        # 如果有外部图片 URL（非微信素材），必须下载并上传到微信
        # 这是关键修复：不能因为"已有 URL"就跳过上传
        if existing_url:
            wx_url = _upload_image_from_url(access_token, existing_url)
            if wx_url:
                item["article_image_url"] = wx_url
                item["image_type"] = "original"
                item["image_upload_status"] = "uploaded"
                logger.info("Re-uploaded external → wx for: %s", title_short)
            else:
                # 上传失败 → 降级，避免"有图卡片但图片被微信吞掉"
                item["image_type"] = "text_only"
                item["article_image_url"] = ""
                item["image_upload_status"] = "upload_failed"
                logger.warning(
                    "Upload failed, downgraded to text_only: %s", title_short,
                )
            return

        # 没有现有 URL，尝试从原文抓取 og:image
        url = item.get("url", "")
        source_type = item.get("source_type", "")

        # 来源天然无图 → 标记 text_only
        if not url or source_type in ("hn", "github", "huggingface", "arxiv"):
            item["image_type"] = "text_only"
            item["article_image_url"] = ""
            item["image_reason"] = (
                f"source usually text-only ({source_type or 'no-url'})"
            )
            return

        og_img = _fetch_og_image(url)
        if og_img:
            wx_url = _upload_image_from_url(access_token, og_img)
            if wx_url:
                item["article_image_url"] = wx_url
                item["image_type"] = "original"
                item["image_upload_status"] = "uploaded"
                logger.info("Enriched image for: %s", title_short)
            else:
                item["image_type"] = "text_only"
                item["article_image_url"] = ""
                item["image_upload_status"] = "upload_failed"
        else:
            item["image_type"] = "text_only"
            item["article_image_url"] = ""
            item["image_reason"] = "no og:image found"

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_process_one, item): item for item in news_list}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.warning("Image enrichment failed: %s", e)

    # 最终状态计数：只统计 image_type=="original" 且确实有微信 URL 的
    img_count = sum(
        1 for item in news_list
        if item.get("image_type") == "original" and item.get("article_image_url")
    )
    text_only_count = sum(
        1 for item in news_list
        if item.get("image_type") == "text_only" or not item.get("article_image_url")
    )
    # 记录每条最终状态
    for item in news_list:
        status = item.get("image_upload_status", "unknown")
        img_type = item.get("image_type", "?")
        img_url = item.get("article_image_url", "")
        title_short = (item.get("chinese_title") or item.get("title", ""))[:40]
        logger.info(
            "Final image state: type=%s status=%s url=%s title=%s",
            img_type, status, img_url[:50] if img_url else "(none)", title_short,
        )

    logger.info(
        "Image enrichment done: %d with WeChat images, %d text-only (total %d)",
        img_count, text_only_count, len(news_list),
    )
    return news_list


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

    # 2. 上传封面图，同时拿到 media_id（缩略图）和 url（文章内嵌）
    thumb_media_id = ""
    cover_url = ""
    if cover_path and os.path.isfile(cover_path):
        upload_result = _upload_permanent_image(access_token, cover_path)
        if upload_result:
            thumb_media_id = upload_result.get("media_id", "")
            cover_url = upload_result.get("url", "")
    if not thumb_media_id:
        logger.warning("No cover image media_id, article will have no cover")

    # 3. 为每条新闻抓取原文配图（og:image → 下载 → 上传微信，并发+降级）
    news_list = _enrich_news_with_images(access_token, news_list)

    # 4. 生成微信推文 HTML（默认确定性模板，WECHAT_USE_AI_TEMPLATE=1 启用 AI 模板）
    from src.generator import render_wechat_article, render_wechat_article_ai

    if os.environ.get("WECHAT_USE_AI_TEMPLATE", "0") == "1":
        content = render_wechat_article_ai(news_list, date_str, pages_url, cover_url)
        if not content:
            logger.info("AI HTML generation failed, falling back to deterministic template")
            content = render_wechat_article(news_list, date_str, pages_url, cover_url)
    else:
        content = render_wechat_article(news_list, date_str, pages_url, cover_url)

    # 5. 构建标题和摘要：去掉机器人/英文模板感，保留栏目式信息密度。
    title = _build_draft_title(date_str)
    digest = _build_draft_digest(news_list)

    # 6. 创建草稿（个人订阅号不支持 API 发布，需手动去后台点发布）
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
