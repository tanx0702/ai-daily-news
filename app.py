"""
Flask 服务 — AI 日报微信回调 + 新闻 API

双路由：
  GET/POST /wechat  — 微信服务器回调（验证 + 接收消息）
  GET /health        — 健康检查
  GET /api/news      — 返回最新新闻 JSON

部署：docker compose up -d（Caddy 自动 HTTPS，反代到本服务 :5000）
"""

import hashlib
import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from functools import wraps

import requests
from dotenv import load_dotenv

load_dotenv()
from flask import Flask, jsonify, request

# ==================== 配置 ====================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("app")

app = Flask(__name__)

WECHAT_APP_ID = os.environ.get("WECHAT_APP_ID", "")
WECHAT_APP_SECRET = os.environ.get("WECHAT_APP_SECRET", "")
WECHAT_TOKEN = os.environ.get("WECHAT_TOKEN", "")
NEWS_DATA_FILE = os.environ.get("NEWS_DATA_FILE", "/app/docs/latest.json")

# ==================== 微信 API ====================

# 内存缓存 access_token（简单实现，单进程够用）
_token_cache: dict = {}


def _get_access_token() -> str | None:
    """获取微信 access_token，带内存缓存（7200 秒有效期，5500 秒刷新）。"""
    now = datetime.now(timezone.utc).timestamp()
    if _token_cache.get("token") and (now - _token_cache.get("ts", 0)) < 5500:
        return _token_cache["token"]

    if not WECHAT_APP_ID or not WECHAT_APP_SECRET:
        logger.error("WECHAT_APP_ID or WECHAT_APP_SECRET not configured")
        return None

    url = (
        "https://api.weixin.qq.com/cgi-bin/token"
        f"?grant_type=client_credential&appid={WECHAT_APP_ID}&secret={WECHAT_APP_SECRET}"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "access_token" in data:
            _token_cache["token"] = data["access_token"]
            _token_cache["ts"] = now
            logger.info("Got new WeChat access_token (expires in %ds)", data.get("expires_in", 0))
            return _token_cache["token"]
        logger.error("WeChat token API error: %s", data)
        return None
    except Exception as e:
        logger.error("Failed to get WeChat access_token: %s", e)
        return None


def _send_customer_message(openid: str, text: str) -> bool:
    """发送微信客服消息（文本）。"""
    token = _get_access_token()
    if not token:
        return False

    url = f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={token}"
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
            logger.info("Customer message sent to %s", openid)
            return True
        logger.error("Failed to send customer message: %s", result)
        return False
    except Exception as e:
        logger.error("Failed to send customer message: %s", e)
        return False


# ==================== 微信 XML ====================


def _parse_wechat_xml(xml_body: bytes) -> dict:
    """解析微信回调 XML。"""
    try:
        root = ET.fromstring(xml_body.decode("utf-8"))
        return {child.tag: child.text for child in root}
    except Exception as e:
        logger.error("Failed to parse WeChat XML: %s", e)
        return {}


def _build_xml_reply(to_user: str, content: str) -> str:
    """构建微信回复 XML。"""
    now = int(datetime.now(timezone.utc).timestamp())
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        "<FromUserName><![CDATA[ai_daily_news]]></FromUserName>"
        f"<CreateTime>{now}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        "</xml>"
    )


def _verify_signature(signature: str, timestamp: str, nonce: str) -> bool:
    """验证微信签名。"""
    if not WECHAT_TOKEN:
        logger.warning("WECHAT_TOKEN not configured, skipping signature verification")
        return True
    tmp = sorted([WECHAT_TOKEN, timestamp, nonce])
    return hashlib.sha1("".join(tmp).encode()).hexdigest() == signature


# ==================== 新闻摘要 ====================


def _load_news() -> list[dict]:
    """从 latest.json 加载最新新闻数据。"""
    try:
        with open(NEWS_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("news", [])
    except FileNotFoundError:
        logger.warning("News data file not found: %s", NEWS_DATA_FILE)
        return []
    except Exception as e:
        logger.error("Failed to load news data: %s", e)
        return []


def _format_summary(news_list: list[dict], full: bool = False) -> str:
    """格式化新闻摘要文本。"""
    if not news_list:
        return "暂无今日 AI 新闻，请稍后再试。"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"今日AI要闻 · {today}", f"共 {len(news_list)} 条精选\n"]

    limit = min(len(news_list), 20) if full else min(len(news_list), 10)
    for i, item in enumerate(news_list[:limit], 1):
        title = item.get("chinese_title") or item.get("title", "")
        lines.append(f"{i}. {title}")
        if full:
            source = item.get("source", "")
            url = item.get("url", "")
            summary = item.get("summary", "")
            if source:
                lines.append(f"   来源: {source}")
            if summary:
                lines.append(f"   {summary[:80]}")
            if url:
                lines.append(f"   {url}")
            lines.append("")

    if len(news_list) > limit:
        lines.append(f"\n... 还有 {len(news_list) - limit} 条新闻")

    pages_url = os.environ.get("PAGES_URL", "https://tankex.xyz")
    lines.append(f"\n完整内容: {pages_url}")
    return "\n".join(lines)


# ==================== 路由 ====================


@app.route("/wechat", methods=["GET", "POST"])
def wechat_callback():
    """微信服务器回调。"""
    if request.method == "GET":
        # 首次配置时的 URL 验证
        signature = request.args.get("signature", "")
        timestamp = request.args.get("timestamp", "")
        nonce = request.args.get("nonce", "")
        echostr = request.args.get("echostr", "")

        if echostr and _verify_signature(signature, timestamp, nonce):
            return echostr
        return "signature verify failed", 403

    # POST: 接收用户消息
    xml_body = request.data
    parsed = _parse_wechat_xml(xml_body)
    if not parsed:
        return _build_xml_reply("", "")

    msg_type = parsed.get("MsgType", "")
    content = parsed.get("Content", "").strip()
    from_user = parsed.get("FromUserName", "")

    if not from_user:
        return _build_xml_reply("", "")

    # 只处理文本消息
    if msg_type != "text":
        _send_customer_message(
            from_user,
            "暂不支持该类型消息。\n请发送「日报」获取今日 AI 新闻。",
        )
        return _build_xml_reply(from_user, "")

    # 关键词路由
    news_list = _load_news()

    if content == "日报":
        summary = _format_summary(news_list, full=False)
        _send_customer_message(from_user, summary)
        return _build_xml_reply(from_user, "已发送今日新闻摘要，请查收。")

    if content == "完整":
        summary = _format_summary(news_list, full=True)
        _send_customer_message(from_user, summary)
        return _build_xml_reply(from_user, "已发送完整新闻列表，请查收。")

    # 默认回复
    help_text = (
        "欢迎阅读今日AI要闻。\n\n"
        "发送「日报」获取今日 AI 新闻摘要\n"
        "发送「完整」获取完整新闻列表\n\n"
        "每天 8:00 自动更新。"
    )
    _send_customer_message(from_user, help_text)
    return _build_xml_reply(from_user, help_text)


@app.route("/health")
def health():
    """健康检查端点。"""
    return jsonify({
        "status": "running",
        "service": "ai-daily-news",
        "time": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/news")
def api_news():
    """返回最新新闻 JSON（供调试或外部调用）。"""
    try:
        with open(NEWS_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return jsonify({"error": "no news data yet", "news": []}), 404
    return jsonify(data)


# ==================== 入口 ====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
