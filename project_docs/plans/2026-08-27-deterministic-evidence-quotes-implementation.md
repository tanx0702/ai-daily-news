# Deterministic Evidence Quotes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace model-copied evidence quotes with deterministic quote-ID resolution while preserving the existing final evidence and publication gates.

**Architecture:** `src/briefing/builder.py` will split canonical `evidence_text` into stable, verbatim quote candidates and include them in the private LLM payload. The LLM will return only `source_quote_id`; `_strict_item` will resolve the ID to the original quote and canonical URL, rejecting invalid title references and degrading invalid summary references to `title_only`.

**Tech Stack:** Python 3.12, OpenAI-compatible chat completions, pytest, Docker Compose, SSH.

---

### Task 1: Generate stable verbatim quote candidates

**Files:**
- Modify: `src/briefing/builder.py`
- Test: `tests/test_brief_builder.py`

- [ ] **Step 1: Write failing quote segmentation tests**

Import `_source_quotes` and add focused tests proving the helper preserves punctuation, returns contiguous source substrings, deduplicates in first-seen order, and does not split decimal values:

```python
def test_source_quotes_are_stable_verbatim_segments():
    evidence = "Model 4.5 scores 10.2 points. Next result!\nNext result!"

    quotes = _source_quotes(evidence)

    assert quotes == (
        ("q1", "Model 4.5 scores 10.2 points."),
        ("q2", "Next result!"),
    )
    assert all(quote in evidence for _quote_id, quote in quotes)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
python -m pytest -q tests/test_brief_builder.py::test_source_quotes_are_stable_verbatim_segments
```

Expected: collection fails because `_source_quotes` does not exist.

- [ ] **Step 3: Implement the minimal deterministic splitter**

Add one private compiled pattern and helper in `src/briefing/builder.py`:

```python
_SOURCE_QUOTE_PATTERN = re.compile(
    r".+?(?:[。！？!?；;]+|[A-Za-z0-9]\.(?=\s|$)|\r?\n+|$)",
    re.DOTALL,
)


def _source_quotes(value: str) -> tuple[tuple[str, str], ...]:
    quotes: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _SOURCE_QUOTE_PATTERN.finditer(value):
        quote = match.group(0).strip()
        if not quote or quote in seen:
            continue
        seen.add(quote)
        quotes.append((f"q{len(quotes) + 1}", quote))
    return tuple(quotes)
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```powershell
python -m pytest -q tests/test_brief_builder.py::test_source_quotes_are_stable_verbatim_segments
```

Expected: `1 passed`.

### Task 2: Send quote IDs and resolve them deterministically

**Files:**
- Modify: `src/briefing/builder.py`
- Modify: `tests/test_brief_builder.py`

- [ ] **Step 1: Write failing request-contract tests**

Update the `generated_item` test helper to keep its existing call signature but emit the new private response schema:

```python
def generated_item(index: int, event_key: str, _source_url: str) -> dict:
    return {
        "index": index,
        "event_key": event_key,
        "chinese_title": f"示例公司发布模型 {index}",
        "brief": f"示例公司发布模型 {index}。",
        "evidence_targets": [
            {"target": "title", "source_quote_id": "q1"},
            {"target": "brief_1", "source_quote_id": "q1"},
        ],
    }
```

Add assertions that the request contains exact quote candidates and the prompt requests IDs instead of copied quotes or URLs:

```python
def test_builder_sends_verbatim_quote_candidates_and_requests_ids():
    item = event(1)
    builder, client = builder_with_responses(
        [{"items": [generated_item(1, item.event_key, item.canonical_evidence.url)]}]
    )

    builder.build_batch([item], attempts={})

    call = client.chat.completions.calls[0]
    request_event = json.loads(call["messages"][1]["content"])["events"][0]
    assert request_event["source_quotes"] == [
        {"quote_id": "q1", "text": "Example releases Model 1."},
        {"quote_id": "q2", "text": "The model adds a text API."},
    ]
    prompt = call["messages"][0]["content"]
    assert "source_quote_id" in prompt
    assert "返回 target/source_quote/source_url" not in prompt
