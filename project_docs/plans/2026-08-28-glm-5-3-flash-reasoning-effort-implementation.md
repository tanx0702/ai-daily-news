# GLM-5.3-Flash Low Reasoning Effort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the production fact-brief structured LLM calls use `reasoning_effort=low` only for `glm-5.3-flash`, while preserving identical requests for every other OpenAI-compatible model.

**Architecture:** Add one pure request-option helper beside `LLMConfig`, then expand its result into the three production `chat.completions.create` calls used by content building, quality validation, and semantic duplicate review. Keep timeouts, retry settings, response parsing, fallback logic, and all non-GLM model behavior unchanged.

**Tech Stack:** Python 3.12+, OpenAI Python SDK 1.58.1, pytest, Docker Compose, SSH

---

### Task 1: Specify Provider-Safe Request Options

**Files:**
- Create: `tests/test_llm_config.py`
- Modify: `src/llm_config.py:1-40`

- [ ] **Step 1: Write failing unit tests for GLM and non-GLM options**

```python
from src.llm_config import LLMConfig, structured_llm_request_options


def test_glm_5_3_flash_uses_low_reasoning_effort():
    config = LLMConfig("key", " GLM-5.3-FLASH ", "https://open.bigmodel.cn/api/paas/v4")

    assert structured_llm_request_options(config) == {
        "extra_body": {"reasoning_effort": "low"}
    }


def test_other_models_do_not_receive_provider_specific_options():
    config = LLMConfig("key", "deepseek-chat", "https://api.deepseek.com/v1")

    assert structured_llm_request_options(config) == {}
```

- [ ] **Step 2: Run the unit tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_llm_config.py
```

Expected: collection fails because `structured_llm_request_options` does not exist. This is the expected missing-feature failure, not a test syntax error.

- [ ] **Step 3: Add the smallest pure helper**

Add below `LLMConfig`:

```python
def structured_llm_request_options(config: LLMConfig) -> dict[str, object]:
    if config.model.strip().casefold() == "glm-5.3-flash":
        return {"extra_body": {"reasoning_effort": "low"}}
    return {}
```

- [ ] **Step 4: Run the unit tests and verify GREEN**

Run:

```powershell
python -m pytest -q tests/test_llm_config.py
```

Expected: `2 passed`.

### Task 2: Apply Options To Content Generation

**Files:**
- Modify: `tests/test_brief_builder.py:205-275`
- Modify: `src/briefing/builder.py:15-18`
- Modify: `src/briefing/builder.py:424-458`

- [ ] **Step 1: Allow the builder test helper to select a model**

Change the helper signature and config construction to:

```python
def builder_with_responses(
    responses,
    *,
    api_key="key",
    model="model",
    builder_batch_size=None,
):
    # existing client/config setup remains unchanged
    builder = BriefBuilder(
        config,
        LLMConfig(api_key=api_key, model=model, base_url="https://llm.example/v1"),
        client_factory=lambda **_kwargs: client,
    )
```

- [ ] **Step 2: Write failing builder request tests**

```python
def test_builder_uses_low_reasoning_effort_for_glm_5_3_flash():
    item = event(1)
    payload = {
        "items": [generated_item(1, item.event_key, item.canonical_evidence.url)]
    }
    builder, client = builder_with_responses([payload], model="glm-5.3-flash")

    builder.build_batch([item], attempts={})

    assert client.chat.completions.calls[0]["extra_body"] == {
        "reasoning_effort": "low"
    }


def test_builder_does_not_send_glm_options_to_other_models():
    item = event(1)
    payload = {
        "items": [generated_item(1, item.event_key, item.canonical_evidence.url)]
    }
    builder, client = builder_with_responses([payload], model="deepseek-chat")

    builder.build_batch([item], attempts={})

    assert "extra_body" not in client.chat.completions.calls[0]
```

- [ ] **Step 3: Run the builder tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_brief_builder.py -k "reasoning_effort or glm_options"
```

Expected: the GLM assertion fails because the request has no `extra_body`; the non-GLM assertion already passes and protects compatibility.

- [ ] **Step 4: Expand the shared request options into the content call**

Import the helper:

```python
from src.llm_config import LLMConfig, structured_llm_request_options
```

Add this final keyword argument to the existing `.create(...)` call:

```python
                **structured_llm_request_options(self.llm_config),
```

- [ ] **Step 5: Run focused and complete builder tests**

Run:

```powershell
python -m pytest -q tests/test_brief_builder.py -k "reasoning_effort or glm_options"
python -m pytest -q tests/test_brief_builder.py
```

Expected: both focused tests pass, then the full builder module passes.

### Task 3: Apply Options To Quality And Semantic Review

**Files:**
- Modify: `tests/test_brief_validator.py:1480-1550`
- Modify: `tests/test_semantic_reviewer.py:50-100`
- Modify: `src/briefing/validator.py:20-40`
- Modify: `src/briefing/validator.py:800-835`
- Modify: `src/briefing/semantic_reviewer.py:10-25`
- Modify: `src/briefing/semantic_reviewer.py:158-185`

