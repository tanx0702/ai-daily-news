# GitHub Actions X 网页采集探针 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 在 GitHub 托管 Runner 上验证 Playwright 能否从公开 X 页面捕获结构化推文 XHR 数据，不调用 X API，也不触碰日报生产链路。

**Architecture:** 探针脚本只接受公开 X URL，在 Chromium 内监听页面响应，只解析四类 Tweet 操作，抽取最小公开字段并写入脱敏报告。GitHub Actions 手动运行并上传报告 Artifact；VPS、Docker、src/main.py 与微信草稿均不参与。

**Tech Stack:** Python 3.12、Playwright 1.52.0、pytest、GitHub Actions Ubuntu Runner、Chromium。

## Global Constraints

- 不调用 X API，不读取或上传 X_BEARER_TOKEN。
- 不保存 Cookie、Authorization、请求头、请求体或完整原始 XHR 响应。
- 不修改 src/main.py、src/collector.py、Dockerfile、docker-compose.yml、VPS cron 或微信草稿流程。
- Playwright 仅列入 requirements-dev.txt，不进入 requirements.txt 或生产 Docker 镜像。
- 只允许 https://x.com、https://www.x.com、https://twitter.com、https://www.twitter.com 的页面 URL。
- 所有新增函数和网络/XHR 特殊逻辑保留简短中文注释。
- 每个任务先写失败测试，使用 python -m pytest；最终执行 python -m pytest -q。
- Git 提交信息使用中文。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| requirements-dev.txt | 仅供 GitHub 探针和本地开发的 Playwright 固定依赖 |
| scripts/x_web_probe.py | URL 校验、操作识别、JSON 推文提取、脱敏报告、浏览器入口 |
| tests/test_x_web_probe.py | 无浏览器单元测试：URL、操作、嵌套 JSON、脱敏与退出码 |
| .github/workflows/x-web-probe.yml | 手动工作流，安装 Chromium、运行探针、上传 Artifact |

### Task 1: 定义可测试的 XHR 解析与脱敏契约

**Files:**
- Create: scripts/x_web_probe.py
- Create: tests/test_x_web_probe.py

**Interfaces:**
- validate_target_url(value: str) -> str
- operation_name(url: str) -> str
- is_allowed_response_url(url: str) -> bool
- extract_tweets(payload: object) -> list[dict[str, object]]
- build_report(target_url: str, captured: list[dict[str, object]], errors: list[str]) -> dict[str, object]
- probe_exit_code(report: dict[str, object]) -> int

- [ ] **Step 1: 写入失败测试**

~~~python
import json

import pytest

from scripts.x_web_probe import (
    build_report,
    extract_tweets,
    is_allowed_response_url,
    probe_exit_code,
    validate_target_url,
)


def test_validate_target_url_only_accepts_x_public_hosts():
    assert validate_target_url("https://x.com/OpenAI") == "https://x.com/OpenAI"
    assert validate_target_url("https://www.twitter.com/OpenAI") == "https://www.twitter.com/OpenAI"
    with pytest.raises(ValueError, match="仅支持公开 X 页面"):
        validate_target_url("https://example.com/redirect")


def test_allowed_response_url_is_limited_to_tweet_operations():
    assert is_allowed_response_url("https://x.com/i/api/graphql/a/TweetResultByRestId")
    assert is_allowed_response_url("https://x.com/i/api/graphql/a/UserTweets")
    assert not is_allowed_response_url("https://x.com/i/api/graphql/a/Viewer")


def test_extract_tweets_reads_nested_payload_and_deduplicates_id():
    payload = {
        "data": {
            "tweetResult": {
                "result": {
                    "rest_id": "42",
                    "legacy": {
                        "full_text": "模型已发布",
                        "created_at": "Sun Aug 03 06:00:00 +0000 2026",
                        "favorite_count": 7,
                        "retweet_count": 3,
                        "reply_count": 2,
                        "quote_count": 1,
                    },
                    "core": {"user_results": {"result": {"legacy": {"screen_name": "OpenAI"}}}},
                }
            }
        },
        "duplicate": {"rest_id": "42", "legacy": {"full_text": "模型已发布"}},
    }

    assert extract_tweets(payload) == [{
        "tweet_id": "42",
        "text": "模型已发布",
        "author": "OpenAI",
        "created_at": "Sun Aug 03 06:00:00 +0000 2026",
        "like_count": 7,
        "repost_count": 3,
        "reply_count": 2,
        "quote_count": 1,
    }]


def test_report_contains_only_public_fields_and_empty_report_fails():
    report = build_report(
        "https://x.com/OpenAI",
        [{"operation": "TweetResultByRestId", "tweets": []}],
        ["response_json_error"],
    )
    serialized = json.dumps(report, ensure_ascii=False).lower()
    assert "authorization" not in serialized
    assert "cookie" not in serialized
    assert probe_exit_code(report) == 1
