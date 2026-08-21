# AI 圈动态与署名观点配额 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将生产日报扩展为最多 20 条，并以独立的 `fact_event`、`ai_update`、`attributed_opinion` 三类内容实现事实至少 3 条、动态目标 5/上限 8、观点目标 5/上限 8。

**Architecture:** 在 X 候选标准化阶段做确定性内容分类，在不可变简报模型中传递 `content_type`，再由分类专用发布性规则核验。选择器只把目标值用于尝试顺序，把上限值用于硬配额；`DraftDecision` 仍是唯一发布决策，并继续强制事实下限、证据绑定、唯一性和 X 上限。

**Tech Stack:** Python 3.12+、dataclasses、pytest、Flask/现有 HTML 渲染器、JSON schema v2、Docker Compose。

## Global Constraints

- `DAILY_TOP_N=20`，`DAILY_MIN_FACT_ITEMS=3`。
- `DAILY_TARGET_UPDATE_ITEMS=5`，`DAILY_MAX_UPDATE_ITEMS=8`。
- `DAILY_TARGET_OPINION_ITEMS=5`，`DAILY_MAX_OPINION_ITEMS=8`。
- `DAILY_CANDIDATE_POOL_N=60`，且必须不小于 `DAILY_TOP_N`。
- 动态和观点目标只控制尝试顺序，不成为 `DraftDecision` 的阻断条件。
- `ai_update` 不要求硬新闻动作，但必须有主体、具体技术/项目细节、规范 URL 和逐字证据绑定。
- `attributed_opinion` 仍只接受 `opinion_eligible=true` 的自然人原帖，同一作者每期最多一条。
- 广告、招聘、课程、活动报名、纯转发、纯链接/图片和无上下文短句继续拒绝。
- LLM 只能翻译和摘要原始证据，不能分类升级或新增事实。
- 保留旧 schema 读取兼容；真实 `.env`、运行时 `docs/` 和凭证不进入提交。

---

## File Map

- `src/briefing/config.py`：解析并预检总量、动态和观点目标/上限。
- `src/briefing/models.py`：让不可变模型和 `DraftDecision` 表达 `ai_update` 及其计数。
- `src/briefing/update.py`：新增 X 动态资格的确定性分类函数，不执行网络请求或 LLM 调用。
- `src/collectors/x_feed.py`：按“观点 → 硬事实 → 动态”的顺序设置候选 `content_type`。
- `src/briefing/publishability.py`：增加动态来源和展示标题的确定性发布性校验。
- `src/briefing/builder.py`、`src/briefing/validator.py`：传递类型并调用对应校验器。
- `src/briefing/selector.py`、`src/briefing/pipeline.py`、`src/briefing/decision.py`：目标感知尝试、硬上限、回填和最终计数。
- `src/briefing/adapters.py`、`src/generator.py`：输出“AI 圈动态”标签。
- `.env.advanced.example`、`AGENTS.md`、`project_docs/pipeline.md`、`project_docs/configuration.md`、`project_docs/sources.md`：同步生产契约。

### Task 1: 扩展配置和不可变数据契约

**Files:**
- Modify: `src/briefing/config.py`
- Modify: `src/briefing/models.py`
- Test: `tests/test_briefing_config.py`
- Test: `tests/test_fact_brief_contract.py`

**Interfaces:**
- Produces: `BriefingConfig.target_update_items: int`、`max_update_items: int`、`target_opinion_items: int`。
- Produces: `content_type` 允许 `fact_event|ai_update|attributed_opinion`。
- Produces: `DraftDecision.update_count`、`max_update_items`、`target_update_items`、`target_opinion_items`，并通过 `to_dict/from_dict` 往返。

- [ ] **Step 1: 写配置和序列化失败测试**

