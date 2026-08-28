# AI 圈快讯分类型门禁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将候选统一分类为事实、AI 圈动态或署名观点，放宽有明确主体和具体进展的非数字动态，同时在内容 LLM 前终止确定性不合格候选。

**Architecture:** 在 `src/briefing/classification.py` 建立只读确定性分类器，复用 `publishability.py` 的事实和动态来源门禁；采集层先规范化证据、冻结类型并执行同类型预检，明确分类拒绝和无效证据终止，普通预检失败项降序候补。非数字动态使用受控的行为和能力词汇建立主体、细节、关系三元组，最终显示仍逐条绑定同一 quote。

**Tech Stack:** Python 3.12、dataclasses、正则表达式、现有 `SourceEvidence`/`PublishabilityResult`/`BriefPipeline`、pytest、Docker Compose。

---

## 文件结构

- Create: `src/briefing/classification.py` — 统一内容类型分类和分类原因。
- Create: `tests/test_content_classification.py` — 分类优先级和跨来源分类测试。
- Modify: `src/briefing/publishability.py` — 非数字动态的主体、能力细节和行为关系绑定。
- Modify: `src/briefing/evidence.py` — 为所有来源保留冻结后的 `content_type`。
- Modify: `src/collector.py` — 分类、明确拒绝终止、普通预检降序、聚合诊断和分类审计。
- Modify: `src/main.py` — 将分类审计仅写入私有 briefing 调试文件。
- Modify: `src/briefing/builder.py` — 提示词覆盖演示、实测、工作流和工具进展。
- Modify: `src/briefing/validator.py` — `rules_only` 跨语言动态允许受控行为和能力词。
- Modify: `tests/test_ai_update_rules.py` — X 初步动态识别兼容非数字具体进展。
- Modify: `tests/test_publishability.py` — 非数字动态来源与 claim/quote 门禁。
- Modify: `tests/test_brief_validator.py` — 非数字动态最终验证和事实动作防升级。
- Modify: `tests/test_collector.py` — 所有来源分类、明确拒绝终止、普通预检降序和 LLM 跳过诊断。
- Modify: `tests/test_main_publish_filter.py` — 私有分类审计持久化。
- Modify: `AGENTS.md`、`project_docs/pipeline.md`、`project_docs/sources.md`、`project_docs/backend.md` — 同步生产约束。

### Task 1: 扩展非数字 AI 动态证据框架

**Files:**
- Modify: `src/briefing/publishability.py`
- Modify: `src/briefing/update.py`
- Test: `tests/test_ai_update_rules.py`
- Test: `tests/test_publishability.py`

- [ ] **Step 1: 写非数字动态失败测试**

在 `tests/test_ai_update_rules.py` 增加候选级测试：

```python
def test_named_model_demo_without_metric_is_ai_update():
    result = evaluate_ai_update_candidate({
        "title": "H3 Max generates high-quality video faster than it can be watched",
        "summary": "H3 Max generates high-quality video faster than it can be watched.",
    })

    assert result.eligible is True


def test_named_tool_workflow_without_metric_is_ai_update():
    result = evaluate_ai_update_candidate({
        "title": "Claude Code supports background agents across a project workflow",
    })

    assert result.eligible is True
```

在 `tests/test_publishability.py` 增加来源和逐条绑定测试：

```python
def test_ai_update_accepts_bound_capability_demo_without_metric():
    evidence = source(
        "H3 Max generates high-quality video faster than it can be watched",
        "H3 Max generates high-quality video faster than it can be watched.",
        content_type="ai_update",
    )

    assert validate_update_source_publishability(evidence).accepted is True
    assert validate_update_display_publishability(
        "H3 Max 生成高质量视频的速度快于观看速度",
        "",
        evidence,
    ).accepted is True


def test_ai_update_rejects_swapped_capability_in_bound_quote():
    evidence = source(
        "H3 Max generates high-quality video",
        content_type="ai_update",
    )

    result = validate_update_display_publishability(
        "H3 Max 生成高质量音频",
        "",
        evidence,
    )

    assert result.reason_codes == ("update_claim_not_source_bound",)
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m pytest -q tests/test_ai_update_rules.py tests/test_publishability.py
```

