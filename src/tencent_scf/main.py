"""
腾讯云 SCF 函数入口

双路由设计：
  - ?source=github  : 接收 GitHub Actions 推送的新闻 JSON，存入 COS
  - ?source=wechat  : 接收微信服务器 XML 回调，解析关键词，推送客服消息

环境变量（SCF 控制台配置）：
  WECHAT_APP_ID         - 微信公众号 AppID
  WECHAT_APP_SECRET     - 微信公众号 AppSecret
  COS_BUCKET            - COS 存储桶名称，格式: xxx-1234567890
  COS_REGION            - COS 地域，如 ap-guangzhou
  COS_SECRET_ID         - COS 密钥 ID
  COS_SECRET_KEY        - COS 密钥 Key
  NEWS_OBJECT_KEY       - COS 中存储新闻数据的键，默认 daily_news/latest.json

依赖（requirements.txt）：
  tencentcloud-scf-common
  qcloud-cos-sdk-v5
  requests>=2.28.0
"""

import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import parse_qs

import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ==================== 常量 ====================

NEWS_OBJECT_KEY = os.environ.get("NEWS_OBJECT_KEY", "daily_news/latest.json")
COS_UPLOAD_EXPIRY = 3600  # COS 预签名 URL 有效期（秒）

# ==================== 微信 API ====================


def _get_access_token() -> str | None:
    """获取微信 access_token，带简单缓存。"""
    cache_key = "_wx_access_token"
    cache_ts_key = "_wx_access_token_ts"

    cache_path = "/tmp/.wx_token_cache"
    ts_path = "/tmp/.wx_token_ts"

    # 读取缓存（5500 秒有效期，access_token 有效期 7200 秒）
    try:
        if os.path.exists(ts_path):
            with open(ts_path, "r") as f:
                ts = int(f.read().strip())
            if datetime.now(timezone.utc).timestamp() - ts < 5500:
                with open(cache_path, "r") as f:
                    return f.read().strip()
    except Exception:
        pass

    app_id = os.environ.get("WECHAT_APP_ID", "")
    app_secret = os.environ.get("WECHAT_APP_SECRET", "")
    if not app_id or not app_secret:
        logger.error("WECHAT_APP_ID or WECHAT_APP_SECRET not configured")
        return None

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
            expires_in = data.get("expires_in", 7200)
            # 写入缓存
            with open(cache_path, "w") as f:
                f.write(token)
            with open(ts_path, "w") as f:
                f.write(str(int(datetime.now(timezone.utc).timestamp())))
            logger.info("Got new WeChat access_token (expires in %ds)", expires_in)
            return token
        logger.error("WeChat token API error: %s", data)
        return None
    except Exception as e:
        logger.error("Failed to get WeChat access_token: %s", e)
        return None


def _send_customer_service_message(
    access_token: str,
    openid: str,
    text: str,
) -> dict:
    """发送微信客服消息（文本）。"""
    url = (
        "https://api.weixin.qq.com/cgi-bin/message/custom/send"
        f"?access_token={access_token}"
    )
    payload = {
        "touser": openid,
        "msgtype": "text",
        "text": {"content": text},
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("errcode", -1) == 0:
            logger.info("Customer service message sent to %s", openid)
        else:
            logger.error("Failed to send customer service message: %s", result)
        return result
    except Exception as e:
        logger.error("Failed to send customer service message: %s", e)
        return {"errcode": -1, "errmsg": str(e)}


def _format_news_summary(news_list: list[dict]) -> str:
    """将新闻列表格式化为客服消息文本（精简版，适合微信消息长度限制）。"""
    if not news_list:
        return "暂无今日 AI 新闻。"

    lines = [f"\U0001f916 AI 日报 {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"]
    lines.append(f"共 {len(news_list)} 条新闻\n")

    for i, item in enumerate(news_list[:10], 1):
        title = item.get("chinese_title") or item.get("title", "")
        lines.append(f"{i}. {title}")

    if len(news_list) > 10:
        lines.append(f"\n... 还有 {len(news_list) - 10} 条新闻，请回复「完整」查看完整版日报。")
    else:
        lines.append("\n回复「完整」查看完整版日报。")

    return "\n".join(lines)


# ==================== COS 操作 ====================


def _upload_to_cos(data: dict) -> bool:
    """
    将数据上传到 COS。

    使用 COS SDK v5 或直接通过预签名 URL 上传。
    这里采用预签名 URL 方案，减少依赖。
    """
    bucket = os.environ.get("COS_BUCKET", "")
    region = os.environ.get("COS_REGION", "")
    secret_id = os.environ.get("COS_SECRET_ID", "")
    secret_key = os.environ.get("COS_SECRET_KEY", "")
    object_key = NEWS_OBJECT_KEY

    if not all([bucket, region, secret_id, secret_key]):
        logger.error("COS credentials not fully configured")
        return False

    # 使用 cos-python-sdk-v5
    try:
        from qcloud_cos import CosConfig, CosS3Client

        config = CosConfig(
            Region=region,
            SecretId=secret_id,
            SecretKey=secret_key,
        )
        client = CosS3Client(config)

        # 确保目录存在
        client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )
        logger.info("News data uploaded to COS: %s/%s", bucket, object_key)
        return True
    except ImportError:
        logger.error("qcloud_cos package not installed, cannot upload to COS")
        return False
    except Exception as e:
        logger.error("Failed to upload to COS: %s", e)
        return False