```python
def test_default_content_mix_configuration():
    config = BriefingConfig.from_env({})
    assert config.max_items == 20
    assert config.candidate_pool_size == 60
    assert (config.target_update_items, config.max_update_items) == (5, 8)
    assert (config.target_opinion_items, config.max_opinion_items) == (5, 8)


def test_content_mix_targets_cannot_exceed_caps():
    with pytest.raises(InvalidBriefingConfiguration):
        BriefingConfig.from_env({
            "DAILY_TARGET_UPDATE_ITEMS": "9",
            "DAILY_MAX_UPDATE_ITEMS": "8",
        })


def test_ai_update_contract_round_trips():
    source = source_evidence(content_type="ai_update")
    restored = SourceEvidence.from_dict(source.to_dict())
    assert restored.content_type == "ai_update"
```

- [ ] **Step 2: 运行测试并确认因缺少字段/类型失败**

Run: `python -m pytest -q tests/test_briefing_config.py tests/test_fact_brief_contract.py`

Expected: FAIL，指出默认值仍为 15/45/3、缺少动态配置字段或 `ai_update` 类型非法。

- [ ] **Step 3: 最小化扩展配置和类型集合**

```python
_CONTENT_TYPES = {"fact_event", "ai_update", "attributed_opinion"}

@dataclass(frozen=True, slots=True)
class BriefingConfig:
    max_items: int = 20
    max_opinion_items: int = 8
    target_opinion_items: int = 5
    max_update_items: int = 8
    target_update_items: int = 5
    candidate_pool_size: int = 60
```

在 `_validate()` 中明确执行：

```python
if not 5 <= self.min_items <= self.max_items <= 20:
    raise InvalidBriefingConfiguration(
        "expected 5 <= DAILY_MIN_ITEMS <= DAILY_TOP_N <= 20"
    )
if not 0 <= self.target_update_items <= self.max_update_items <= 8:
    raise InvalidBriefingConfiguration(
        "expected 0 <= DAILY_TARGET_UPDATE_ITEMS <= DAILY_MAX_UPDATE_ITEMS <= 8"
    )
if not 0 <= self.target_opinion_items <= self.max_opinion_items <= 8:
    raise InvalidBriefingConfiguration(
        "expected 0 <= DAILY_TARGET_OPINION_ITEMS <= DAILY_MAX_OPINION_ITEMS <= 8"
    )
```

所有 `SourceEvidence`、`BuiltBrief`、`BriefItem` 的类型检查复用 `_CONTENT_TYPES`；旧字典缺少 `content_type` 时继续默认 `fact_event`。`DraftDecision.from_dict()` 对新增计数字段使用 `data.get(..., 0)`，避免破坏旧 `latest.json`。

- [ ] **Step 4: 运行精确测试**

Run: `python -m pytest -q tests/test_briefing_config.py tests/test_fact_brief_contract.py`

Expected: PASS。

- [ ] **Step 5: 提交任务 1**

```powershell
git add src/briefing/config.py src/briefing/models.py tests/test_briefing_config.py tests/test_fact_brief_contract.py
git commit -m "feat(briefing): 扩展动态与观点配置契约"
```

### Task 2: 确定性分类 X 动态

**Files:**
- Create: `src/briefing/update.py`
- Modify: `src/collectors/x_feed.py`
- Test: `tests/test_ai_update_rules.py`
- Test: `tests/test_x_feed_collector.py`

**Interfaces:**
- Produces: `evaluate_ai_update_candidate(candidate: Mapping[str, object]) -> UpdateEligibility`。
- `UpdateEligibility` 字段：`eligible: bool`、`reason_codes: tuple[str, ...]`。
- Consumes: `asserted_action_types(text: str)`，有硬新闻动作的内容保持 `fact_event`。

- [ ] **Step 1: 写分类失败测试**

```python
def test_concrete_benchmark_progress_is_ai_update():
    result = evaluate_ai_update_candidate({
        "title": "Qwen3.8-27B GGUF scores 10% higher on Div-300",
        "summary": "Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark.",
    })
    assert result.eligible is True


@pytest.mark.parametrize("text", [
    "Register for our AI workshop",
    "https://t.co/example",
    "Great work!",
])
def test_promotional_link_only_and_vague_posts_are_not_updates(text):
    result = evaluate_ai_update_candidate({"title": text, "summary": text})
    assert result.eligible is False


def test_x_collector_classifies_opinion_before_update_and_release_as_fact():
    opinion = normalize_tweet(opinion_tweet(), opinion_source())
    release = normalize_tweet(release_tweet("We released Model 2.0"), official_source())
    update = normalize_tweet(update_tweet("Model 2.0 reaches #6 on the benchmark"), official_source())
    assert opinion["content_type"] == "attributed_opinion"
    assert release["content_type"] == "fact_event"
    assert update["content_type"] == "ai_update"
```