~~~

Run:

~~~powershell
python -m pytest tests/test_x_web_probe.py -q
~~~

Expected: FAIL because scripts.x_web_probe does not exist.

- [ ] **Step 2: 实现最小纯函数模块**

~~~python
from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import urlparse

ALLOWED_HOSTS = frozenset({"x.com", "www.x.com", "twitter.com", "www.twitter.com"})
ALLOWED_OPERATIONS = (
    "TweetResultByRestId",
    "UserTweets",
    "UserByScreenName",
    "SearchTimeline",
)


def validate_target_url(value: str) -> str:
    """限制探针只打开公开 X 页面，避免工作流被用作通用浏览器。"""
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("仅支持公开 X 页面 URL")
    return normalized


def operation_name(url: str) -> str:
    """从允许的 GraphQL 响应地址识别操作名。"""
    return next((name for name in ALLOWED_OPERATIONS if name in url), "")


def is_allowed_response_url(url: str) -> bool:
    """只接收包含推文结构化数据的公开 XHR 响应。"""
    return bool(operation_name(url))
~~~

实现 _walk(value: object) -> Iterator[Mapping[str, Any]] 深度遍历 dict 与 list。extract_tweets 只接收同时具有 rest_id（或 legacy.id_str）与非空 legacy.full_text 的映射，读取 core.user_results.result.legacy.screen_name，将互动指标缺失值置为 0，按 tweet_id 去重并保持首次出现顺序。

build_report 只输出 schema_version、target_url、captured_operations、tweet_count、tweets、errors；不得把输入 payload 直接写入返回值。probe_exit_code 在 tweet_count 大于等于 1 时返回 0，否则返回 1。

- [ ] **Step 3: 验证并提交**

~~~powershell
python -m pytest tests/test_x_web_probe.py -q
git add scripts/x_web_probe.py tests/test_x_web_probe.py
git commit -m "feat: 增加 X 网页探针解析契约"
~~~

Expected: 4 passed before commit.

### Task 2: 添加 Playwright 浏览器探针与失败产物

**Files:**
- Create: requirements-dev.txt
- Modify: scripts/x_web_probe.py
- Modify: tests/test_x_web_probe.py

**Interfaces:**
- write_report(report: dict[str, object], output_dir: Path) -> Path
- run_probe(target_url: str, output_dir: Path, timeout_ms: int = 45_000) -> int
- CLI: python scripts/x_web_probe.py --target-url URL --output-dir PATH

- [ ] **Step 1: 扩展失败测试**

~~~python
from pathlib import Path

from scripts.x_web_probe import write_report


def test_write_report_creates_only_probe_report_json(tmp_path: Path):
    path = write_report({"schema_version": "x-web-probe-v1", "tweet_count": 0}, tmp_path)

    assert path == tmp_path / "probe-report.json"
    assert json.loads(path.read_text(encoding="utf-8"))["tweet_count"] == 0
    assert [item.name for item in tmp_path.iterdir()] == ["probe-report.json"]
~~~

Run:

~~~powershell
python -m pytest tests/test_x_web_probe.py -q
~~~

Expected: FAIL because write_report does not exist.

- [ ] **Step 2: 添加开发依赖与浏览器执行入口**

Create requirements-dev.txt:

~~~text
playwright==1.52.0
~~~

在 scripts/x_web_probe.py 延迟导入 from playwright.sync_api import sync_playwright，使 Task 1 的纯函数测试不依赖 Chromium。write_report 创建 output_dir，以 UTF-8、ensure_ascii=False、indent=2 写入唯一的 probe-report.json。

~~~python
def run_probe(target_url: str, output_dir: Path, timeout_ms: int = 45_000) -> int:
    """在隔离浏览器中捕获公开 X 页面响应，并写入脱敏诊断。"""
    captured: list[dict[str, object]] = []
    errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def capture(response) -> None:
            # 只解析已允许的 Tweet XHR，避免收集会话或无关页面数据。
            operation = operation_name(response.url)
            if not operation:
                return
            try:
                tweets = extract_tweets(response.json())
            except Exception as exc:
                errors.append(f"{operation}:json_error:{type(exc).__name__}")
                return
            captured.append({"operation": operation, "tweets": tweets})

        page.on("response", capture)
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(8_000)
        except Exception as exc:
            errors.append(f"page_error:{type(exc).__name__}")
        finally:
            report = build_report(target_url, captured, errors)
            write_report(report, output_dir)
            if probe_exit_code(report):
                page.screenshot(path=str(output_dir / "failure.png"), full_page=True)
            browser.close()
    return probe_exit_code(report)
~~~

