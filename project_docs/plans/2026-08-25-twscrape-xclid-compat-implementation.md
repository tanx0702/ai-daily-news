# twscrape XClId Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore authenticated X user and timeline queries when X serves direct legacy bundles with inline transaction-ID animation indices.

**Architecture:** Add one dependency adapter under `scripts/` that wraps `twscrape.xclid.parse_anim_idx`, scans only trusted `abs.twimg.com` bundles for the existing upstream index pattern, and delegates to the original parser when the compatibility path does not apply. Install it only in the authenticated snapshot runner before constructing `twscrape.API`.

**Tech Stack:** Python 3.12, `twscrape==0.20.0`, asyncio, pytest, Docker, SSH.

---

### Task 1: Lock the current X page shape in regression tests

**Files:**
- Create: `tests/test_twscrape_xclid_compat.py`
- Create: `scripts/twscrape_xclid_compat.py`

- [ ] **Step 1: Write failing extraction and parser tests**

Add fixtures containing the current direct `vendor`, `i18n`, and `main` URLs. Assert that only
`abs.twimg.com/responsive-web/client-web/*.js` is accepted, `main` is fetched first, the two-capture-group
index regex yields integers, and no match delegates to the original parser.

- [ ] **Step 2: Verify the focused tests fail**

Run: `python -m pytest -q tests/test_twscrape_xclid_compat.py`

Expected: collection fails because `scripts.twscrape_xclid_compat` does not exist.

- [ ] **Step 3: Implement the minimal adapter**

Implement these interfaces without importing `twscrape` at module import time:

```python
def extract_direct_legacy_assets(html: str) -> list[str]: ...

def build_compatible_parser(
    original_parser,
    get_page_text,
    indices_regex,
): ...

def install_twscrape_xclid_compat() -> None: ...
```

The generated async parser scans trusted direct assets for `match.group(2)`, returns the first non-empty
index list, and otherwise awaits `original_parser(html, client)`. The installer is idempotent and replaces
only `twscrape.xclid.parse_anim_idx`.

- [ ] **Step 4: Verify the focused tests pass**

Run: `python -m pytest -q tests/test_twscrape_xclid_compat.py`

Expected: all compatibility tests pass.

### Task 2: Install the adapter in the authenticated runner

**Files:**
- Modify: `scripts/x_authenticated_feed.py`
- Modify: `tests/test_x_authenticated_feed.py`

- [ ] **Step 1: Write a failing installer-order test**

Patch the adapter installer and fake `twscrape.API`; assert `_build_twscrape_client` installs compatibility
before constructing the API with the existing database, proxy, timeout and wait settings.

- [ ] **Step 2: Run the runner tests and confirm failure**

Run: `python -m pytest -q tests/test_x_authenticated_feed.py`

Expected: the new assertion fails because the installer is not called.

- [ ] **Step 3: Install compatibility before API construction**

Call `install_twscrape_xclid_compat()` in `_build_twscrape_client` immediately after importing `API` and
before returning the client. Do not change snapshot schema, account database, proxy or collection rules.

- [ ] **Step 4: Run both focused suites**

Run: `python -m pytest -q tests/test_twscrape_xclid_compat.py tests/test_x_authenticated_feed.py`

Expected: both suites pass.

### Task 3: Synchronize source-boundary documentation

**Files:**
- Modify: `project_docs/sources.md`
- Modify: `project_docs/architecture.md`

- [ ] **Step 1: Document the compatibility boundary**

State that the VPS runner applies a narrow, removable XClId adapter for trusted direct legacy bundles,
reuses the upstream transaction-ID algorithm, and falls back to upstream parsing on non-matching pages.
State that credentials remain only in the root-owned server database and X still enters production only as
an `x-feed-v1` snapshot.

- [ ] **Step 2: Check documentation and source diffs**

Run: `git diff --check`

Expected: exit code 0.

### Task 4: Verify, publish and deploy

**Files:**
- No additional source files.

- [ ] **Step 1: Run repository validation**

Run: `python -m pytest -q`

Expected: all tests pass, or any pre-existing unrelated failure is recorded explicitly.

Run: `git diff --check`

Expected: exit code 0.

Run: `git status --short`

Expected: only this task's files are modified in the isolated worktree.

- [ ] **Step 2: Commit the reviewed scope**

Inspect `git diff --staged`, then commit with:

```text
fix(x): 兼容内联交易标识索引
```

- [ ] **Step 3: Merge and push the release baseline**

Merge the temporary branch into `master` with an English `Merge` commit, push `master`, then remove the
temporary remote branch if one was created. Preserve unrelated user files in the primary worktree.

- [ ] **Step 4: Verify on the server without publishing**

Pull `master`, query `OpenAI`, run the complete authenticated snapshot, inspect only counts and failure
reasons, then run the daily pipeline with `SKIP_WECHAT_DRAFT=1`. Success requires a non-empty X snapshot;
the dry run must not call the real WeChat draft API.