- [ ] **Step 2: 运行测试并确认模块/分类缺失**

Run: `python -m pytest -q tests/test_ai_update_rules.py tests/test_x_feed_collector.py`

Expected: FAIL，`src.briefing.update` 不存在或 X 候选仍回落为 `fact_event`。

- [ ] **Step 3: 实现最小确定性动态资格规则**

```python
@dataclass(frozen=True, slots=True)
class UpdateEligibility:
    eligible: bool
    reason_codes: tuple[str, ...]


def evaluate_ai_update_candidate(candidate: Mapping[str, object]) -> UpdateEligibility:
    text = " ".join(str(candidate.get("summary") or candidate.get("title") or "").split())
    if x_content_rejection_reason(candidate):
        return UpdateEligibility(False, ("update_promotional_or_repost",))
    visible = re.sub(r"https?://\S+", "", text).strip()
    if len(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", visible)) < 8:
        return UpdateEligibility(False, ("update_no_substantive_detail",))
    if not _has_update_anchor(visible):
        return UpdateEligibility(False, ("update_missing_concrete_anchor",))
    return UpdateEligibility(True, ())
```

`_has_update_anchor()` 仅接受可机械核验的模型/版本、百分比/排名/速度等数字，或 `benchmark|leaderboard|evaluation|experiment|framework|training|inference|GGUF|quant` 等技术对象；不引入 LLM 分类。

在 `x_feed.py` 中保持明确优先级：

```python
opinion = evaluate_opinion_candidate(candidate, registry_source)
if opinion.eligible:
    content_type = "attributed_opinion"
elif asserted_action_types(candidate["summary"]):
    content_type = "fact_event"
elif evaluate_ai_update_candidate(candidate).eligible:
    content_type = "ai_update"
else:
    content_type = "fact_event"
candidate["content_type"] = content_type
```

- [ ] **Step 4: 运行分类测试**

Run: `python -m pytest -q tests/test_ai_update_rules.py tests/test_x_feed_collector.py`

Expected: PASS。

- [ ] **Step 5: 提交任务 2**

```powershell
git add src/briefing/update.py src/collectors/x_feed.py tests/test_ai_update_rules.py tests/test_x_feed_collector.py
git commit -m "feat(x): 分类可追溯 AI 圈动态"
```

### Task 3: 增加动态专用发布性和证据校验

**Files:**
- Modify: `src/briefing/publishability.py`
- Modify: `src/briefing/builder.py`
- Modify: `src/briefing/validator.py`
- Test: `tests/test_publishability.py`
- Test: `tests/test_brief_builder.py`
- Test: `tests/test_brief_validator.py`

**Interfaces:**
- Produces: `validate_update_source_publishability(source: SourceEvidence) -> PublishabilityResult`。
- Produces: `validate_update_display_publishability(title: str, brief: str, source: SourceEvidence) -> PublishabilityResult`。
- Consumes: 现有 `claim_supported_by_quote()`、证据 URL/quote 校验和跨语言保护锚点规则。

- [ ] **Step 1: 写动态发布性失败测试**

```python
def test_ai_update_accepts_concrete_result_without_release_action():
    source = evidence(
        content_type="ai_update",
        source_title="Qwen3.8-27B GGUF scores 10% higher on Div-300",
        evidence_text="Qwen3.8-27B GGUF scores 10% higher on Div-300 benchmark.",
    )
    result = validate_update_source_publishability(source)
    assert result.accepted is True


def test_ai_update_rejects_vague_or_promotional_content():
    assert validate_update_source_publishability(
        evidence(content_type="ai_update", source_title="Interesting AI trend", evidence_text="Interesting AI trend")
    ).reason_codes == ("update_missing_concrete_detail",)


def test_validator_accepts_bound_ai_update_but_rejects_invented_result():
    accepted = validator().validate(update_event(), bound_update_draft(), generation_attempt=1, now=NOW)
    rejected = validator().validate(update_event(), invented_update_draft(), generation_attempt=1, now=NOW)
    assert accepted.action == "accept"
    assert rejected.reason_codes in {("claim_quote_mismatch",), ("update_claim_not_source_bound",)}
```