- [ ] **Step 1: Add model selection to quality test helpers**

Change `quality_validator` to accept `model="quality-model"` and pass it into `LLMConfig`. Change `reviewer` in `tests/test_semantic_reviewer.py` the same way.

```python
def quality_validator(response, *, model="quality-model"):
    # existing setup
    quality_config=LLMConfig(
        api_key="quality-key",
        model=model,
        base_url="https://quality.example/v1",
    )
```

```python
def reviewer(responses, *, max_calls=20, model="quality-model"):
    # existing setup
    LLMConfig("quality-key", model, "https://quality.example/v1")
```

- [ ] **Step 2: Write failing integration assertions for both calls**

```python
def test_quality_validator_uses_low_reasoning_effort_for_glm_5_3_flash():
    instance, client = quality_validator(
        {"items": [review_item()]},
        model="glm-5.3-flash",
    )

    instance.validate(event(), draft(), generation_attempt=1, now=NOW)

    assert client.calls[0]["extra_body"] == {"reasoning_effort": "low"}
```

```python
def test_semantic_reviewer_uses_low_reasoning_effort_for_glm_5_3_flash():
    instance, client = reviewer([response()], model="glm-5.3-flash")

    instance.review(LEFT, RIGHT)

    assert client.calls[0]["extra_body"] == {"reasoning_effort": "low"}
```

- [ ] **Step 3: Run both tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_brief_validator.py tests/test_semantic_reviewer.py -k "reasoning_effort"
```

Expected: `2 failed` because neither request currently contains `extra_body`.

- [ ] **Step 4: Apply the helper to both production requests**

In each module, import:

```python
from src.llm_config import LLMConfig, structured_llm_request_options
```

Add to the existing quality request:

```python
            **structured_llm_request_options(self.quality_llm_config),
```

Add to the existing semantic request:

```python
            **structured_llm_request_options(self.quality_llm_config),
```

- [ ] **Step 5: Run focused and complete review tests**

Run:

```powershell
python -m pytest -q tests/test_brief_validator.py tests/test_semantic_reviewer.py -k "reasoning_effort"
python -m pytest -q tests/test_brief_validator.py tests/test_semantic_reviewer.py
```

Expected: focused tests pass, then both full modules pass.

### Task 4: Synchronize Contracts And Verify Release

**Files:**
- Modify: `AGENTS.md:44`
- Modify: `project_docs/pipeline.md:60-75`

- [ ] **Step 1: Document the model-specific production rule**

Add to the existing content/quality LLM rule in `AGENTS.md`:

```text
`glm-5.3-flash` 的结构化内容、质量和语义去重请求必须使用 `reasoning_effort=low`，其它供应商不得接收该智谱专用参数。
```

Add the same behavior to pipeline stage 2 after the SDK retry rule.

- [ ] **Step 2: Verify the affected modules**

Run:

```powershell
python -m pytest -q tests/test_llm_config.py tests/test_brief_builder.py tests/test_brief_validator.py tests/test_semantic_reviewer.py
```

Expected: all affected tests pass.

- [ ] **Step 3: Run complete repository verification**

Run:

```powershell
python -m pytest -q
git diff --check
git status --short
```

Expected: the full suite passes; diff check exits 0; the pre-existing untracked `project_docs/plans/2026-08-18-authenticated-x-snapshot.md` remains untouched.

- [ ] **Step 4: Inspect and commit only this implementation**

Run:

```powershell
git add src/llm_config.py src/briefing/builder.py src/briefing/validator.py src/briefing/semantic_reviewer.py tests/test_llm_config.py tests/test_brief_builder.py tests/test_brief_validator.py tests/test_semantic_reviewer.py AGENTS.md project_docs/pipeline.md
git diff --staged --check
git diff --staged
git commit -m "fix(llm): 降低 GLM 结构化请求推理强度"
```

Expected: the staged diff contains only the shared helper, three production call sites, tests, and matching documentation.

- [ ] **Step 5: Push and deploy the unique release baseline**

Run:

```powershell
git push origin master
ssh root@tankex.xyz "cd /opt/ai-news && git pull --ff-only origin master && docker compose up -d --build --force-recreate"
```

Expected: remote and server `master` fast-forward to the implementation commit; containers recreate and `web` becomes healthy without printing `.env` secrets.

- [ ] **Step 6: Run a safe server verification**

Run:

```powershell
ssh root@tankex.xyz "cd /opt/ai-news && docker compose exec -e SKIP_WECHAT_DRAFT=1 -T web python -m src.main"
```

Expected: no WeChat draft is created. Inspect `docs/latest.json`, `docs/debug/<date>-briefing.json`, and sanitized logs for content request success/timeout/invalid/circuit counts, selected item counts, X counts, and final `DraftDecision`; report the actual result even if quality gates block the issue.
