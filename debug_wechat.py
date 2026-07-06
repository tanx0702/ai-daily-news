#!/usr/bin/env python3
"""
微信回调验证诊断脚本

用法：
    docker compose exec web python debug_wechat.py
"""

import hashlib
import os
import sys
from dotenv import load_dotenv

load_dotenv()

token = os.environ.get("WECHAT_TOKEN", "")
app_id = os.environ.get("WECHAT_APP_ID", "")

print("=" * 60)
print("微信回调验证诊断")
print("=" * 60)

# 1. 检查配置
print("\n[1] 环境变量检查:")
print(f"    WECHAT_TOKEN  = '{token}' (长度={len(token)})")
print(f"    WECHAT_APP_ID = '{app_id}'")
if not token:
    print("    ❌ WECHAT_TOKEN 未设置！")
    sys.exit(1)
if len(token) < 3:
    print("    ⚠️  Token 太短，建议至少 10 个字符")
if not app_id:
    print("    ❌ WECHAT_APP_ID 未设置！")
    sys.exit(1)
print("    ✅ 配置完整")

# 2. 检查最新的 latest.json
print("\n[2] 新闻数据检查:")
try:
    import json
    with open("/app/docs/latest.json", "r") as f:
        data = json.load(f)
    news_count = len(data.get("news", []))
    print(f"    latest.json 存在，包含 {news_count} 条新闻")
except FileNotFoundError:
    print("    ⚠️  latest.json 不存在（需要先跑 python -m src.main）")
except Exception as e:
    print(f"    ❌ 读取失败: {e}")

# 3. 测试签名验证逻辑
print("\n[3] 签名算法验证（模拟微信）:")
timestamp = "1783307466"
nonce = "1468020557"
echostr = "VERIFY_OK_12345"

tmp = sorted([token, timestamp, nonce])
sorted_str = "".join(tmp)
signature = hashlib.sha1(sorted_str.encode()).hexdigest()

print(f"    排序后: {sorted_str}")
print(f"    SHA1:   {signature}")

# 用自己的逻辑验证
verify_ok = hashlib.sha1("".join(
    sorted([token, timestamp, nonce])
).encode()).hexdigest() == signature
print(f"    验证结果: {'✅ 通过' if verify_ok else '❌ 失败'}")

# 4. 直接用 Flask 的 _verify_signature 测试
print("\n[4] Flask _verify_signature 函数测试:")
from flask import Flask, request

app_test = Flask("test")
with app_test.test_request_context(
    f"/?signature={signature}&timestamp={timestamp}&nonce={nonce}&echostr={echostr}"
):
    from app import _verify_signature
    ok = _verify_signature(
        request.args["signature"],
        request.args["timestamp"],
        request.args["nonce"],
    )
    print(f"    测试签名: {signature}")
    print(f"    echostr:   {echostr}")
    print(f"    _verify_signature: {'✅ 返回 True' if ok else '❌ 返回 False'}")
    if ok:
        print(f"    应返回 echostr → {echostr}")
    else:
        print("    ❌ 签名验证失败！")

# 5. 打印 nginx 最近的 wechat 请求
print("\n[5] 提示：运行以下命令查看微信发来的实际请求:")
print('    docker compose logs nginx | grep "GET.*wechat" | tail -5')
print('    docker compose logs web   | grep "POST.*wechat" | tail -5')

# 6. IP 白名单检查
print("\n[6] IP 白名单提醒:")
print("    确认已在微信开发者平台添加: 8.163.131.99")
print("    具体路径: 基础信息 → 开发密钥 → IP 白名单")

print("\n" + "=" * 60)
print("诊断完成。找到上面标 ❌ 的项进行修复。")
print("=" * 60)