- [ ] **Step 2: 运行测试并确认动态仍走硬新闻门禁**

Run: `python -m pytest -q tests/test_publishability.py tests/test_brief_builder.py tests/test_brief_validator.py`

Expected: FAIL，缺少动态发布性函数或因没有硬新闻动作返回 `non_news_content`。

- [ ] **Step 3: 实现动态专用校验并接入 Builder/Validator**

```python
def validate_update_source_publishability(source: SourceEvidence) -> PublishabilityResult:
    title = _normalize(source.source_title)
    if _is_promotional_or_vague(title, source.evidence_text):
        return PublishabilityResult(False, ("update_missing_concrete_detail",))
    subjects = _update_subject_anchors(title)
    details = _update_detail_anchors(title)
    if not subjects:
        return PublishabilityResult(False, ("update_missing_subject",))
    if not details:
        return PublishabilityResult(False, ("update_missing_concrete_detail",))
    return PublishabilityResult(True, (), "ai_update", tuple(sorted(subjects)), "complete")
```

Validator 分流必须保持事实和动态规则互不放宽：

```python
if source.content_type == "ai_update":
    source_result = validate_update_source_publishability(source)
    display_result = validate_update_display_publishability(
        draft.chinese_title, draft.brief, source
    )
else:
    source_result = validate_source_publishability(source)
    display_result = validate_display_publishability(
        draft.chinese_title, draft.brief, source
    )
```

Builder system prompt增加一条：`content_type=ai_update` 时只能概括原始项目/模型/实验/榜单具体进展，不得改写成正式发布或确定性行业结论。类型仍从事件复制，模型响应不能覆盖。

- [ ] **Step 4: 运行发布性和生成测试**

Run: `python -m pytest -q tests/test_publishability.py tests/test_brief_builder.py tests/test_brief_validator.py`

Expected: PASS。

- [ ] **Step 5: 提交任务 3**

```powershell
git add src/briefing/publishability.py src/briefing/builder.py src/briefing/validator.py tests/test_publishability.py tests/test_brief_builder.py tests/test_brief_validator.py
git commit -m "feat(quality): 增加 AI 圈动态证据门禁"
```

### Task 4: 实现三类目标、上限和回填

**Files:**
- Modify: `src/briefing/selector.py`
- Modify: `src/briefing/pipeline.py`
- Modify: `src/briefing/decision.py`
- Test: `tests/test_brief_selector.py`
- Test: `tests/test_fact_brief_pipeline.py`
- Test: `tests/test_draft_decision.py`

**Interfaces:**
- Produces: `BriefSelector.update_count`。
- Produces: `BriefSelector.target_deficit(content_type: str) -> int`。
- Pipeline consumes category targets only for `_pop_next_event()` priority.
- `decide_draft()` enforces dynamic/opinion caps but does not block target deficits.

- [ ] **Step 1: 写选择、回填和决策失败测试**

```python
def test_selector_enforces_update_and_opinion_caps():
    selector = BriefSelector(events_for_mix(), mix_config())
    for value in eight_updates_and_eight_opinions():
        assert selector.accept(item(value)) is True
    assert selector.accept(item(ninth_update())) is False
    assert selector.accept(item(ninth_opinion())) is False


def test_pipeline_reaches_category_targets_before_quality_fill():
    result = run_brief_pipeline(
        mixed_ranked_events(), (), mix_config(), builder(), validator()
    )
    assert sum(i.content_type == "fact_event" for i in result.accepted_items) >= 3
    assert sum(i.content_type == "ai_update" for i in result.accepted_items) >= 5
    assert sum(i.content_type == "attributed_opinion" for i in result.accepted_items) >= 5
    assert len(result.accepted_items) == 20


def test_target_shortfall_does_not_block_a_valid_short_draft():
    decision = decide_draft(
        [*three_facts(), *two_updates()], mix_config()
    )
    assert decision.action == "create"
    assert "insufficient_update_items" not in decision.reasons
```