Expected: 新增的无数字演示和工作流测试失败，现有数字/榜单测试继续通过。

- [ ] **Step 3: 实现受控行为和能力框架**

在 `src/briefing/publishability.py` 增加受控跨语言词汇，并让 `_update_claim_frame` 同时支持数字结果关系和非数字行为关系：

```python
_UPDATE_BEHAVIOR_PATTERNS = (
    ("demo", re.compile(r"\b(?:demonstrates?|shows?|tests?|tested)\b|展示|演示|测试", re.I)),
    ("support", re.compile(r"\b(?:supports?|enables?|allows?)\b|支持|允许|可用于", re.I)),
    ("generate", re.compile(r"\b(?:generates?|creates?)\b|生成|创建", re.I)),
    ("run", re.compile(r"\b(?:runs?|executes?)\b|运行|执行", re.I)),
    ("handle", re.compile(r"\b(?:handles?|processes?)\b|处理", re.I)),
)
_UPDATE_CAPABILITY_PATTERNS = (
    ("video", re.compile(r"\bvideos?\b|视频", re.I)),
    ("image", re.compile(r"\bimages?\b|图像|图片", re.I)),
    ("audio", re.compile(r"\baudio\b|音频", re.I)),
    ("code", re.compile(r"\bcode\b|代码", re.I)),
    ("agent", re.compile(r"\bagents?\b|智能体", re.I)),
    ("workflow", re.compile(r"\bworkflows?\b|工作流", re.I)),
    ("browser", re.compile(r"\bbrowsers?\b|浏览器", re.I)),
    ("document", re.compile(r"\bdocuments?|files?\b|文档|文件", re.I)),
)
```

实现 `_first_update_relation()`，让 `_update_subject_anchors()` 按最早的数字或行为关系切分谓词前主体；让 `_update_detail_anchors()` 返回命名对象、数字指标或能力类别；让 `_update_relation_types()` 为非数字动态返回 `behavior:<category>`。`update_claim_supported_by_quote()` 继续要求同一 quote 的主体、细节和关系全部覆盖显示声明。

`PublishabilityResult` 在末尾增加默认字段以保留兼容性：

```python
detail_anchors: tuple[str, ...] = ()
```

`validate_update_source_publishability()` 返回排序后的主体和细节锚点。同步放宽 `src/briefing/update.py` 的候选级 `_has_update_anchor()`：保留现有数字结果路径，并增加以下明确路径；推广、链接-only 和空泛内容仍先拒绝。

```python
_NAMED_UPDATE_SUBJECT = re.compile(
    r"\b(?:gpt|claude|gemini|llama|qwen|deepseek|mistral|h\d|model\s*v?\d)"
    r"[\w.+/-]*(?:\s+[A-Z][\w.+/-]*)?\b|"
    r"\b[A-Za-z][A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b",
    re.I,
)
_CONCRETE_BEHAVIOR = re.compile(
    r"\b(?:demonstrates?|shows?|tests?|supports?|enables?|allows?|generates?|"
    r"creates?|runs?|executes?|handles?|processes?)\b|"
    r"展示|演示|测试|支持|允许|生成|创建|运行|执行|处理",
    re.I,
)
_CONCRETE_CAPABILITY = re.compile(
    r"\b(?:videos?|images?|audio|code|agents?|workflows?|browsers?|documents?|files?)\b|"
    r"视频|图像|图片|音频|代码|智能体|工作流|浏览器|文档|文件",
    re.I,
)


def _has_update_anchor(text: str) -> bool:
    numeric_result = _RESULT_RELATION.search(text) and (
        (_MODEL_VERSION.search(text) and (_MECHANICAL_PROGRESS.search(text) or _BENCHMARK_RESULT.search(text)))
        or (_TECHNICAL_OBJECT.search(text) and _MECHANICAL_PROGRESS.search(text))
    )
    capability_update = (
        _NAMED_UPDATE_SUBJECT.search(text)
        and _CONCRETE_BEHAVIOR.search(text)
        and _CONCRETE_CAPABILITY.search(text)
    )
    return bool(numeric_result or capability_update)
```

