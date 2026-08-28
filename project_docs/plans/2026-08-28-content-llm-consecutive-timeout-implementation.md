# Content LLM Consecutive Timeout Circuit Breaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change the content LLM circuit breaker so it opens after three consecutive ordinary request timeouts, while a successful decoded response resets the streak and explicit provider failures still open it immediately.

**Architecture:** Keep the state inside each `BriefBuilder` instance and use a module constant with value `3`; do not add configuration or retries. Increment the streak only for `_is_timeout` exceptions, reset it immediately after response JSON decoding succeeds, and leave the existing nonrecoverable-error branch and audit reason codes unchanged.

**Tech Stack:** Python 3.12+, pytest, existing `BriefBuilder` fake OpenAI client tests, Docker Compose, SSH

---

### Task 1: Specify The Consecutive Timeout State Machine

**Files:**
- Modify: `tests/test_brief_builder.py:899-923`

- [ ] **Step 1: Replace the one-timeout circuit test with a failing three-timeout threshold test**

```python
def test_three_consecutive_timeouts_open_circuit_and_skip_next_batch():
    events = [event(index, chinese=True) for index in range(1, 5)]
    builder, client = builder_with_responses(
        [TimeoutError("Request timed out.") for _event in events[:3]]
    )

    results = builder.build_batch(events, attempts={})

    assert len(client.chat.completions.calls) == 3
    assert [result.circuit_open for result in results] == [False, False, True, True]
    assert [result.reason_code for result in results] == [
        "content_llm_timeout",
        "content_llm_timeout",
        "content_llm_timeout",
        "content_llm_unavailable",
    ]
    assert builder.diagnostics["content_llm_timeout_count"] == 3
    assert builder.diagnostics["content_llm_circuit_open_count"] == 1
```

- [ ] **Step 2: Add a failing success-reset test**

```python
def test_successful_decoded_response_resets_consecutive_timeout_count():
    events = [event(index, chinese=True) for index in range(1, 6)]
    successful_item = generated_item(
        1,
        events[2].event_key,
        events[2].canonical_evidence.url,
    )
    successful_item["chinese_title"] = "示例公司发布模型 3"
    successful_item["brief"] = "示例公司发布模型 3。"
    builder, client = builder_with_responses(
        [
            TimeoutError("Request timed out."),
            TimeoutError("Request timed out."),
            {"items": [successful_item]},
            TimeoutError("Request timed out."),
            TimeoutError("Request timed out."),
        ]
    )

    results = builder.build_batch(events, attempts={})

    assert len(client.chat.completions.calls) == 5
    assert all(result.circuit_open is False for result in results)
    assert results[2].reason_code is None
    assert [result.reason_code for result in results[:2] + results[3:]] == [
        "content_llm_timeout",
        "content_llm_timeout",
        "content_llm_timeout",
        "content_llm_timeout",
    ]
    assert builder.diagnostics["content_llm_timeout_count"] == 4
    assert builder.diagnostics["content_llm_success_count"] == 1
```

- [ ] **Step 3: Strengthen the existing single SDK-timeout assertion**

Add this assertion to `test_sdk_timeout_message_reports_transport_failure`:

```python
    assert result.circuit_open is False
```

- [ ] **Step 4: Run the new tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_brief_builder.py -k "three_consecutive_timeouts or successful_decoded_response_resets or sdk_timeout_message"
```

Expected: the threshold, reset, and single-timeout assertions fail because the first timeout currently opens `_circuit_open`; the failures must be assertion failures, not fixture or collection errors.

### Task 2: Implement The Minimum Circuit State Change

**Files:**
- Modify: `src/briefing/builder.py:20-22`
- Modify: `src/briefing/builder.py:326-339`
- Modify: `src/briefing/builder.py:446-475`

- [ ] **Step 1: Add the fixed module threshold**

Immediately after `logger`:

```python
_CONSECUTIVE_TIMEOUT_CIRCUIT_THRESHOLD = 3
```

- [ ] **Step 2: Initialize the instance streak counter**

Immediately after `self._circuit_open = False` in `BriefBuilder.__init__`:

```python
        self._consecutive_timeouts = 0
```

- [ ] **Step 3: Reset the streak after successful JSON decoding**

Immediately after `decoded = json.loads(_response_content(response))`:

```python
            self._consecutive_timeouts = 0
