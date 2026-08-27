# 内容 LLM 逐条请求 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将生产内容 LLM 从每次最多 5 条改为每次固定 1 条，并在服务器安全干跑验证真实效果。

**Architecture:** 保留现有 `BriefingConfig -> fact brief pipeline -> BriefBuilder` 数据流，只把内部固定批大小从 5 改为 1。Builder 的确定性证据恢复、两次生成预算和本期熔断状态保持不变；不新增环境变量或并发机制。

**Tech Stack:** Python 3.12、pytest、OpenAI 兼容内容 LLM、Docker Compose、Git/SSH

---

### Task 1: 以失败测试定义逐条请求契约

**Files:**
- Modify: `tests/test_briefing_config.py`
- Modify: `tests/test_brief_builder.py`

- [ ] **Step 1: 修改配置默认值断言**

将 `test_briefing_config_uses_approved_defaults` 中的批大小断言改为：

```python
assert config.builder_batch_size == 1
```

- [ ] **Step 2: 修改 Builder 请求粒度测试**

把原有最多五条批处理测试替换为六条输入、六个单条响应：

```python
def test_builder_sends_one_event_per_request():
    events = [event(index) for index in range(1, 7)]
    responses = [
        {
            "items": [
                generated_item(
                    1,
                    item.event_key,
                    item.canonical_evidence.url,
                )
            ]
        }
        for item in events
    ]
    builder, client = builder_with_responses(responses)

    results = builder.build_batch(events, attempts={})

    assert len(results) == 6
    assert all(result.draft is not None for result in results)
    assert len(client.chat.completions.calls) == 6
    request_sizes = [
        len(json.loads(call["messages"][1]["content"])["events"])
        for call in client.chat.completions.calls
    ]
    assert request_sizes == [1, 1, 1, 1, 1, 1]
```

- [ ] **Step 3: 运行测试并确认 RED**

Run:

```powershell
python -m pytest -q tests\test_briefing_config.py::test_briefing_config_uses_approved_defaults tests\test_brief_builder.py::test_builder_sends_one_event_per_request
```

Expected: 两个测试都因当前 `builder_batch_size == 5` 而失败；不是导入、语法或 fixture 错误。

### Task 2: 最小实现与文档同步

**Files:**
- Modify: `src/briefing/config.py`
- Modify: `AGENTS.md`
- Modify: `project_docs/pipeline.md`

- [ ] **Step 1: 修改固定批大小**

把数据类默认值和 `from_env()` 固定赋值都改为 `1`：

```python
builder_batch_size: int = 1
```

```python
builder_batch_size=1,
```

- [ ] **Step 2: 运行精确测试并确认 GREEN**

Run:

```powershell
python -m pytest -q tests\test_briefing_config.py::test_briefing_config_uses_approved_defaults tests\test_brief_builder.py::test_builder_sends_one_event_per_request
```

Expected: `2 passed`。

- [ ] **Step 3: 同步生产约束文档**

在 `AGENTS.md` 和 `project_docs/pipeline.md` 明确：所有内容 LLM 初次生成和重建均为单条请求；429、5xx 或网关不可用仍立即打开本期熔断，后续候选不继续撞击供应商。删除“其它来源保持有界批量”的过时描述。

- [ ] **Step 4: 运行受影响测试**

Run:

```powershell
python -m pytest -q tests\test_briefing_config.py tests\test_brief_builder.py tests\test_fact_brief_pipeline.py
```

Expected: 全部通过。

### Task 3: 完整验证并提交唯一发布基线

**Files:**
- Verify all modified files

- [ ] **Step 1: 运行完整测试和仓库检查**

Run:

```powershell
python -m pytest -q
git diff --check
git status --short
```

Expected: pytest 零失败，`git diff --check` 零错误；`git status` 只包含本任务文件和用户原有未跟踪文件 `project_docs/plans/2026-08-18-authenticated-x-snapshot.md`。

- [ ] **Step 2: 检查暂存内容并提交**

Run:

```powershell
git add -- src/briefing/config.py tests/test_briefing_config.py tests/test_brief_builder.py AGENTS.md project_docs/pipeline.md project_docs/plans/2026-08-27-single-item-content-llm-implementation.md
git diff --staged --check
git diff --staged
git commit -m "fix(briefing): 内容 LLM 改为逐条请求"
```

Expected: 暂存区不包含 `.env`、运行产物、密钥或用户原有未跟踪文档；提交成功。

- [ ] **Step 3: 推送 `master`**

Run:

```powershell
git push origin master
```

Expected: `origin/master` 指向本次实现提交。

### Task 4: 服务器部署与安全干跑

**Files:**
- Server checkout: `/opt/ai-news`
- Runtime output: `/opt/ai-news/docs/latest.json`

- [ ] **Step 1: 拉取并重建服务**

Run:

```powershell
ssh root@tankex.xyz "cd /opt/ai-news && git pull --ff-only origin master && docker compose up -d --force-recreate && docker compose ps"
```

Expected: 服务器工作副本更新到实现提交，`web` 和 `nginx` 健康运行；不读取或输出 `.env` 密钥。

- [ ] **Step 2: 执行唯一安全干跑命令**

Run:

```powershell
ssh root@tankex.xyz "cd /opt/ai-news && docker compose exec -e SKIP_WECHAT_DRAFT=1 -T web python -m src.main"
```

Expected: 不调用真实微信草稿 API。若 `DraftDecision=create`，执行状态为 `dry_run` 且进程返回 0；若内容门禁仍 block，则保留非零结果并从诊断中报告真实原因，不绕过测试。

- [ ] **Step 3: 检查部署版本、服务和脱敏诊断摘要**

Run:

```powershell
ssh root@tankex.xyz "cd /opt/ai-news && git rev-parse --short HEAD && docker compose ps && python -c 'import json; d=json.load(open(\"docs/latest.json\", encoding=\"utf-8\")); print({\"brief_count\": len(d.get(\"brief_items\", [])), \"decision\": d.get(\"draft_decision\"), \"execution\": d.get(\"draft_execution\"), \"briefing\": d.get(\"diagnostics\", {}).get(\"briefing\", {})})'"
```

Expected: 版本匹配 `origin/master`；输出仅含条数、决策、执行与内容生成计数，不含凭证或完整第三方响应。