- [ ] **Step 4: 运行目标测试确认通过**

Run:

```powershell
python -m pytest -q tests/test_ai_update_rules.py tests/test_publishability.py
```

Expected: 所有测试通过；数字指标的方向和维度测试没有回归。

- [ ] **Step 5: 提交动态证据框架**

```powershell
git add src/briefing/publishability.py src/briefing/update.py tests/test_ai_update_rules.py tests/test_publishability.py
git commit -m "feat(briefing): 支持非数字 AI 圈动态证据"
```

### Task 2: 增加统一确定性内容分类器

**Files:**
- Create: `src/briefing/classification.py`
- Modify: `src/briefing/evidence.py`
- Test: `tests/test_content_classification.py`
- Test: `tests/test_fact_brief_contract.py`

- [ ] **Step 1: 写分类和跨来源类型保留失败测试**

创建 `tests/test_content_classification.py`，覆盖正式事件优先、合法观点、非数字动态和拒绝：

```python
from dataclasses import replace

from src.briefing.classification import classify_source_content
from src.briefing.models import SourceEvidence


def source_evidence(**overrides):
    values = {
        "publisher_id": "example",
        "publisher_name": "Example Media",
        "channel": "rss",
        "authority": "professional_media",
        "is_official": False,
        "official_identity_source": "",
        "source_title": "Example title",
        "evidence_text": "Example evidence.",
        "url": "https://example.test/story",
        "published_at": "2026-08-28T00:00:00+00:00",
    }
    values.update(overrides)
    return SourceEvidence(**values)


def test_formal_release_stays_fact_event():
    result = classify_source_content(source_evidence(
        source_title="OpenAI releases Model 5",
        evidence_text="OpenAI releases Model 5.",
    ))
    assert result.content_type == "fact_event"


def test_professional_media_demo_becomes_ai_update():
    result = classify_source_content(source_evidence(
        source_title="H3 Max generates high-quality video",
        evidence_text="H3 Max generates high-quality video.",
        channel="rss",
    ))
    assert result.content_type == "ai_update"


def test_eligible_personal_stance_stays_attributed_opinion():
    opinion = replace(
        source_evidence(
            source_title="I think open models will win",
            evidence_text="I think open models will win because they are easier to adapt.",
            channel="x",
        ),
        content_type="attributed_opinion",
        opinion_author="Andrej Karpathy",
        opinion_eligible=True,
        original_post=True,
        context_complete=True,
    )
    assert classify_source_content(opinion).content_type == "attributed_opinion"


def test_vague_or_promotional_candidate_is_rejected():
    result = classify_source_content(source_evidence(
        source_title="Join our amazing AI workshop",
        evidence_text="Register now for our amazing AI workshop.",
    ))
    assert result.content_type is None


def test_unverified_rumor_is_rejected_before_formal_action_classification():
    result = classify_source_content(source_evidence(
        source_title="Rumor: OpenAI reportedly acquires Example AI",
        evidence_text="Sources say OpenAI reportedly acquires Example AI.",
    ))
    assert result.content_type is None
    assert result.reason_codes == ("unverified_rumor",)
```

在 `tests/test_fact_brief_contract.py` 增加非 X candidate 的 `content_type="ai_update"` 能被 `source_evidence_from_candidate()` 保留的测试。

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
python -m pytest -q tests/test_content_classification.py tests/test_fact_brief_contract.py
```

Expected: `src.briefing.classification` 尚不存在，测试失败。

- [ ] **Step 3: 实现分类器和类型保留**

创建 `src/briefing/classification.py`：

```python
from dataclasses import dataclass, replace
import re

from src.briefing.models import SourceEvidence
from src.briefing.publishability import (
    validate_content_source_publishability,
    validate_source_publishability,
    validate_update_source_publishability,
)