main() 使用 argparse 提供 --target-url 和 --output-dir，先调用 validate_target_url。URL 不合法时输出中文错误并返回 2；其他结果返回 run_probe 的退出码。不得输出响应内容、浏览器存储、Cookie 或 Header。

- [ ] **Step 3: 验证浏览器探针边界并提交**

~~~powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests/test_x_web_probe.py -q
python scripts/x_web_probe.py --target-url https://example.com --output-dir .tmp/x-web-probe
~~~

Expected: 单元测试通过；最后一条命令输出中文 URL 校验错误并以退出码 2 结束；.tmp/x-web-probe 不产生报告。

~~~powershell
git add requirements-dev.txt scripts/x_web_probe.py tests/test_x_web_probe.py
git commit -m "feat: 增加 Playwright X 网页探针"
~~~

### Task 3: 创建手动 GitHub Actions 探针工作流

**Files:**
- Create: .github/workflows/x-web-probe.yml
- Modify: tests/test_x_web_probe.py

**Interfaces:**
- Actions 名称: X Web Probe
- 触发器: 仅 workflow_dispatch
- 输入: target_url，默认值 https://x.com/OpenAI
- Artifact 名称: x-web-probe

- [ ] **Step 1: 写入工作流约束测试**

~~~python
from pathlib import Path


def test_workflow_is_manual_and_does_not_reference_vps_or_secrets():
    workflow = Path(".github/workflows/x-web-probe.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "secrets." not in workflow
    assert "ssh " not in workflow.lower()
    assert "scp " not in workflow.lower()
    assert "actions/upload-artifact@v4" in workflow
    assert "if: always()" in workflow
~~~

Run:

~~~powershell
python -m pytest tests/test_x_web_probe.py::test_workflow_is_manual_and_does_not_reference_vps_or_secrets -q
~~~

Expected: FAIL because the workflow file does not exist.

- [ ] **Step 2: 创建工作流**

~~~yaml
name: X Web Probe

on:
  workflow_dispatch:
    inputs:
      target_url:
        description: Public X profile, status, or search URL
        required: true
        default: https://x.com/OpenAI
        type: string

permissions:
  contents: read

jobs:
  probe:
    runs-on: ubuntu-latest
    timeout-minutes: 12
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install probe dependencies
        run: python -m pip install -r requirements-dev.txt
      - name: Install Chromium
        run: python -m playwright install --with-deps chromium
      - name: Run X web probe
        run: >-
          python scripts/x_web_probe.py
          --target-url "${{ inputs.target_url }}"
          --output-dir artifacts/x-web-probe
      - name: Upload probe artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: x-web-probe
          path: artifacts/x-web-probe
          if-no-files-found: warn
~~~

工作流中不得加入 secrets、SSH、SCP、VPS 地址、cron 或任何生产部署命令。

- [ ] **Step 3: 验证工作流约束、完整测试集并提交**

~~~powershell
python -m pytest tests/test_x_web_probe.py -q
python -m pytest -q
git diff --check
git add .github/workflows/x-web-probe.yml tests/test_x_web_probe.py
git commit -m "ci: 增加 X 网页采集探针工作流"
~~~

Expected: 探针测试和完整测试集通过，git diff --check 无输出。

### Task 4: 在 GitHub 执行并判读真实探针

**Files:**
- No repository file changes.

**Interfaces:**
- Artifact: x-web-probe/probe-report.json
- 失败截图: x-web-probe/failure.png，仅探针失败时存在。

- [ ] **Step 1: 推送分支并手动运行工作流**

推送 codex/x-web-probe，在 GitHub Actions 选择该分支，使用默认 https://x.com/OpenAI 触发 X Web Probe。不设置 Secret，不执行 VPS 命令。

- [ ] **Step 2: 判读 Artifact**

通过报告示例：

~~~json
{
  "schema_version": "x-web-probe-v1",
  "tweet_count": 1,
  "captured_operations": ["UserTweets"]
}
~~~

tweet_count 可以大于 1；每条 tweets 只含 tweet_id、text、author、created_at、like_count、repost_count、reply_count、quote_count。

- [ ] **Step 3: 判读失败并保持生产隔离**

若 Actions 任务非零，审阅 probe-report.json 的 errors 与 failure.png。该结果只决定后续是否设计浏览器会话管理；不修改 RSS、日报、微信草稿或 VPS cron。若报告包含 Cookie、Authorization、Header、Token 或原始 XHR，则探针不合格，先修复脱敏边界。

## 计划自检

- 规格中的 GitHub Runner、允许操作、公开 URL、脱敏 Artifact、失败截图、手动触发、生产隔离和回滚边界由 Task 1 至 Task 4 覆盖。
- 计划包含精确文件、接口、测试代码、命令和通过条件。
- 定时采集、登录态管理与 VPS 候选导入不在本计划范围内；必须在探针通过后单独设计。