def _download_from_cos() -> list[dict]:
    """从 COS 下载最新新闻数据。"""
    bucket = os.environ.get("COS_BUCKET", "")
    region = os.environ.get("COS_REGION", "")
    secret_id = os.environ.get("COS_SECRET_ID", "")
    secret_key = os.environ.get("COS_SECRET_KEY", "")
    object_key = NEWS_OBJECT_KEY

    if not all([bucket, region, secret_id, secret_key]):
        logger.error("COS credentials not fully configured")
        return []

    try:
        from qcloud_cos import CosConfig, CosS3Client

        config = CosConfig(
            Region=region,
            SecretId=secret_id,
            SecretKey=secret_key,
        )
        client = CosS3Client(config)

        resp = client.get_object(Bucket=bucket, Key=object_key)
        body = resp["Body"].get_raw_stream().read()
        data = json.loads(body.decode("utf-8"))

        news_list = data.get("news", [])
        logger.info("Downloaded %d news items from COS", len(news_list))
        return news_list
    except Exception as e:
        logger.error("Failed to download from COS: %s", e)
        return []


# ==================== GitHub 路由 ====================


def handle_github_post(body: bytes) -> dict:
    """
    处理 GitHub Actions 推送的新闻数据。

    期望 JSON body:
    {
        "date": "2026-07-02",
        "news": [...],
        "html_url": "https://...",
        "cover_image_url": "..."
    }
    """
    try:
        data = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON from GitHub: %s", e)
        return {"status": "error", "message": "invalid JSON"}

    if "news" not in data:
        logger.error("Missing 'news' field in GitHub payload")
        return {"status": "error", "message": "missing 'news' field"}

    # 上传到 COS
    success = _upload_to_cos(data)
    if success:
        return {"status": "ok", "message": "news stored to COS"}
    return {"status": "error", "message": "failed to store to COS"}


# ==================== 微信路由 ====================


def _parse_wechat_xml(xml_body: bytes) -> dict:
    """
    解析微信 XML 回调体。

    返回解析后的字段字典。
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_body.decode("utf-8"))
        result = {child.tag: child.text for child in root}
        return result
    except Exception as e:
        logger.error("Failed to parse WeChat XML: %s", e)
        return {}


def _verify_wechat_signature(
    signature: str,
    timestamp: str,
    nonce: str,
    echostr: str,
) -> bool:
    """
    验证微信签名（仅用于首次配置回调 URL 时的验证）。

    正常消息推送不需要此步骤，但保留以兼容。
    """
    token = os.environ.get("WECHAT_TOKEN", "")
    if not token:
        # 未配置 token，跳过验证
        return True

    import hashlib

    tmp_list = sorted([token, timestamp, nonce])
    tmp_str = "".join(tmp_list)
    hash_str = hashlib.sha1(tmp_str.encode("utf-8")).hexdigest()
    return hash_str == signature


def handle_wechat_callback(
    xml_body: bytes,
    query_params: dict,
) -> tuple[int, str]:
    """
    处理微信 XML 回调。

    返回 (HTTP状态码, 响应体)。

    支持的关键词：
      - "日报" : 推送今日新闻摘要
      - "完整" : 推送完整新闻列表（含链接）
    """
    signature = query_params.get("signature", [""])[0]
    timestamp = query_params.get("timestamp", [""])[0]
    nonce = query_params.get("nonce", [""])[0]
    echostr = query_params.get("echostr", [""])[0]

    # 首次配置回调 URL 时的验证请求
    if echostr:
        ok = _verify_wechat_signature(signature, timestamp, nonce, echostr)
        if ok:
            return (200, echostr)
        return (403, "signature verify failed")

    # 解析 XML
    parsed = _parse_wechat_xml(xml_body)
    if not parsed:
        return (200, "<xml></xml>")

    msg_type = parsed.get("MsgType", "")
    content = parsed.get("Content", "").strip()
    from_user = parsed.get("FromUserName", "")

    if not from_user:
        return (200, "<xml></xml>")

    # 只响应文本消息
    if msg_type != "text":
        return (
            200,
            _build_wechat_xml(
                from_user, "暂无该类型消息，请发送「日报」获取今日 AI 新闻。"
            ),
        )

    access_token = _get_access_token()
    if not access_token:
        return (
            200,
            _build_wechat_xml(
                from_user, "系统繁忙，请稍后再试。"
            ),
        )

    if content == "日报":
        news_list = _download_from_cos()
        summary = _format_news_summary(news_list)
        _send_customer_service_message(access_token, from_user, summary)
        return (
            200,
            _build_wechat_xml(from_user, "已发送今日新闻摘要，请查收。"),
        )

    if content == "完整":
        news_list = _download_from_cos()
        summary = _format_full_summary(news_list)
        _send_customer_service_message(access_token, from_user, summary)
        return (
            200,
            _build_wechat_xml(from_user, "已发送完整新闻列表，请查收。"),
        )

    # 未知关键词
    help_text = (
        "欢迎使用 AI 日报机器人！\n\n"
        "发送「日报」获取今日 AI 新闻摘要\n"
        "发送「完整」获取完整新闻列表\n\n"
        "每日 8:00 自动推送最新资讯。"
    )
    _send_customer_service_message(access_token, from_user, help_text)
    return (
        200,
        _build_wechat_xml(from_user, help_text),
    )


def _build_wechat_xml(from_user: str, content: str) -> str:
    """构建微信回复 XML。"""
    now = int(datetime.now(timezone.utc).timestamp())
    return f"""<xml>
