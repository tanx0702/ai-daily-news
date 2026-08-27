# X 内容分类与发布性分派修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复 X 注册发布者身份丢失、官方发布和 AI 观点误分类，以及候选预检与最终 Validator 发布性规则漂移，同时继续拒绝体育、政治、直播和推广内容。

**Architecture:** 保留现有确定性分类顺序和最终事实门禁，只在可信 X 采集边界恢复注册来源名称、补充少量高置信表达，并在 `publishability.py` 建立唯一的内容类型分派入口。候选预检和最终 Validator 共用该入口；LLM 不参与类型判断，未知内容继续保守拒绝。

**Tech Stack:** Python 3.12、pytest、现有 `src.collectors.x_feed` / `src.briefing` / `src.collector` 流水线、Docker Compose。

---

## 任务 1：恢复可信 X 注册发布者身份

**文件：**

- 修改：`tests/test_x_feed_collector.py`
- 修改：`tests/test_source_normalization.py`
- 修改：`src/collectors/x_feed.py`
- 修改：`src/briefing/evidence.py`

### 步骤 1：写注册发布者 round-trip 失败测试

在 `tests/test_x_feed_collector.py` 的正常注册来源采集测试中断言候选含有：

```python
assert items[0]["x_source_name"] == "OpenAI"
```

并断言转换后的证据保留发布者名称：

```python
evidence = source_evidence_from_candidate(items[0], trusted_x_collector=True)
assert evidence is not None
assert evidence.publisher_name == "OpenAI"
```

在 `tests/test_source_normalization.py` 增加安全边界：同一候选即使带 `x_source_name="Fake Publisher"`，当 `trusted_x_collector=False` 时，`publisher_name` 仍为通用域名名称 `X`。

### 步骤 2：运行目标测试，确认失败

运行：

```powershell
python -m pytest -q tests/test_x_feed_collector.py tests/test_source_normalization.py
```

预期：注册来源未写入 `x_source_name` 或证据发布者仍为 `X`，新增断言失败。

### 步骤 3：实现最小可信身份传递

在 `src/collectors/x_feed.py::_tweet_to_candidate()` 中，仅当 `registry_source` 命中时写入注册表名称：

```python
candidate["x_source_name"] = source_name if registry_source else ""
```

在 `src/briefing/evidence.py::source_evidence_from_candidate()` 的 X 分支中，只在可信采集器且字段非空时覆盖通用名称：

```python
trusted_source_name = str(candidate.get("x_source_name") or "").strip()
if trusted_x_collector and trusted_source_name:
    source_name = trusted_source_name
```

不改变 handle、官方账号映射、authority 或 publisher ID 的现有校验逻辑。

### 步骤 4：重新运行目标测试

运行：

```powershell
python -m pytest -q tests/test_x_feed_collector.py tests/test_source_normalization.py
```

预期：全部通过。

## 任务 2：修复官方发布和白名单 AI 观点分类

**文件：**

- 修改：`tests/test_publishability.py`
- 修改：`tests/test_opinion_rules.py`
- 修改：`tests/test_x_feed_collector.py`
- 修改：`src/briefing/publishability.py`
- 修改：`src/briefing/opinion.py`

### 步骤 1：写官方发布表达失败测试

扩展 `tests/test_publishability.py::test_source_publishability_recognizes_common_factual_news_verbs`，加入代表性标题：

```python
"Qwen3.8-Flash API is live on QwenCloud"
"OpenAI is releasing a technical report"
```

断言两者都通过 `validate_source_publishability()`。

### 步骤 2：写 AI 观点和非 AI 反例失败测试

在 `tests/test_opinion_rules.py` 增加：

- 白名单人物的 `Not every AI task benefits from more compute...` 被识别为观点；
- 白名单人物的 `人类对 AI Agents 的监督能力需要跟上...` 被识别为观点；
- 同一白名单人物的网球或政治长观点返回 `opinion_no_ai_topic`；
- 仅包含 AI 主题但没有立场的直播预告仍返回 `opinion_no_substantive_claim`。