_FORMAL_EVENT_TYPES = {
    "release", "research", "funding", "acquisition", "partnership",
    "appointment", "departure", "organizational_change", "joining",
    "layoff", "policy", "infrastructure", "security", "open_source",
}
_UNVERIFIED_RUMOR = re.compile(
    r"\b(?:rumou?r|reportedly|sources? say|unconfirmed|leak(?:ed)?)\b|"
    r"据传|传闻|未经证实|消息人士称|爆料",
    re.I,
)


@dataclass(frozen=True, slots=True)
class ContentClassification:
    content_type: str | None
    reason_codes: tuple[str, ...]
    subject_anchors: tuple[str, ...] = ()
    detail_anchors: tuple[str, ...] = ()


def classify_source_content(source: SourceEvidence) -> ContentClassification:
    combined = f"{source.source_title} {source.evidence_text}"
    if _UNVERIFIED_RUMOR.search(combined):
        return ContentClassification(None, ("unverified_rumor",))
    fact = validate_source_publishability(replace(source, content_type="fact_event"))
    if fact.accepted and fact.event_type in _FORMAL_EVENT_TYPES:
        return ContentClassification(
            "fact_event", ("classified_fact_event",), fact.subject_anchors
        )
    if source.content_type == "attributed_opinion":
        opinion = validate_content_source_publishability(source)
        if opinion.accepted:
            return ContentClassification(
                "attributed_opinion", ("classified_attributed_opinion",)
            )
    update_source = replace(source, content_type="ai_update")
    update = validate_update_source_publishability(update_source)
    if update.accepted:
        return ContentClassification(
            "ai_update",
            ("classified_ai_update",),
            update.subject_anchors,
            update.detail_anchors,
        )
    return ContentClassification(
        None,
        update.reason_codes or fact.reason_codes or ("non_news_content",),
    )
