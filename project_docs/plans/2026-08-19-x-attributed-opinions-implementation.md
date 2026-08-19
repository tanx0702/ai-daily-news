# X 署名观点与来源扩容 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有日报流水线中加入可审计的 X 署名观点内容类型，并将 X 每期硬上限扩展到 8 条，同时保持至少 3 条事实新闻、最多 3 条观点和 DraftDecision 的确定性门禁。

**Architecture:** 在现有 `SourceEvidence -> MergedEvent -> BriefItem -> renderer` 链路上增加内容类型和观点元数据，不新建第二条发布流水线。X 快照继续由 Runner/服务器生成；配置决定观点资格，采集器保留线程关系；独立的观点预检/构建/校验规则与事实规则共享证据绑定、事件去重、选择器、审计和发布出口。选择器先满足事实下限，再按排名填充观点，最终决策统一检查总量、事实下限、观点上限、X 上限和作者上限。

**Tech Stack:** Python 3.12+, dataclasses, pytest, JSON 配置，现有 OpenAI-compatible LLM、Jinja/HTML 渲染和 Docker/cron 运行链路。

## Global Constraints

- 生产日报只能展示 5-15 条唯一内容；其中至少 3 条 `fact_event`，最多 3 条 `attributed_opinion`。
- `DAILY_X_TARGET_ITEMS=5` 只控制候选尝试顺序；`DAILY_X_MAX_ITEMS=8` 是硬上限，不能靠低质量条目凑数。
- 同一 X 作者每期最多 1 条；观点必须来自配置中 `opinion_eligible=true` 的自然人原帖。
- 观点标题必须保留作者归因；不得把观点改写成机构公告或客观事实。
- 所有标题和摘要句必须绑定规范来源原文证据；观点使用本人 X 原帖作为规范来源。
- X 快照失效、上下文不完整、LLM 失败或观点规则不确定时跳过该观点，不影响其它来源；事实不足 3 条或总量不足 5 条时 block。
- 不提交 `.env`、密钥、运行产物、完整外部响应和服务器私有文件；保留现有未跟踪用户文件不变。

---

### Task 1: 扩展配置 schema 和白名单资格

**Files:**
- Modify: `src/briefing/config.py:BriefingConfig` 配置字段、环境解析和校验
- Modify: `config/x_sources.json` 为自然人增加 `opinion_eligible`，补充经过设计文档核验的候选账号
- Modify: `.env.advanced.example` X 配置说明
- Modify: `project_docs/configuration.md`、`project_docs/sources.md`、`project_docs/architecture.md`
- Test: `tests/test_briefing_config.py`, `tests/test_x_web_feed.py`, `tests/test_environment_templates.py`

**Interfaces:**
- Produces `BriefingConfig.max_opinion_items == 3`, `BriefingConfig.min_fact_items == 3`, `BriefingConfig.max_x_items == 8`, `BriefingConfig.target_x_items == 5`.
- Produces registry records with boolean `opinion_eligible`; old records default to `False` when omitted.
- `tests/test_x_web_feed.py` adds `load_x_sources(Path) -> list[dict[str, object]]` coverage using the production loader already imported by that module.

- [ ] **Step 1: Write the failing tests**

```python
def test_opinion_and_x_limits_use_new_defaults():
    config = BriefingConfig.from_env({})
    assert config.min_fact_items == 3
    assert config.max_opinion_items == 3
    assert config.max_x_items == 8
    assert config.target_x_items == 5


def test_x_limits_reject_opinion_or_x_values_outside_contract():
    with pytest.raises(InvalidBriefingConfiguration):
        BriefingConfig.from_env({"DAILY_MAX_OPINION_ITEMS": "4"})
    with pytest.raises(InvalidBriefingConfiguration):
        BriefingConfig.from_env({"DAILY_X_MAX_ITEMS": "9"})


def test_person_sources_expose_explicit_opinion_eligibility():
    sources = load_x_sources(Path("config/x_sources.json"))
    karpathy = next(source for source in sources if source["handle"] == "karpathy")
    openai = next(source for source in sources if source["handle"] == "OpenAI")
    assert karpathy["opinion_eligible"] is True
    assert openai["opinion_eligible"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_briefing_config.py::test_opinion_and_x_limits_use_new_defaults tests/test_briefing_config.py::test_x_limits_reject_opinion_or_x_values_outside_contract tests/test_x_web_feed.py::test_person_sources_expose_explicit_opinion_eligibility -q`

