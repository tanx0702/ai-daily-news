# 来源回退与熔断降级收紧 Implementation Plan

> **For implementation:** follow this plan task by task and verify each task before moving on.

**Goal:** 防止内容 LLM 回退把缺少受控证据的普通英文标题重新放入最终日报，同时保留已验证的中文原文回退和明确的技术故障审计原因。

**Architecture:** 在 `src/briefing/publishability.py` 的 `source_anchored_title()` 收紧来源回退的受控英文 token；`src/briefing/builder.py` 继续通过该函数进入既有构建/验证/审计链路。保持 `src/briefing/validator.py` 和 `src/pipeline.py` 的最终门禁与 DraftDecision 逻辑不变，不新增外部调用或配置项。

**Tech Stack:** Python 3.12+, pytest, 现有 briefing builder/validator 审计模型。

### Task 1: Add regression tests for fallback boundaries

**Files:** `tests/test_brief_builder.py`, `tests/test_publishability.py`

- Add a failing test proving a descriptive English token is not a controlled fallback detail.
- Add a failing test proving a second rebuild does not create a source fallback from that title.
- Preserve the current per-attempt audit reason behavior and the independent `source_fallback_used` flag.

### Task 2: Tighten source fallback acceptance

**Files:** `src/briefing/publishability.py`

- Reuse the existing deterministic entity/action/detail checks and remove only the broad token path that admits a descriptive English adjective.
- Keep documented product/model/organization, `@handle`, number/version, and explicit source-title anchors available.
- Ensure rejected fallback candidates do not become valid brief items while each build attempt retains its own technical failure reason.

### Task 3: Synchronize production documentation

**Files:** `project_docs/pipeline.md`, `project_docs/backend.md`

- Document the tightened fallback boundary and the distinction between technical failure reasons and `source_fallback_used`.
- Do not change item-count targets, X quotas, retry budgets, or DraftDecision rules.

### Task 4: Verify, commit, deploy, and run

- Run focused builder/validator tests, then `python -m pytest -q`, `git diff --check`, and staged-diff inspection.
- Commit the fallback fix with a Chinese Conventional Commit message, push `master` to `origin`, and fast-forward the server deployment.
- Confirm services and locks, then run two strictly serial `SKIP_WECHAT_DRAFT=1` production tasks; stop after the first failure and report its logs.

**Verification:** The focused regression tests must pass; the full suite must pass with only the known baseline warning; server runs must produce `DraftExecution=dry_run`, release the run lock, and return exit code 0 for both rounds.