```

- [ ] **Step 2: Write failing resolution and canonical URL tests**

Add a test that uses multiple IDs for one target and asserts the builder constructs exact `EvidenceBinding` objects without trusting a model URL:

```python
def test_builder_resolves_quote_ids_to_verbatim_quotes_and_canonical_url():
    item = event(1)
    payload = generated_item(1, item.event_key, item.canonical_evidence.url)
    payload["evidence_targets"] = [
        {"target": "title", "source_quote_id": "q1"},
        {"target": "title", "source_quote_id": "q2"},
        {"target": "brief_1", "source_quote_id": "q1"},
    ]
    builder, _ = builder_with_responses([{"items": [payload]}])

    result = builder.build_batch([item], attempts={})[0]

    assert [binding.source_quote for binding in result.draft.evidence_bindings] == [
        "Example releases Model 1.",
        "The model adds a text API.",
        "Example releases Model 1.",
    ]
    assert all(
        binding.source_url == item.canonical_evidence.url
        for binding in result.draft.evidence_bindings
    )
```

- [ ] **Step 3: Run the new contract tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_brief_builder.py -k "quote_candidates or resolves_quote_ids"
```

Expected: failures show that the payload lacks `source_quotes` and `_strict_item` rejects `source_quote_id`.

- [ ] **Step 4: Implement the private request and response contract**

Add `source_quotes` to each request event:

```python
"source_quotes": [
    {"quote_id": quote_id, "text": quote}
    for quote_id, quote in _source_quotes(
        event.canonical_evidence.evidence_text
    )
],
```

Change the system prompt to require one or more `{target, source_quote_id}` records and state that IDs must come from the same event's `source_quotes`. Replace the old copied-quote/URL instruction with:

```python
"为标题和摘要中的每个完整展示目标返回 target/source_quote_id；target 只能是 "
"title、brief_1 或 brief_2，同一 target 可返回多条记录；source_quote_id 必须逐字选择"
"该事件 source_quotes 中存在的 quote_id，不得返回、改写或拼接原文 quote，也不得返回 URL；"
```

In `_strict_item`, build `quote_by_id = dict(_source_quotes(event.canonical_evidence.evidence_text))`, accept only binding objects with exactly `target` and `source_quote_id`, and construct bindings with:

```python
EvidenceBinding(
    claim=target_claims[target],
    source_quote=quote_by_id[source_quote_id],
    source_url=event.canonical_evidence.url,
)
```

Unknown targets and invalid title IDs return `None`. Preserve multiple records for the same target.

- [ ] **Step 5: Run the builder suite and make the mechanical fixture updates**

Run:

```powershell
python -m pytest -q tests/test_brief_builder.py
```

Update only tests that manually construct `evidence_targets` so they use `source_quote_id`. Preserve all existing assertions about batching, attempts, malformed items, fallback behavior, protected anchors and reason codes.

Expected: the complete builder suite passes.

### Task 3: Degrade unresolved summary references without weakening title evidence

**Files:**
- Modify: `src/briefing/builder.py`
- Modify: `tests/test_brief_builder.py`

- [ ] **Step 1: Write failing invalid-reference tests**

Add three explicit cases:

```python
def test_builder_rejects_unknown_title_quote_id():
    item = event(1)
    payload = generated_item(1, item.event_key, item.canonical_evidence.url)
    payload["evidence_targets"][0]["source_quote_id"] = "q999"
    builder, _ = builder_with_responses([{"items": [payload]}])

    result = builder.build_batch([item], attempts={})[0]

    assert result.draft is None
    assert result.reason_code == "builder_item_malformed"


def test_builder_drops_brief_when_any_summary_quote_id_is_unknown():
    item = event(1)
    payload = generated_item(1, item.event_key, item.canonical_evidence.url)
    payload["evidence_targets"][1]["source_quote_id"] = "q999"
    builder, _ = builder_with_responses([{"items": [payload]}])

    result = builder.build_batch([item], attempts={})[0]

    assert result.draft.brief == ""
    assert result.draft.brief_mode == "title_only"
    assert result.draft.brief_reason == "brief_quote_unresolved"
    assert [binding.claim for binding in result.draft.evidence_bindings] == [
        result.draft.chinese_title
    ]


def test_builder_rejects_legacy_copied_quote_response():
    item = event(1)
    payload = generated_item(1, item.event_key, item.canonical_evidence.url)
    payload["evidence_targets"][0] = {
        "target": "title",
        "source_quote": item.canonical_evidence.source_title,
        "source_url": item.canonical_evidence.url,
    }
    builder, _ = builder_with_responses([{"items": [payload]}])

    result = builder.build_batch([item], attempts={})[0]

    assert result.draft is None
    assert result.reason_code == "builder_item_malformed"
```