Expected: FAIL because the config has no opinion fields, X defaults are still 3/5, and the registry has no explicit opinion flag.

- [ ] **Step 3: Implement the minimal configuration and registry changes**

Add fields and parsing to `BriefingConfig`:

```python
min_fact_items: int = 3
max_opinion_items: int = 3
max_x_items: int = 8
target_x_items: int = 5
```

Read `DAILY_MIN_FACT_ITEMS` (default `3`) and `DAILY_MAX_OPINION_ITEMS` (default `3`), validate `0 <= max_opinion_items <= max_items`, `3 <= min_fact_items <= min_items`, `0 <= max_x_items <= 8`, and `0 <= target_x_items <= max_x_items`. Set `opinion_eligible: true` only on the existing 18 research people and the individually verified new natural-person accounts; leave institution and media accounts false. Update the two environment/documentation tables with the exact new defaults and constraints.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_briefing_config.py tests/test_x_web_feed.py tests/test_environment_templates.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/briefing/config.py config/x_sources.json .env.advanced.example project_docs/configuration.md project_docs/sources.md project_docs/architecture.md tests/test_briefing_config.py tests/test_x_web_feed.py tests/test_environment_templates.py
git commit -m "feat: 扩展 X 观点来源配置"
```

### Task 2: 增加 X 快照观点元数据和确定性预检

**Files:**
- Modify: `scripts/x_authenticated_feed.py`、`scripts/x_web_feed.py` 快照字段透传
- Modify: `src/collectors/x_feed.py` 将配置资格、原创/回复/引用关系映射到候选
- Create: `src/briefing/opinion.py` 观点候选规则和原因码
- Modify: `src/briefing/evidence.py` 将观点元数据写入 `SourceEvidence`
- Test: `tests/test_x_authenticated_feed.py`, `tests/test_x_web_feed.py`, `tests/test_x_feed_collector.py`, `tests/test_opinion_rules.py`

**Interfaces:**
- `class OpinionEligibility` exposes `eligible: bool`, `reason_codes: tuple[str, ...]`, `stance_type: str`, `context_complete: bool`, `original_post: bool`.
- `evaluate_opinion_candidate(candidate: Mapping[str, object], registry_source: Mapping[str, object] | None) -> OpinionEligibility`.
- `SourceEvidence` gains JSON-safe fields `content_type`, `opinion_author`, `opinion_eligible`, `original_post`, `context_complete`, `stance_type`, `affiliation_disclosure` with conservative defaults.
- `tests/test_opinion_rules.py` defines `_candidate(**overrides)` with a valid X status, `text`, `source_handle`, `source_tier`, `reply_to_id`, `quoted_id`, `is_repost`, and `context_complete`, plus `_eligible_source()` returning `{"name": "Andrej Karpathy", "handle": "karpathy", "tier": "research", "official": False, "opinion_eligible": True}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_original_eligible_person_with_substantive_claim_is_opinion():
    result = evaluate_opinion_candidate(_candidate(text="I think open models will win because they are easier to adapt"), _eligible_source())
    assert result.eligible is True
    assert result.original_post is True
    assert result.stance_type in {"opinion", "prediction", "critique", "comparison"}


def test_repost_and_missing_reply_context_are_rejected():
    assert "opinion_repost_only" in evaluate_opinion_candidate(_candidate(repost=True), _eligible_source()).reason_codes
    assert "opinion_context_missing" in evaluate_opinion_candidate(_candidate(reply_to_id="41"), _eligible_source()).reason_codes