在 `tests/test_x_feed_collector.py` 用短文本验证分类顺序：官方 `is live` / `releasing` 为 `fact_event`，AI 观点为 `attributed_opinion`，非 AI 观点仍不会升级为 `attributed_opinion`。

### 步骤 3：运行目标测试，确认失败

运行：

```powershell
python -m pytest -q tests/test_publishability.py tests/test_opinion_rules.py tests/test_x_feed_collector.py
```

预期：新增官方表达返回 `non_news_content`，新增观点返回 `opinion_no_substantive_claim`，测试失败。

### 步骤 4：补充最小确定性词表和 AI 主题前提

在 `src/briefing/publishability.py::EVENT_ACTION_MARKERS["release"]` 只加入：

```python
"is live", "goes live", "went live", "releasing"
```

在 `src/briefing/opinion.py`：

- 增加确定性 `_AI_TOPIC` 正则，覆盖 `AI`、人工智能、机器学习、深度学习、LLM、大模型、模型、智能体、agent(s) 及已知模型/机构名称；
- 在推广、转发、缺上下文和 link-only 检查之后，立场判断之前检查 AI 主题；缺失时返回 `opinion_no_ai_topic`；
- 仅把 `not every` 和中文 `需要` 加入高置信立场词。

不新增宽泛的 `should` / `must`，避免普通政治和生活观点被升级。

### 步骤 5：重新运行目标测试

运行：

```powershell
python -m pytest -q tests/test_publishability.py tests/test_opinion_rules.py tests/test_x_feed_collector.py
```

预期：全部通过，正例进入正确内容类型，反例继续拒绝。

## 任务 3：统一来源发布性分派

**文件：**

- 修改：`tests/test_publishability.py`
- 修改：`tests/test_collector.py`
- 修改：`tests/test_brief_validator.py`
- 修改：`src/briefing/publishability.py`
- 修改：`src/collector.py`
- 修改：`src/briefing/validator.py`

### 步骤 1：写三类内容分派失败测试

在 `tests/test_publishability.py` 增加 `validate_content_source_publishability()` 测试：

- `fact_event` 仍走硬新闻门禁；
- 具有模型主体和机械细节、但没有硬新闻动作的 `ai_update` 走动态门禁并通过；
- 完整的 `attributed_opinion` 元数据通过；
- 缺作者、非原帖、上下文不全或 `opinion_eligible=False` 的观点返回 `opinion_author_not_allowed`。

### 步骤 2：写候选预检和最终 Validator 集成测试

在 `tests/test_collector.py` 增加小型候选集合，验证合法 `ai_update` 和 `attributed_opinion` 的 `_publishability_preflight.accepted` 为 `True`，不再被硬新闻规则提前拒绝。

在 `tests/test_brief_validator.py` 增加或扩展观点测试，验证来源元数据不完整时由共享来源分派拒绝，完整来源仍继续进入现有作者归因和 quote 显示门禁。

### 步骤 3：运行目标测试，确认失败

运行：

```powershell
python -m pytest -q tests/test_publishability.py tests/test_collector.py tests/test_brief_validator.py
```

预期：新入口不存在，或合法动态/观点在候选预检中被硬新闻门禁拒绝。

### 步骤 4：实现共享分派并替换两个调用点

在 `src/briefing/publishability.py` 增加：

```python
def validate_content_source_publishability(
    source: SourceEvidence,
) -> PublishabilityResult:
    if source.content_type == "ai_update":
        return validate_update_source_publishability(source)
    if source.content_type == "attributed_opinion":
        if not (
            source.opinion_eligible
            and source.original_post
            and source.context_complete
            and source.opinion_author.strip()
        ):
            return PublishabilityResult(False, ("opinion_author_not_allowed",))
        return PublishabilityResult(True, (), "attributed_opinion")
    return validate_source_publishability(source)
```

在 `src/collector.py::collect_candidates()` 中用该入口替换固定的 `validate_source_publishability()`。