- [ ] **Step 2: 运行测试并确认缺少动态配额/目标顺序**

Run: `python -m pytest -q tests/test_brief_selector.py tests/test_fact_brief_pipeline.py tests/test_draft_decision.py`

Expected: FAIL，缺少 `update_count`、20 条上限或目标优先顺序。

- [ ] **Step 3: 最小化实现配额感知选择**

Selector增加：

```python
@property
def update_count(self) -> int:
    return sum(item.content_type == "ai_update" for item in self._accepted)

def target_deficit(self, content_type: str) -> int:
    targets = {
        "ai_update": self.config.target_update_items,
        "attributed_opinion": self.config.target_opinion_items,
    }
    current = {
        "ai_update": self.update_count,
        "attributed_opinion": self.opinion_count,
    }
    return max(targets.get(content_type, 0) - current.get(content_type, 0), 0)
```

`can_attempt()`、`limit_reason()`、`accept()` 和 `replace_accepted()` 对 `ai_update` 使用 `max_update_items`，原因码统一为 `update_limit`。

Pipeline 每次组 batch 时按以下优先级挑选：事实未到 3 → 动态/观点中目标缺口更大者 → 尚未达目标的另一类 → 原有队列顺序。目标相同则保持事件原始排序，确保确定性；达到目标后所有类别按编辑队列自然回填。

`decide_draft()` 增加 `update_count > max_update_items` 的硬阻断，并把新增计数写入 `DraftDecision`；不因 `update_count < target_update_items` 或 `opinion_count < target_opinion_items` 添加 reason。

- [ ] **Step 4: 运行选择和决策测试**

Run: `python -m pytest -q tests/test_brief_selector.py tests/test_fact_brief_pipeline.py tests/test_draft_decision.py`

Expected: PASS。

- [ ] **Step 5: 提交任务 4**

```powershell
git add src/briefing/selector.py src/briefing/pipeline.py src/briefing/decision.py tests/test_brief_selector.py tests/test_fact_brief_pipeline.py tests/test_draft_decision.py
git commit -m "feat(briefing): 按目标编排事实动态与观点"
```

### Task 5: 同步展示、环境模板和维护文档

**Files:**
- Modify: `src/briefing/adapters.py`
- Modify: `src/generator.py`
- Modify: `.env.advanced.example`
- Modify: `AGENTS.md`
- Modify: `project_docs/pipeline.md`
- Modify: `project_docs/configuration.md`
- Modify: `project_docs/sources.md`
- Test: `tests/test_generator.py`
- Test: `tests/test_environment_templates.py`
- Test: `tests/test_latest_schema_compat.py`

**Interfaces:**
- Produces display label mapping: `fact_event -> 事实简报`、`ai_update -> AI 圈动态`、`attributed_opinion -> 圈内观点`。
- Public output continues to include only canonical source links.

- [ ] **Step 1: 写展示和模板失败测试**

```python
def test_renderers_label_ai_update_and_keep_original_link():
    update = {
        **SAMPLE_NEWS[0],
        "content_type": "ai_update",
        "content_label": "AI 圈动态",
        "url": "https://x.com/example/status/42",
    }
    assert "AI 圈动态" in render_daily_html([update], date_str="2026-08-21")
    wechat = render_wechat_article([update], date_str="2026-08-21")
    assert "AI 圈动态" in wechat
    assert update["url"] in wechat


def test_advanced_template_documents_content_mix_defaults():
    values = parse_env_example(Path(".env.advanced.example"))
    assert values["DAILY_TOP_N"] == "20"
    assert values["DAILY_TARGET_UPDATE_ITEMS"] == "5"
    assert values["DAILY_MAX_UPDATE_ITEMS"] == "8"
    assert values["DAILY_TARGET_OPINION_ITEMS"] == "5"
    assert values["DAILY_MAX_OPINION_ITEMS"] == "8"
```

- [ ] **Step 2: 运行测试并确认标签/模板缺失**