def test_noneligible_media_account_cannot_become_opinion():
    result = evaluate_opinion_candidate(_candidate(), {"opinion_eligible": False, "tier": "media"})
    assert result.eligible is False
    assert result.reason_codes == ("opinion_author_not_allowed",)


def test_opinion_metadata_round_trips_in_source_evidence():
    evidence = source_evidence_from_candidate(_candidate(), trusted_x_collector=True)
    assert evidence.content_type == "attributed_opinion"
    assert evidence.opinion_eligible is True
    assert SourceEvidence.from_dict(evidence.to_dict()) == evidence
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_opinion_rules.py -q`

Expected: FAIL because there is no opinion evaluator and `SourceEvidence` has no content-type fields.

- [ ] **Step 3: Implement the minimal rules and metadata**

Use only deterministic signals: registry eligibility, `repost`/`retweeted_status` absence, reply parent presence, non-empty text, minimum substantive length, and keyword groups for first-person judgement/prediction/critique/comparison. Reject promotional markers (`招聘`, `课程`, `报名`, `折扣`, `congratulations`, `hiring`, `course`, `join us`) and empty/link-only posts. For replies require a non-empty `context_complete` marker from the snapshot; otherwise return `opinion_context_missing`. For quotes require non-empty own text plus `quoted_id`; a quote without own commentary returns `opinion_repost_only`. Store the evaluated result on the candidate, map eligible natural-person X candidates to `content_type="attributed_opinion"`, and leave all other X candidates as `fact_event` candidates subject to existing fact publishability.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_opinion_rules.py tests/test_x_authenticated_feed.py tests/test_x_web_feed.py tests/test_x_feed_collector.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/x_authenticated_feed.py scripts/x_web_feed.py src/collectors/x_feed.py src/briefing/opinion.py src/briefing/evidence.py tests/test_opinion_rules.py tests/test_x_authenticated_feed.py tests/test_x_web_feed.py tests/test_x_feed_collector.py
git commit -m "feat: 增加 X 署名观点预检"
```

### Task 3: 扩展 BriefItem/BuiltBrief 内容契约和观点构建

**Files:**
- Modify: `src/briefing/models.py` `BuiltBrief`、`BriefItem` 序列化和不变量
- Modify: `src/briefing/builder.py` prompt、fallback 和 strict response
- Modify: `src/briefing/validator.py` 分流事实/观点规则
- Modify: `src/briefing/adapters.py` 公共投影
- Test: `tests/test_fact_brief_contract.py`, `tests/test_brief_builder.py`, `tests/test_brief_validator.py`, `tests/test_opinion_builder.py`

**Interfaces:**
- `BriefItem.content_type` is exactly `fact_event|attributed_opinion`.
- `BuiltBrief.content_type` and `BriefItem` opinion fields round-trip through `to_dict/from_dict`.
- `BriefValidator` accepts attributed titles only when title and every brief sentence retain the canonical author attribution and all bindings point to the same X URL.
- `tests/test_opinion_builder.py` defines `opinion_event()`, `opinion_draft(chinese_title: str)`, `opinion_item(**overrides)`, and `fake_llm_request_for(event)` using the same `SourceEvidence`/`MergedEvent` fixture style as `tests/test_brief_validator.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_opinion_brief_requires_attribution_and_source_binding():
    item = opinion_item(chinese_title="Andrej Karpathy 认为开放模型更易适配")
    assert item.content_type == "attributed_opinion"
    assert item.opinion_author == "Andrej Karpathy"


def test_opinion_validator_rejects_objective_rewrite_without_attribution():
    result = validator.validate(opinion_event(), opinion_draft(chinese_title="开放模型将赢得竞争"), generation_attempt=1)
    assert result.action == "rebuild"
    assert "opinion_attribution_missing" in result.reason_codes


def test_opinion_builder_prompt_forbids_fact_claims_and_preserves_author():
    request = fake_llm_request_for(opinion_event())
    assert "保留作者归因" in request
    assert "不得改写成客观事实" in request
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_opinion_builder.py tests/test_fact_brief_contract.py::test_brief_item_round_trips_content_type -q`