<ToUserName><![CDATA[{from_user}]]></ToUserName>
<FromUserName><![CDATA[mp_ai_daily_news]]></FromUserName>
<CreateTime>{now}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{content}]]></Content>
</xml>"""


def _format_full_summary(news_list: list[dict]) -> str:
    """格式化完整新闻摘要（含来源和链接）。"""
    if not news_list:
        return "暂无今日 AI 新闻。"

    lines = [f"\U0001f916 AI 日报完整列表 {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"]
    lines.append(f"共 {len(news_list)} 条新闻\n")

    for i, item in enumerate(news_list[:20], 1):
        title = item.get("chinese_title") or item.get("title", "")
        source = item.get("source", "")
        url = item.get("url", "")
        summary = item.get("summary", "")

        lines.append(f"{i}. {title}")
        if source:
            lines.append(f"   来源: {source}")
        if summary:
            lines.append(f"   {summary[:80]}...")
        if url:
            lines.append(f"   {url}")
        lines.append("")

    if len(news_list) > 20:
        lines.append(f"... 还有 {len(news_list) - 20} 条新闻")

    return "\n".join(lines)


# ==================== SCF 入口 ====================


def main_handler(event: dict, context: dict) -> dict:
    """
    腾讯云 SCF HTTP 触发器入口。

    event 结构（HTTP 触发器）：
    {
        "version": "2.0",
        "route": "$default",
        "method": "POST",
        "headers": {...},
        "queryString": {...},
        "body": "...",
        "isBase64Encoded": false
    }
    """
    # SCF HTTP 触发器 v2.0 协议
    headers = event.get("headers", {})
    query_string = event.get("queryString", {})
    body = event.get("body", "")
    is_base64 = event.get("isBase64Encoded", False)
    method = event.get("method", "GET").upper()

    # 解码 body
    if is_base64 and body:
        import base64
        body_bytes = base64.b64decode(body)
    elif body:
        body_bytes = body.encode("utf-8")
    else:
        body_bytes = b""

    # 路由判断：优先使用 query string 参数
    source = query_string.get("source", [None])[0] if query_string else None

    try:
        if source == "github" and method == "POST":
            result = handle_github_post(body_bytes)
            status_code = 200 if result.get("status") == "ok" else 400
            return {
                "statusCode": status_code,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(result, ensure_ascii=False),
            }

        elif source == "wechat" and method in ("GET", "POST"):
            status_code, xml_response = handle_wechat_callback(
                body_bytes, query_string or {}
            )
            return {
                "statusCode": status_code,
                "headers": {"Content-Type": "application/xml"},
                "body": xml_response,
            }

        else:
            # 健康检查 / 未知路由
            if method == "GET" and not source:
                return {
                    "statusCode": 200,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({
                        "service": "ai-daily-news-scf",
                        "status": "running",
                        "routes": {
                            "github": "POST ?source=github (JSON body)",
                            "wechat": "GET/POST ?source=wechat (XML body)",
                        },
                    }, ensure_ascii=False),
                }
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({
                    "error": "unknown route",
                    "hint": "use ?source=github or ?source=wechat",
                }, ensure_ascii=False),
            }

    except Exception as e:
        logger.exception("Unhandled error in SCF handler")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "error": "internal_server_error",
                "message": str(e),
            }, ensure_ascii=False),
        }
