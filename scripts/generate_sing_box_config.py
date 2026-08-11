"""Generate a private sing-box configuration from one VLESS WebSocket + TLS URI."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


class ProxyConfigError(ValueError):
    """Raised when a node URI cannot be safely converted into the supported config."""


SUPPORTED_FINGERPRINTS = frozenset(
    {"chrome", "firefox", "safari", "ios", "android", "edge", "360", "qq", "random", "randomized"}
)


def _query_value(query: dict[str, list[str]], name: str, default: str = "") -> str:
    values = query.get(name, [])
    return values[-1].strip() if values else default


def build_proxy_config(node_uri: str) -> dict[str, Any]:
    """Return a sing-box config for exactly one VLESS WebSocket + TLS node."""
    parsed = urlsplit(node_uri.strip())
    if parsed.scheme.lower() != "vless" or not parsed.hostname or not parsed.username:
        raise ProxyConfigError("节点必须是包含主机和 UUID 的 vless:// 链接")

    try:
        node_uuid = str(uuid.UUID(parsed.username))
    except ValueError as exc:
        raise ProxyConfigError("VLESS 节点 UUID 无效") from exc

    try:
        server_port = parsed.port
    except ValueError as exc:
        raise ProxyConfigError("VLESS 节点端口无效") from exc
    if server_port is None:
        server_port = 443
    if not 1 <= server_port <= 65535:
        raise ProxyConfigError("VLESS 节点端口无效")

    query = parse_qs(parsed.query, keep_blank_values=True)
    transport_type = _query_value(query, "type", "tcp").lower()
    security = _query_value(query, "security", "none").lower()
    encryption = _query_value(query, "encryption", "none").lower()
    allow_insecure = _query_value(query, "allowInsecure", "0").lower()
    if transport_type != "ws" or security != "tls":
        raise ProxyConfigError("当前仅支持 VLESS WebSocket + TLS 节点")
    if encryption not in ("", "none"):
        raise ProxyConfigError("VLESS 节点 encryption 必须为 none")
    if allow_insecure in {"1", "true", "yes"}:
        raise ProxyConfigError("不允许跳过 TLS 证书验证")

    server_name = _query_value(query, "sni") or parsed.hostname
    host_header = _query_value(query, "host") or server_name
    path = _query_value(query, "path", "/") or "/"
    if not path.startswith("/"):
        path = f"/{path}"

    fingerprint = (_query_value(query, "fp", "chrome") or "chrome").lower()
    if fingerprint not in SUPPORTED_FINGERPRINTS:
        raise ProxyConfigError("TLS fingerprint 不受支持")

    tls: dict[str, Any] = {
        "enabled": True,
        "server_name": server_name,
        "insecure": False,
        "utls": {
            "enabled": True,
            "fingerprint": fingerprint,
        },
    }
    alpn = [entry.strip() for entry in _query_value(query, "alpn").split(",") if entry.strip()]
    if alpn:
        tls["alpn"] = alpn

    return {
        "log": {"level": "warn", "timestamp": True},
        "inbounds": [
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": "0.0.0.0",
                "listen_port": 7890,
            }
        ],
        "outbounds": [
            {
                "type": "vless",
                "tag": "proxy",
                "server": parsed.hostname,
                "server_port": server_port,
                "uuid": node_uuid,
                "tls": tls,
                "transport": {
                    "type": "ws",
                    "path": path,
                    "headers": {"Host": host_header},
                },
            }
        ],
        "route": {"final": "proxy"},
    }


def write_proxy_config(node_file: Path, output_file: Path) -> None:
    """Read one private URI and atomically write a root-only JSON config."""
    try:
        node_uri = node_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ProxyConfigError("无法读取 VLESS 节点文件") from exc
    if not node_uri:
        raise ProxyConfigError("VLESS 节点文件为空")

    config = build_proxy_config(node_uri)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{output_file.name}.", dir=output_file.parent, text=True
    )
    try:
        os.chmod(temporary_path, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_path, output_file)
    except OSError:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a sing-box config from one VLESS WebSocket + TLS node")
    parser.add_argument("node_file", type=Path, help="private file containing one vless:// URI")
    parser.add_argument("output_file", type=Path, help="private sing-box JSON output path")
    arguments = parser.parse_args()
    try:
        write_proxy_config(arguments.node_file, arguments.output_file)
    except ProxyConfigError as exc:
        parser.error(str(exc))
    except OSError as exc:
        parser.error(f"无法写入 sing-box 配置: {exc.strerror or 'I/O error'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