Expected: FAIL because the current models only represent facts and the builder prompt is fact-only.

- [ ] **Step 3: Implement the minimal content contract**

Add `content_type` and opinion metadata to `BuiltBrief`/`BriefItem`, default old serialized records to `fact_event`, and reject unknown values. In the builder payload include `content_type`, `opinion_author`, `stance_type`, `context_complete`, and `affiliation_disclosure`. Use a separate prompt branch for opinions that says to preserve attribution, compress only the source text, and return `opinion_attribution_missing`/`opinion_claim_not_source_bound` when unsafe. In the validator, run existing fact rules for `fact_event`; for opinions require Chinese attributed title, author name or exact configured handle/name anchor, no unsupported institutional announcement verbs, and evidence bindings whose quotes contain the original post text. At most two brief sentences remain; quality-LLM failure falls back to rules-only only when these deterministic rules pass.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_opinion_builder.py tests/test_fact_brief_contract.py tests/test_brief_builder.py tests/test_brief_validator.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/briefing/models.py src/briefing/builder.py src/briefing/validator.py src/briefing/adapters.py tests/test_opinion_builder.py tests/test_fact_brief_contract.py tests/test_brief_builder.py tests/test_brief_validator.py
git commit -m "feat: 支持署名观点简报契约"
```

### Task 4: 分类型聚类、选择和 DraftDecision 门禁

**Files:**
- Modify: `src/briefing/clusterer.py` and/or `src/briefing/semantic.py` to avoid merging distinct viewpoints solely by shared topic
- Modify: `src/briefing/selector.py` to enforce facts-first, opinion max, X max, author max
- Modify: `src/briefing/decision.py` to require minimum facts and validate opinion/X/author limits
- Modify: `src/briefing/pipeline.py` to route opinion candidates, audit exclusions, and continue fact fallback
- Test: `tests/test_brief_selector.py`, `tests/test_draft_decision.py`, `tests/test_fact_brief_pipeline.py`, `tests/test_opinion_selection.py`

**Interfaces:**
- `BriefSelector.fact_count`, `opinion_count`, `x_count`, and `opinion_author_counts` are deterministic projections.
- `decide_draft()` blocks with reason codes `insufficient_fact_items`, `opinion_limit`, `x_limit`, or `opinion_author_limit` when applicable.
- Pipeline diagnostics include `opinion_selected_count`, `opinion_rejected_count`, `fact_reserve_fill_count` and reason-code counts.
- `tests/test_opinion_selection.py` defines `config = BriefingConfig(min_items=5, max_items=15, min_fact_items=3, max_opinion_items=3, max_x_items=8, target_x_items=5)` and fixture factories `fact_event(index)`, `opinion_event()`, `fact_item(index)`, and `opinion_item(**overrides)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_selector_keeps_three_facts_before_accepting_opinions():
    selector = BriefSelector([opinion_event(), fact_event(1), fact_event(2), fact_event(3)], config)
    assert selector.accept(fact_item(1))
    assert selector.accept(fact_item(2))
    assert selector.accept(fact_item(3))
    assert selector.accept(opinion_item())
    assert selector.fact_count == 3


def test_selector_rejects_second_opinion_by_same_author():
    selector = BriefSelector([], config)
    assert selector.accept(opinion_item(event_key="a", opinion_author="Andrej Karpathy"))
    assert not selector.accept(opinion_item(event_key="b", opinion_author="Andrej Karpathy"))


def test_decision_blocks_when_three_facts_are_missing_even_with_five_opinions():
    decision = decide_draft([opinion_item(index) for index in range(5)], config)
    assert decision.action == "block"
    assert "insufficient_fact_items" in decision.reasons


