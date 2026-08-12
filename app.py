"""
Flask 服务 — AI 日报微信回调 + 新闻 API

双路由：
  GET/POST /wechat  — 微信服务器回调（验证 + 接收消息）
  GET /health        — 健康检查
  GET /api/news      — 返回最新新闻 JSON

部署：docker compose up -d（nginx 负责 HTTPS、静态文件和 /wechat 反代）
"""

import hashlib
import hmac
import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
from flask import Flask, Response, jsonify, render_template, request
from src.briefing.adapters import brief_item_to_display_dict
from src.briefing.latest import LatestSnapshot, load_latest
from src.domain.models import FeedbackLabel
from src.services.editorial_review import list_review_runs, load_review_run
from src.services.shadow_history import record_feedback
from src.time_utils import report_date_str

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
EDITORIAL_REVIEW_USERNAME = os.environ.get("EDITORIAL_REVIEW_USERNAME", "")
EDITORIAL_REVIEW_PASSWORD = os.environ.get("EDITORIAL_REVIEW_PASSWORD", "")
SHADOW_HISTORY_DIR = Path(
    os.environ.get("SHADOW_HISTORY_DIR", "/app/docs/debug/shadow")
)


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def _editorial_review_enabled() -> bool:
    return bool(EDITORIAL_REVIEW_USERNAME and EDITORIAL_REVIEW_PASSWORD)


def _editorial_review_unauthorized() -> Response:
    return Response(
        "authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="Editorial Review", charset="UTF-8"'},
    )


def _require_editorial_review_auth(view):
    """Protect private editorial data without exposing a public URL token."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _editorial_review_enabled():
            return "not found", 404
        auth = request.authorization
        username_matches = bool(auth) and hmac.compare_digest(
            auth.username or "", EDITORIAL_REVIEW_USERNAME
        )
        password_matches = bool(auth) and hmac.compare_digest(
            auth.password or "", EDITORIAL_REVIEW_PASSWORD
        )
        if not username_matches or not password_matches:
            return _editorial_review_unauthorized()
        return view(*args, **kwargs)

    return wrapped


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
        if _env_bool("ALLOW_INSECURE_WECHAT_TOKEN", False):
            logger.warning(
                "WECHAT_TOKEN not configured; insecure signature bypass is enabled"
            )
            return True
        logger.error("WECHAT_TOKEN not configured; rejecting WeChat request")
        return False
    tmp = sorted([WECHAT_TOKEN, timestamp, nonce])
    return hashlib.sha1("".join(tmp).encode()).hexdigest() == signature


# ==================== 新闻摘要 ====================


def _load_latest_snapshot() -> LatestSnapshot | None:
    """Read the versioned latest snapshot without deriving a quality decision."""
    try:
        return load_latest(NEWS_DATA_FILE)
    except ValueError as exc:
        logger.warning("News data file not found: %s", NEWS_DATA_FILE)
        logger.debug("Latest snapshot read failure: %s", exc)
        return None


def _load_news() -> list[dict]:
    """Project v2 briefs or legacy v1 news for customer-message formatting."""
    snapshot = _load_latest_snapshot()
    if snapshot is None:
        return []
    if snapshot.schema_version == 2:
        return [brief_item_to_display_dict(item) for item in snapshot.brief_items]
    return [_thaw_mapping(item) for item in snapshot.legacy_news]


def _thaw_mapping(value):
    """Convert the immutable latest read model into a Flask JSON value."""
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(key): _thaw_mapping(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw_mapping(item) for item in value]
    return value


def _snapshot_payload(snapshot: LatestSnapshot) -> dict:
    if snapshot.schema_version == 1:
        return {
            "schema_version": 1,
            "news": [_thaw_mapping(item) for item in snapshot.legacy_news],
        }
    return {
        "schema_version": 2,
        "brief_items": [item.to_dict() for item in snapshot.brief_items],
        "draft_decision": (
            snapshot.draft_decision.to_dict() if snapshot.draft_decision else None
        ),
        "draft_execution": (
            snapshot.draft_execution.to_dict() if snapshot.draft_execution else None
        ),
        "diagnostics": _thaw_mapping(snapshot.diagnostics),
    }


def _format_summary(news_list: list[dict], full: bool = False) -> str:
    """格式化新闻摘要文本。"""
    if not news_list:
        return "暂无今日 AI 新闻，请稍后再试。"

    today = report_date_str()
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
    signature = request.args.get("signature", "")
    timestamp = request.args.get("timestamp", "")
    nonce = request.args.get("nonce", "")

    if request.method == "GET":
        # 首次配置时的 URL 验证
        echostr = request.args.get("echostr", "")

        if echostr and _verify_signature(signature, timestamp, nonce):
            return echostr
        return "signature verify failed", 403

    # POST: 接收用户消息
    if not _verify_signature(signature, timestamp, nonce):
        return "signature verify failed", 403

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
    snapshot = _load_latest_snapshot()
    decision = snapshot.draft_decision if snapshot else None
    execution = snapshot.draft_execution if snapshot else None
    callback_configured = bool(WECHAT_TOKEN)
    degraded = not callback_configured or (
        execution is not None and execution.status in {"blocked", "failed"}
    )
    return jsonify({
        "status": "degraded" if degraded else "running",
        "service": "ai-daily-news",
        "time": datetime.now(timezone.utc).isoformat(),
        "wechat_callback": {"configured": callback_configured},
        "draft_decision": decision.to_dict() if decision else None,
        "draft_execution": execution.to_dict() if execution else None,
    })


@app.route("/api/news")
def api_news():
    """返回最新新闻 JSON（供调试或外部调用）。"""
    snapshot = _load_latest_snapshot()
    if snapshot is None:
        return jsonify({"error": "no news data yet", "brief_items": []}), 404
    return jsonify(_snapshot_payload(snapshot))


@app.route("/editorial-review")
@_require_editorial_review_auth
def editorial_review_page():
    """Render saved shadow candidates for one authenticated human editor."""
    requested_run_id = request.args.get("run_id", "").strip()
    review = load_review_run(SHADOW_HISTORY_DIR, run_id=requested_run_id or None)
    return render_template(
        "editorial_review.html",
        review=review,
        runs=list_review_runs(SHADOW_HISTORY_DIR),
        labels=[label.value for label in FeedbackLabel],
    )


@app.route("/editorial-review/feedback", methods=["POST"])
@_require_editorial_review_auth
def editorial_review_feedback():
    """Append one human label to an existing shadow-run feedback history."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON object required"}), 400

    run_id = payload.get("run_id")
    candidate_id = payload.get("candidate_id")
    label = payload.get("label")
    note = payload.get("note", "")
    if not all(isinstance(value, str) for value in (run_id, candidate_id, label, note)):
        return jsonify({"error": "run_id, candidate_id, label, and note must be strings"}), 400

    try:
        event, _ = record_feedback(
            history_dir=SHADOW_HISTORY_DIR,
            run_id=run_id,
            candidate_id=candidate_id,
            label=label,
            note=note,
        )
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"event": event})


# ==================== 入口 ====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