```

在 `src/briefing/evidence.py` 将默认 `thread_values["content_type"]` 改为 `str(candidate.get("content_type") or "fact_event")`，其它观点线程字段仍只信任 X collector。

- [ ] **Step 4: 运行分类测试确认通过**

Run:

```powershell
python -m pytest -q tests/test_content_classification.py tests/test_fact_brief_contract.py
```

Expected: 所有分类和序列化测试通过。

- [ ] **Step 5: 提交分类器**

```powershell
git add src/briefing/classification.py src/briefing/evidence.py tests/test_content_classification.py tests/test_fact_brief_contract.py
git commit -m "feat(briefing): 统一分类 AI 圈快讯候选"
```

### Task 3: 在采集层终止明确分类拒绝并降低普通预检失败项顺序

**Files:**
- Modify: `src/collector.py`
- Test: `tests/test_collector.py`

- [ ] **Step 1: 写跨来源分类、终止和诊断失败测试**

在 `tests/test_collector.py` 增加一个 RSS 演示、一个正式发布和一个教程候选，调用：

```python
classification_audit = []
items = collector.collect_candidates(
    limit=10,
    hours=36,
    diagnostics=diagnostics,
    candidate_audit=classification_audit,
    now=now,
)
```

断言：

```python
assert [item["content_type"] for item in items] == ["fact_event", "ai_update"]
assert all(item["_publishability_preflight"]["accepted"] for item in items)
assert diagnostics["content_classification_counts"] == {
    "ai_update": 1,
    "fact_event": 1,
}
assert diagnostics["content_classification_rejected"] == 1
assert diagnostics["content_llm_skipped_count"] == 1
assert any(
    row["content_llm_skipped"] and row["final_state"] == "rejected"
    for row in classification_audit
)
```

调整原预检回填测试：确定性拒绝项不再补入候选池，`limit` 大于合格项数量时也只返回合格项。

- [ ] **Step 2: 运行 collector 测试确认失败**

Run:

```powershell
python -m pytest -q tests/test_collector.py
```

Expected: `candidate_audit` 参数和新诊断字段不存在，或动态仍被错误分类。

- [ ] **Step 3: 实现分类、终止和分类审计**

将 `collect_candidates()` 签名扩展为：

```python
def collect_candidates(
    config_path: str | None = None,
    hours: int | None = None,
    limit: int | None = 45,
    rss_timeout: int = 30,
    diagnostics: dict | None = None,
    candidate_audit: list[dict[str, object]] | None = None,
    now: datetime | None = None,
) -> list[dict]:
```

对每个时效和 AI 主题过滤后的 candidate：

1. 构造规范 `SourceEvidence`；
2. 调用 `classify_source_content()`；
3. 分类失败时写入 `content_classification` 审计，标记 `content_llm_skipped=True`，不加入返回池；
4. 分类成功时把 `item["content_type"]` 冻结为分类结果，重新构造证据并调用 `validate_content_source_publishability()`；
5. 无效证据和分类失败终止；其它预检失败项写入审计并排在通过项之后作为候补；
6. `publishable` 与 `preflight_rejected` 分组按现有分数排序后应用 `limit`。

分类审计记录使用有限结构：

```python
{
    "candidate_type": "content_classification",
    "candidate_id": str(item.get("id") or item.get("url") or position),
    "source_evidence": source_evidence.to_dict(),
    "original_content_type": original_content_type,
    "content_type": classification.content_type,
    "classification_reason_codes": list(classification.reason_codes),
    "classification_subject_anchors": list(classification.subject_anchors),
    "classification_detail_anchors": list(classification.detail_anchors),
    "preflight_accepted": preflight_accepted,
    "content_llm_skipped": not preflight_accepted,
    "attempts": [],
    "final_state": "eligible" if preflight_accepted else "rejected",
    "final_reason_codes": list(reason_codes),
}
```

诊断增加 `content_classification_counts`、`content_classification_rejected` 和 `content_llm_skipped_count`；保留已有来源健康、时效、AI 主题和预检原因统计。

- [ ] **Step 4: 运行 collector 测试确认通过**

Run:

```powershell
python -m pytest -q tests/test_collector.py
```

Expected: 所有 collector 测试通过，确定性拒绝候选不再出现在返回池。

- [ ] **Step 5: 提交分类拒绝与预检降序**

```powershell
git add src/collector.py tests/test_collector.py
git commit -m "fix(collector): 终止不可发布的快讯候选"
```

### Task 4: 接入私有审计、Builder 和最终动态验证

**Files:**
- Modify: `src/main.py`
- Modify: `src/briefing/builder.py`
- Modify: `src/briefing/validator.py`
- Test: `tests/test_main_publish_filter.py`
- Test: `tests/test_brief_builder.py`
- Test: `tests/test_brief_validator.py`

- [ ] **Step 1: 写审计、提示词和最终验证失败测试**

在 `tests/test_main_publish_filter.py` 的安全干跑 fixture 中捕获 `collect_candidates(candidate_audit=...)`，断言 `docs/debug/<date>-briefing.json` 的 `candidate_audit` 同时包含 `content_classification` 和 `merged_event`，而 `latest.json` 不包含候选级分类审计。

在 `tests/test_brief_builder.py` 断言 ai_update 系统提示包含“能力演示、实测观察、工作流或工具进展”，并继续禁止没有来源动作的“正式发布”。

在 `tests/test_brief_validator.py` 增加：

```python
def test_validator_accepts_source_bound_non_numeric_ai_update():
    item = event(
        content_type="ai_update",
        source_title="H3 Max generates high-quality video",
        evidence_text="H3 Max generates high-quality video.",
    )
    generated = draft(
        item,
        chinese_title="H3 Max 生成高质量视频",
        brief="",
        evidence_bindings=(
            EvidenceBinding(
                "H3 Max 生成高质量视频",
                "H3 Max generates high-quality video.",
                item.canonical_evidence.url,
            ),
        ),
        content_type="ai_update",
    )

    result = validator().validate(
        item,
        generated,
        generation_attempt=1,
        now=NOW,
    )

    assert result.action == "accept"