在 `src/briefing/validator.py::BriefValidator.validate()` 中先调用共享入口；通过后，观点继续调用现有 `_opinion_contract_reasons()`，动态/事实继续执行现有显示发布性、quote、URL 和质量规则。删除本次变更产生的未使用导入，不重构相邻代码。

### 步骤 5：重新运行目标测试

运行：

```powershell
python -m pytest -q tests/test_publishability.py tests/test_collector.py tests/test_brief_validator.py
```

预期：全部通过，候选预检和最终 Validator 对来源发布性采用同一分派结果。

## 任务 4：同步约束文档并完成本地验证

**文件：**

- 修改：`project_docs/sources.md`
- 修改：`project_docs/pipeline.md`
- 修改：`AGENTS.md`

### 步骤 1：更新文档

记录以下稳定约束：

- 注册 X 来源名称只能从可信采集器与注册表传入；
- 白名单观点还必须有确定性 AI 主题和实质立场；
- 候选预检与最终 Validator 共用按 `content_type` 的来源发布性分派；
- 官方发布词补充不放宽最终主体、细节和 quote 绑定门禁。

### 步骤 2：运行受影响测试

运行：

```powershell
python -m pytest -q tests/test_x_feed_collector.py tests/test_source_normalization.py tests/test_opinion_rules.py tests/test_publishability.py tests/test_collector.py tests/test_brief_validator.py
```

预期：全部通过。

### 步骤 3：运行完整验证

运行：

```powershell
python -m pytest -q
git diff --check
git status --short
```

预期：完整测试通过，diff 无空白错误；状态只包含本次修改和用户已有的未跟踪 `project_docs/plans/2026-08-18-authenticated-x-snapshot.md`。

### 步骤 4：检查暂存内容并提交

运行：

```powershell
git add AGENTS.md project_docs/sources.md project_docs/pipeline.md src/collectors/x_feed.py src/briefing/evidence.py src/briefing/opinion.py src/briefing/publishability.py src/collector.py src/briefing/validator.py tests/test_x_feed_collector.py tests/test_source_normalization.py tests/test_opinion_rules.py tests/test_publishability.py tests/test_collector.py tests/test_brief_validator.py
git diff --staged --check
git diff --staged --stat
git commit -m "fix(x): 修复内容分类与发布性分派"
```

不暂存或修改用户已有的未跟踪计划文件。

## 任务 5：推送 master、部署并安全干跑

**文件：**

- 服务器工作树：`/opt/ai-news`
- 运行产物：服务器 `docs/debug/<date>-briefing.json`、`docs/latest.json` 和容器日志（仅诊断，不提交）

### 步骤 1：推送唯一发布基线

运行：

```powershell
git push origin master
```

预期：`origin/master` 快进到修复提交。

### 步骤 2：服务器快进拉取并重建

通过已配置的 SSH 连接在 `/opt/ai-news` 运行只读状态检查，确认没有会被覆盖的服务器本地改动后执行：

```bash
git pull --ff-only origin master
docker compose up -d --force-recreate
```

预期：服务器 HEAD 与 `origin/master` 一致，服务容器健康；不读取或输出 `.env` 密钥。

### 步骤 3：执行安全干跑

在服务器容器中以 `SKIP_WECHAT_DRAFT=1` 运行生产入口，保留真实来源、LLM 和全部门禁，但不调用真实微信草稿 API。

预期检查：

- X 注册来源的 `publisher_name` 不再统一为 `X`；
- `is live` / `releasing` 官方样本进入事实尝试；
- 带 AI 主题和实质立场的白名单观点进入观点尝试；
- 体育、政治、直播和推广内容继续拒绝；
- 汇总内容 LLM 成功/失败/熔断计数、X 各类型与拒绝原因、最终 5–20 条数量及 `DraftDecision`；
- 若仍 `block`，按真实原因报告，不放宽门禁、不调用微信草稿。

### 步骤 4：记录最终证据

最终报告列出：本地实际测试命令与结果、提交与推送状态、服务器部署 HEAD、干跑耗时、X 分类变化、最终条数/类型和未解决问题。