def test_decision_allows_three_facts_plus_three_opinions_and_eight_x_items():
    decision = decide_draft([fact_item(i) for i in range(3)] + [opinion_item(i) for i in range(3)], config)
    assert decision.action == "create"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_opinion_selection.py tests/test_draft_decision.py::test_decision_blocks_when_three_facts_are_missing_even_with_five_opinions -q`

Expected: FAIL because the current selector and decision count only total items and X items.

- [ ] **Step 3: Implement the minimal selection and decision rules**

Track fact/opinion counts in `BriefSelector`. `accept()` rejects an opinion when the author already exists, the opinion quota is full, or accepting it would leave fewer than `min_fact_items` possible fact slots; it rejects X when the new cap is reached. Order queue with facts before opinions until the fact reserve is met, then use existing authority/freshness/topic preferences. Update `replace_accepted()` atomically for both content type and author quotas. Update `decide_draft()` to validate all `BriefItem.content_type` values, fact minimum, opinion maximum, X maximum and unique opinion authors; keep total minimum/maximum and dedup checks unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_opinion_selection.py tests/test_brief_selector.py tests/test_draft_decision.py tests/test_fact_brief_pipeline.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/briefing/clusterer.py src/briefing/semantic.py src/briefing/selector.py src/briefing/decision.py src/briefing/pipeline.py tests/test_opinion_selection.py tests/test_brief_selector.py tests/test_draft_decision.py tests/test_fact_brief_pipeline.py
git commit -m "feat: 增加观点与事实配额门禁"
```

### Task 5: 审计、网页/微信展示和回归兼容

**Files:**
- Modify: `src/briefing/adapters.py`, `src/generator.py`, `src/pipeline_artifacts.py` for content labels and public projections
- Modify: `src/briefing/pipeline.py` audit fields and reason codes
- Modify: `tests/test_generator.py`, `tests/test_wechat_boundary.py`, `tests/test_pipeline_artifacts.py`, `tests/test_latest_schema_compat.py`
- Modify: `project_docs/pipeline.md`, `project_docs/workflow.md`, `AGENTS.md` navigation/constraints if required

**Interfaces:**
- Public item projection has `content_type`, `content_label` (`事实简报` or `圈内观点`), author and source URL; raw evidence remains private.
- Debug audit has `content_type`, `opinion_author`, `opinion_eligible`, `original_post`, `context_complete`, `stance_type`, `affiliation_disclosure`, and precise opinion reason codes.
- Renderer tests reuse the `opinion_item()` fixture from `tests/test_opinion_builder.py`; the audit test invokes `run_brief_pipeline(events, quarantined=(), config=config, builder=builder, validator=validator, now=fixed_now)` with the concrete fake builder/validator fixtures already used in `tests/test_fact_brief_pipeline.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_public_projection_labels_opinion_without_exposing_raw_evidence():
    payload = brief_item_to_display_dict(opinion_item())
    assert payload["content_type"] == "attributed_opinion"
    assert payload["content_label"] == "圈内观点"
    assert payload["opinion_author"] == "Andrej Karpathy"
    assert "evidence_text" not in payload["canonical_source"]


def test_wechat_render_shows_opinion_label_and_original_x_link():
    html = render_wechat_article([opinion_item()])
    assert "圈内观点" in html
    assert "https://x.com/karpathy/status/42" in html


def test_audit_contains_opinion_fields_and_reason_code():
    result = run_brief_pipeline(events, quarantined=(), config=config, builder=builder, validator=validator, now=fixed_now)
    entry = next(item for item in result.audit_entries if item["event"]["content_type"] == "attributed_opinion")
    assert entry["opinion_author"]
    assert "opinion_" in " ".join(entry["final_reason_codes"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_generator.py tests/test_wechat_boundary.py tests/test_pipeline_artifacts.py tests/test_latest_schema_compat.py -q`

Expected: FAIL because display and audit payloads do not expose content type or opinion labels.

- [ ] **Step 3: Implement the minimal renderer and audit changes**