```

另加“生成音频”替换来源“生成视频”和把“展示”升级为“正式发布”均被拒绝的测试。

- [ ] **Step 2: 运行目标测试确认失败**

Run:

```powershell
python -m pytest -q tests/test_main_publish_filter.py tests/test_brief_builder.py tests/test_brief_validator.py
```

Expected: 分类审计未接入、提示词缺词或非数字动态被现有 claim frame 拒绝。

- [ ] **Step 3: 接入实现**

在 `src/main.py` 创建 `collection_candidate_audit: list[dict[str, object]] = []` 并传给 `collect_candidates(candidate_audit=...)`。所有 `_save_debug_report()` 调用使用：

```python
candidate_audit=(
    *collection_candidate_audit,
    *briefing.audit_entries,
)
```

公开 `latest.json` 继续只接收聚合 `diagnostics`，不接收 `collection_candidate_audit`。

在 `src/briefing/builder.py` 更新 ai_update 指令：允许来源明确支持的能力演示、实测观察、工作流和工具进展；数字和榜单不是必需；没有正式动作证据不得写成发布。

在 `src/briefing/validator.py` 的 `_UPDATE_CROSS_LANGUAGE_RULE_ONLY_MARKERS` 增加设计中受控行为和能力中文词，不允许通用评价词。`validate_update_display_publishability()` 和 `update_claim_supported_by_quote()` 仍是最终事实边界。

- [ ] **Step 4: 运行目标测试确认通过**

Run:

```powershell
python -m pytest -q tests/test_main_publish_filter.py tests/test_brief_builder.py tests/test_brief_validator.py
```

Expected: 所有目标测试通过；私有审计和公开产物边界正确。

- [ ] **Step 5: 提交流水线接入**

```powershell
git add src/main.py src/briefing/builder.py src/briefing/validator.py tests/test_main_publish_filter.py tests/test_brief_builder.py tests/test_brief_validator.py
git commit -m "feat(briefing): 接入分类型快讯流水线"
```

### Task 5: 同步约束、完整验证和服务器安全验收

**Files:**
- Modify: `AGENTS.md`
- Modify: `project_docs/pipeline.md`
- Modify: `project_docs/sources.md`
- Modify: `project_docs/backend.md`

- [ ] **Step 1: 同步生产文档**

明确写入：

- 所有来源在内容 LLM 前统一冻结类型；
- `ai_update` 允许无数字但有明确主体、具体行为/能力和同 quote 证据的动态；
- 分类明确拒绝或证据无效的候选不进入 LLM；普通预检失败只降低顺序，预检通过也不能替代最终门禁；
- `fact_event`、观点、逐条 quote、反传闻和 GitHub release 边界不变；
- 分类细节只进入私有 briefing 审计，公开产物只保留聚合诊断。

- [ ] **Step 2: 运行受影响测试**

Run:

```powershell
python -m pytest -q tests/test_ai_update_rules.py tests/test_publishability.py tests/test_content_classification.py tests/test_fact_brief_contract.py tests/test_collector.py tests/test_main_publish_filter.py tests/test_brief_builder.py tests/test_brief_validator.py tests/test_fact_brief_pipeline.py
```

Expected: 全部通过。

- [ ] **Step 3: 运行完整验证**

Run:

```powershell
python -m pytest -q
git diff --check
git status --short
```

Expected: pytest 全部通过；`git diff --check` 无错误；状态只包含本任务变更和用户原有未跟踪 X 方案文档。

- [ ] **Step 4: 提交文档**

```powershell
git add AGENTS.md project_docs/pipeline.md project_docs/sources.md project_docs/backend.md
git commit -m "docs(briefing): 同步 AI 圈快讯门禁"
```

- [ ] **Step 5: 推送并安全部署**

```powershell
git push origin master
ssh root@tankex.xyz "cd /opt/ai-news && git pull --ff-only origin master && docker compose up -d --build --force-recreate"
ssh root@tankex.xyz "cd /opt/ai-news && docker compose exec -e SKIP_WECHAT_DRAFT=1 -T web python -m src.main"
```

Expected: 服务器任务不创建真实微信草稿；容器健康；审计显示分类明确拒绝和无效证据候选未调用内容 LLM，普通预检失败项仅在通过项之后候补，并报告事实、动态、观点和 X 的实际入选数量。外部候选不足时如实报告，不以放宽证据规则硬凑。