Run: `python -m pytest -q tests/test_generator.py tests/test_environment_templates.py tests/test_latest_schema_compat.py`

Expected: FAIL，动态仍显示“事实简报”或高级模板缺少新变量。

- [ ] **Step 3: 实现三类标签并同步契约文档**

```python
_CONTENT_LABELS = {
    "fact_event": "事实简报",
    "ai_update": "AI 圈动态",
    "attributed_opinion": "圈内观点",
}
```

`brief_item_to_display_dict()` 和渲染器统一读取此映射，不复制第二套分类逻辑。`.env.advanced.example` 写入 20/60、动态 5/8、观点 5/8。文档同步：

- `AGENTS.md`：5-20 条、三类内容、目标是软顺序、上限是硬配额；
- `pipeline.md`：分类、目标优先、20 条停止和短版降级；
- `configuration.md`：新增变量、默认值和预检不等式；
- `sources.md`：X 在标准化阶段按观点、硬事实、动态确定性分类。

- [ ] **Step 4: 运行展示、模板和兼容测试**

Run: `python -m pytest -q tests/test_generator.py tests/test_environment_templates.py tests/test_latest_schema_compat.py`

Expected: PASS。

- [ ] **Step 5: 提交任务 5**

```powershell
git add src/briefing/adapters.py src/generator.py .env.advanced.example AGENTS.md project_docs/pipeline.md project_docs/configuration.md project_docs/sources.md tests/test_generator.py tests/test_environment_templates.py tests/test_latest_schema_compat.py
git commit -m "docs(briefing): 同步三类内容生产契约"
```

### Task 6: 完整回归与安全干跑

**Files:**
- Modify only if a regression directly caused by Tasks 1-5 is confirmed.
- Do not modify: `.env`、`docs/` runtime artifacts、credentials。

**Interfaces:**
- Verifies the production entrypoint without calling real WeChat draft API.

- [ ] **Step 1: 运行完整测试**

Run: `python -m pytest -q`

Expected: 全部 PASS；若有基线失败，记录测试名、错误和与本变更的关系，不声称全绿。

- [ ] **Step 2: 运行静态差异检查**

```powershell
git diff --check
git status --short
git diff --stat master...HEAD
```

Expected: `git diff --check` 无输出；状态中不包含 `.env`、`docs/`、日志、缓存或凭证。

- [ ] **Step 3: 运行本地安全干跑**

Run: `$env:SKIP_WECHAT_DRAFT='1'; python -m src.main`

Expected: 不调用真实微信 `draft/add`；成功时 `draft_execution.status=dry_run`，不足 5 条或事实不足时返回非零并有明确 `DraftDecision` 原因。外部来源不可用可以降级，但不得无日志终止整期。

- [ ] **Step 4: 检查生成审计但不暂存产物**

检查本次干跑日志给出的报告日期所对应的 `docs/debug/YYYY-MM-DD-briefing.json`，确认每个候选具有 `content_type`、attempts、`final_state` 和 `final_reason_codes`；确认动态/观点目标缺口只出现在 diagnostics，不进入 `DraftDecision.reasons`。

- [ ] **Step 5: 最终提交（仅在步骤 1-4 产生必要修复时）**

仅当步骤 1-4 发现并修复了本变更引入的回归时，逐个 `git add` 实际修改的源码、测试或维护文档，然后运行 `git diff --staged`，确认范围后提交：

```powershell
git commit -m "fix(briefing): 修复三类内容回归"
```

不得暂存 `.env`、`docs/`、日志、缓存或服务器私有文件。

## Plan Self-Review

- 规格覆盖：三类模型、20 条总量、事实下限、动态/观点 5/8、分类、证据门禁、选择回填、决策、展示、schema 兼容、环境模板和文档均有对应任务。
- 类型一致：所有任务统一使用 `ai_update`、`target_update_items`、`max_update_items`、`target_opinion_items`、`max_opinion_items`。
- 边界一致：目标值只控制尝试顺序；上限、事实下限、唯一性、证据绑定和 X 上限继续由确定性代码控制。
- 范围控制：不改变 X 采集认证机制、微信 API 边界、真实 `.env` 或运行时产物提交规则。