Add content fields to the existing public adapter, show a compact `圈内观点` label next to the source in both HTML outputs, preserve the canonical X URL, and keep raw source evidence out of public JSON. Add audit fields from `SourceEvidence`/`BriefItem` and retain original failure reasons alongside `source_fallback_used` and rules-only degradation. Keep old v1/latest payloads readable by defaulting missing `content_type` to `fact_event`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_generator.py tests/test_wechat_boundary.py tests/test_pipeline_artifacts.py tests/test_latest_schema_compat.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/briefing/adapters.py src/generator.py src/pipeline_artifacts.py src/briefing/pipeline.py project_docs/pipeline.md project_docs/workflow.md AGENTS.md tests/test_generator.py tests/test_wechat_boundary.py tests/test_pipeline_artifacts.py tests/test_latest_schema_compat.py
git commit -m "feat: 展示并审计 X 署名观点"
```

### Task 6: 全量验证、冻结快照干跑和服务器验收

**Files:**
- Modify only if test findings require it; otherwise no source changes
- Test: full `tests/` suite and deployment smoke commands

- [ ] **Step 1: Run focused and full tests**

Run:

```powershell
python -m pytest tests/test_opinion_rules.py tests/test_opinion_builder.py tests/test_opinion_selection.py tests/test_briefing_config.py tests/test_x_feed_collector.py tests/test_brief_builder.py tests/test_brief_validator.py tests/test_draft_decision.py -q
python -m pytest -q
git diff --check
git status --short
```

Expected: all tests pass, diff check is clean, and only the intended commits plus the pre-existing untracked user plan remain.

- [ ] **Step 2: Run a local frozen-snapshot dry run**

Use a fixture containing at least 3 valid fact events, 3 eligible original opinions from distinct authors, one rejected repost, one missing-context reply, and enough X records to exercise the 8-item cap. Run `SKIP_WECHAT_DRAFT=1 python -m src.main` with `DAILY_X_TARGET_ITEMS=5`, `DAILY_X_MAX_ITEMS=8`, and inspect `docs/debug/<date>-briefing.json`.

Expected: `DraftDecision.action=create`, at least 3 `fact_event`, no more than 3 `attributed_opinion`, no more than 8 X canonical sources, no repeated opinion author, and public HTML/Wechat output contains `圈内观点` only for opinion entries.

- [ ] **Step 3: Deploy to server and run one safe production task**

On `root@tankex.xyz` in `/opt/ai-news`, fetch/pull the approved `master`, set only the documented `.env` overrides, run `docker compose up -d --force-recreate`, then execute the existing dry-run command with `SKIP_WECHAT_DRAFT=1`. Do not call real WeChat draft APIs until the result is inspected.

- [ ] **Step 4: Verify server acceptance criteria**

Check container health, task exit code, `docs/latest.json`, `docs/debug/<date>-briefing.json`, and the public preview. Report exact selected counts, fact/opinion/X counts, exclusion reason distribution, and any source failures. If the task blocks, preserve the artifacts and fix the specific failing rule with a regression test before rerunning.

- [ ] **Step 5: Commit any verification-only documentation update**

```bash
git diff --check
git status --short
```

Do not commit generated `docs/`, logs, `.env`, snapshots, cookies, or server-private files.

## Self-Review Checklist

- Spec coverage: content types, explicit opinion whitelist, original/reply/quote rules, author/topic deduplication, 3-fact minimum, 3-opinion maximum, X soft target 5, X hard cap 8, audit reason codes, rendering label, rules-only degradation, dry-run rollout, and server acceptance are each covered by a task.
- Placeholder scan: no `TBD`, `TODO`, or unspecified validation step is used; each implementation step names files, interfaces, exact commands, and expected results.
- Type consistency: `content_type`, opinion metadata and configuration names are defined in Tasks 1-3 before Tasks 4-5 consume them; `BriefSelector` projections and `decide_draft()` reason codes are named consistently.