```

This position treats a transport response with decodable JSON as a successful request even when later schema validation records `invalid_builder_response`, matching the approved design.

- [ ] **Step 4: Open the circuit only when the timeout streak reaches three**

Replace the timeout branch with:

```python
            if _is_timeout(exc):
                self._consecutive_timeouts += 1
                if (
                    self._consecutive_timeouts
                    >= _CONSECUTIVE_TIMEOUT_CIRCUIT_THRESHOLD
                ):
                    self._circuit_open = True
                self.diagnostics["content_llm_timeout_count"] += 1
                request_failure_reason = "content_llm_timeout"
```

Do not modify `_is_nonrecoverable`; its 401/402/403/404/429/5xx and provider-marker paths must still return before this branch and open the circuit immediately.

- [ ] **Step 5: Run the focused timeout tests and verify GREEN**

Run:

```powershell
python -m pytest -q tests/test_brief_builder.py -k "three_consecutive_timeouts or successful_decoded_response_resets or sdk_timeout_message or payment_required or provider_502 or nonrecoverable_error"
```

Expected: `6 passed` with no failures. This proves the new threshold and reset behavior while retaining immediate circuit opening for 402, 502, and 401 examples.

- [ ] **Step 6: Run the complete builder test module**

Run:

```powershell
python -m pytest -q tests/test_brief_builder.py
```

Expected: all builder tests pass.

### Task 3: Synchronize The Production Contract Documentation

**Files:**
- Modify: `AGENTS.md:44`
- Modify: `project_docs/pipeline.md:111`

- [ ] **Step 1: Record the timeout threshold in the repository constraint**

In the content LLM rule in `AGENTS.md`, retain the immediate-circuit sentence and add:

```text
普通请求超时只在连续 3 次后打开本期熔断，任意一次完成 JSON 解码的成功响应都将连续超时计数清零。
```

- [ ] **Step 2: Record the same state transition in the pipeline failure table**

In the content LLM row of `project_docs/pipeline.md`, add after the immediate-circuit behavior:

```text
普通请求超时仅在连续 3 次后打开本期熔断，成功完成 JSON 解码则清零连续计数；
```

- [ ] **Step 3: Verify documentation consistency and whitespace**

Run:

```powershell
rg -n "连续 3 次|JSON 解码" AGENTS.md project_docs/pipeline.md project_docs/plans/2026-08-28-content-llm-consecutive-timeout-design.md
git diff --check
```

Expected: the approved rule appears in all three documents, and `git diff --check` exits 0.

### Task 4: Verify, Commit, Push, And Deploy The Release Baseline

**Files:**
- Verify: `src/briefing/builder.py`
- Verify: `tests/test_brief_builder.py`
- Verify: `AGENTS.md`
- Verify: `project_docs/pipeline.md`

- [ ] **Step 1: Run the complete local verification**

Run:

```powershell
python -m pytest -q
git diff --check
git status --short
```

Expected: the full suite passes; diff check exits 0; status shows only the four implementation files plus the pre-existing untracked `project_docs/plans/2026-08-18-authenticated-x-snapshot.md`, which must remain untouched.

- [ ] **Step 2: Inspect and commit only the approved implementation**

Run:

```powershell
git diff -- src/briefing/builder.py tests/test_brief_builder.py AGENTS.md project_docs/pipeline.md
git add src/briefing/builder.py tests/test_brief_builder.py AGENTS.md project_docs/pipeline.md
git diff --staged --check
git diff --staged
git commit -m "fix(llm): 连续三次超时后熔断"
```

Expected: the staged diff contains only threshold state, tests, and matching documentation; the commit succeeds without bypassing hooks.

- [ ] **Step 3: Push the unique release baseline**

Run:

```powershell
git push origin master
```

Expected: remote `master` fast-forwards through the design and implementation commits.

- [ ] **Step 4: Fast-forward the server and rebuild the service**

Run:

```powershell
ssh root@tankex.xyz "cd /opt/ai-news && git pull --ff-only origin master && docker compose up -d --build --force-recreate"
```

Expected: `/opt/ai-news` fast-forwards to the pushed `master`, the image builds, and Compose recreates the service without printing `.env` values.

- [ ] **Step 5: Verify deployed revision, container health, and threshold constant**

Run:

```powershell
ssh root@tankex.xyz "cd /opt/ai-news && git rev-parse --short HEAD && docker compose ps && docker compose exec -T web python -c 'from src.briefing.builder import _CONSECUTIVE_TIMEOUT_CIRCUIT_THRESHOLD as threshold; assert threshold == 3; print(threshold)'"
```

Expected: server revision equals local `master`, required containers are running/healthy, and the final line is `3`. Do not run a real WeChat draft; a future full safe pipeline run must set `SKIP_WECHAT_DRAFT=1`.