- [ ] **Step 2: Run the three tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_brief_builder.py -k "unknown_title_quote_id or summary_quote_id_is_unknown or legacy_copied_quote"
```

Expected: the summary case is rejected instead of becoming `title_only`, and the new-schema cases do not parse yet.

- [ ] **Step 3: Implement title-only degradation**

Resolve bindings by target. Require at least one valid title binding. If any expected brief target is missing or contains an unknown quote ID, discard every brief binding and construct the draft with:

```python
brief = ""
brief_reason = "brief_quote_unresolved"
bindings = bindings_by_target["title"]
```

Otherwise retain the normalized brief, all valid bindings and the existing `brief_empty` behavior. Structural errors such as extra binding fields, an unknown target, a non-string ID or a missing title target remain malformed.

- [ ] **Step 4: Run focused builder and validator suites**

Run:

```powershell
python -m pytest -q tests/test_brief_builder.py tests/test_brief_validator.py
```

Expected: both suites pass, including existing validator checks for `quote_not_found`, `claim_quote_mismatch`, action binding and translation failures.

### Task 4: Synchronize contracts and verify the repository

**Files:**
- Modify: `project_docs/pipeline.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Document the deterministic quote boundary**

Update the content LLM stage to state that Python sends verbatim numbered quote candidates, the LLM returns quote IDs, and builder restores canonical quotes/URLs. Document `brief_quote_unresolved -> title_only`, while preserving the rule that invalid title evidence rebuilds or rejects the item.

Add the same concise constraint to the root `AGENTS.md` quality paragraph because this changes a production quality gate boundary.

- [ ] **Step 2: Run repository verification**

Run:

```powershell
python -m pytest -q
git diff --check
git status --short
```

Expected: the full suite passes; whitespace check exits 0; status contains only this task's implementation/doc files plus the preserved user-owned untracked `project_docs/plans/2026-08-18-authenticated-x-snapshot.md`.

- [ ] **Step 3: Inspect and commit the implementation**

Run:

```powershell
git add src/briefing/builder.py tests/test_brief_builder.py project_docs/pipeline.md AGENTS.md project_docs/plans/2026-08-27-deterministic-evidence-quotes-implementation.md
git diff --staged --check
git diff --staged
git commit -m "fix(briefing): 确定性绑定原文引用"
```

Expected: only the reviewed scope is committed; hooks pass without bypass flags.

### Task 5: Review, merge and validate on the server

**Files:**
- No additional source files.

- [ ] **Step 1: Request an independent code review**

Review the branch diff against the approved design, prioritizing evidence-gate regressions, silent fallback, prompt/schema mismatches and missing tests. Address only confirmed findings, then rerun affected checks.

- [ ] **Step 2: Merge and push the release baseline**

Merge the temporary branch into `master` with an English merge commit:

```text
Merge: 合并确定性证据引用修复
```

Push only `master`; do not publish the temporary branch. Preserve the user-owned untracked plan file and remove the temporary branch/worktree after integration.

- [ ] **Step 3: Deploy without publishing a WeChat draft**

On `root@tankex.xyz`, pull `master` in `/opt/ai-news` and run:

```bash
docker compose up -d --force-recreate
```

Verify that the container still resolves `glm-4.5-air` with base URL `https://open.bigmodel.cn/api/paas/v4` without printing the API key.

- [ ] **Step 4: Repeat the fixed real-sample comparison**

Run the same 5 X + 5 RSS single-item sample used before implementation. Record request availability, structured drafts, `quote_not_found`, `brief_quote_unresolved`, and display-contract pass counts. Success requires zero `quote_not_found` caused by model-copied text; other validator failures remain visible and must not be reclassified.

- [ ] **Step 5: Run the full safe server dry run**

Run exactly once:

```bash
docker compose exec -e SKIP_WECHAT_DRAFT=1 -T web python -m src.main
```

Inspect `docs/latest.json` and the daily briefing audit. Report selected count, content-type mix, X count, `DraftDecision`, and remaining exclusion reasons. A blocked decision remains a failed production outcome and must not be bypassed or described as successful.
