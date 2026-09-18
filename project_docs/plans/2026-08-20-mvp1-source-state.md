# MVP-1 Source State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** Add a persistent RSS source-health ledger and a small set of verified AI feeds so the daily candidate pool can be diagnosed and strengthened without weakening the publication gate.

**Architecture:** Keep `src.collector` as the production entry point. A new SQLite-backed `SourceStateStore` records one sanitized latest status per configured RSS source; `_fetch_source` records success, empty, timeout, HTTP, invalid-feed, and fallback outcomes. `collect_candidates` exposes the state snapshot through existing diagnostics. X, LLM, `DraftDecision`, and WeChat execution remain unchanged.

**Tech Stack:** Python 3.12+, `sqlite3`, `feedparser`, `requests`, existing pytest suite, JSON source registry, Docker runtime volume.

## Global Constraints

- Production runs use Python 3.12+ and the standard `logging` module.
- Every external RSS failure is logged and degrades to other sources; one source cannot abort the run.
- `DraftDecision` remains the only `create|block` decision and fewer than five valid briefs still blocks.
- X remains a snapshot input and is not made responsible for the minimum item count.
- Do not commit `.env`, credentials, logs, `docs/` runtime output, or the SQLite runtime database.
- Every source/config change updates `project_docs/sources.md`, `project_docs/architecture.md`, `project_docs/configuration.md`, and `project_docs/operations.md` as applicable.

---

### Task 1: Source-state ledger

**Files:**
- Create: `src/source_state.py`
- Create: `tests/test_source_state.py`
- Modify: `.gitignore`
- Modify: `.env.advanced.example`
- Modify: `tests/test_environment_templates.py`

**Interfaces:**
- `SourceStateStore(path: str)` creates the parent directory and SQLite schema.
- `SourceStateStore.record(...)` upserts a source's latest status and preserves a consecutive failure count.
- `SourceStateStore.snapshot()` returns JSON-serializable source-health dictionaries.
- `SourceStateStore.from_environment()` reads `SOURCE_STATE_DB_PATH`, defaulting to `runtime/source-state.db`.

- [ ] **Step 1: Write failing tests**

```python
def test_source_state_records_success_and_resets_failure_streak(tmp_path):
    store = SourceStateStore(str(tmp_path / "state.db"))
    store.record("OpenAI Blog", "https://example.test/feed", status="error", item_count=0, latency_ms=20, error="timeout")
    store.record("OpenAI Blog", "https://example.test/feed", status="success", item_count=3, latency_ms=40, content_hash="abc")
    row = store.snapshot()["OpenAI Blog"]
    assert row["status"] == "success"
    assert row["consecutive_failures"] == 0
    assert row["last_item_count"] == 3
    assert row["last_content_hash"] == "abc"

def test_source_state_preserves_failure_streak_and_is_serializable(tmp_path):
    store = SourceStateStore(str(tmp_path / "state.db"))
    for _ in range(2):
        store.record("Feed", "https://example.test/feed", status="timeout", item_count=0, latency_ms=100, error="timeout")
    row = store.snapshot()["Feed"]
    assert row["consecutive_failures"] == 2
    assert isinstance(row["last_attempt_at"], str)
```

- [ ] **Step 2: Run the focused tests and verify the expected missing-import failure**

Run: `python -m pytest -q tests/test_source_state.py`

Expected: FAIL because `src.source_state.SourceStateStore` does not exist yet.

- [ ] **Step 3: Implement the minimal SQLite store**

Use `sqlite3`, `datetime.now(timezone.utc).isoformat()`, one `source_health` table keyed by `(source_name, source_url)`, `INSERT ... ON CONFLICT DO UPDATE`, and `json`-serializable primitive values only. Treat `success` and `not_modified` as healthy statuses that reset `consecutive_failures`; all other statuses increment it.

- [ ] **Step 4: Run focused tests and environment-template tests**

Run: `python -m pytest -q tests/test_source_state.py tests/test_environment_templates.py`

Expected: PASS.

- [ ] **Step 5: Commit the ledger**

```bash
git add src/source_state.py tests/test_source_state.py .gitignore .env.advanced.example tests/test_environment_templates.py
git commit -m "feat: 增加 RSS 来源状态账本"
```

### Task 2: Integrate RSS health recording

**Files:**
- Modify: `src/collector.py`
- Modify: `tests/test_collector.py`
- Modify: `tests/test_collector_diagnostics.py`

**Interfaces:**
- `_fetch_source(source, timeout, state_store=None)` keeps returning `list[dict]`.
- `_fetch_raw_candidates(..., source_health=None)` fills the supplied mapping and continues returning `list[dict]`.
- Existing `_fetch_single(name, url, timeout)` behavior remains compatible; an internal outcome helper may carry status, latency, and content hash.

- [ ] **Step 1: Write failing tests**

```python
def test_fetch_source_records_success_and_item_count(tmp_path, monkeypatch):
    store = SourceStateStore(str(tmp_path / "state.db"))
    response = FakeResponse(b"<rss><channel><item><title>OpenAI releases Model 5</title><link>https://openai.com/model-5</link></item></channel></rss>")
    monkeypatch.setattr(collector.requests, "get", lambda *args, **kwargs: response)
    items = collector._fetch_source({"name": "OpenAI", "url": "https://example.test/feed", "tier": "primary"}, 5, store)
    assert len(items) == 1
    assert store.snapshot()["OpenAI"]["status"] == "success"
    assert store.snapshot()["OpenAI"]["last_item_count"] == 1

def test_collect_candidates_exposes_source_health_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setenv("SOURCE_STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(collector, "_load_sources", lambda _path: [])
    monkeypatch.setattr(collector, "_fetch_x", lambda _timeout: [])
    diagnostics = {}
    collector.collect_candidates(limit=0, hours=36, diagnostics=diagnostics)
    assert diagnostics["source_health"] == {}
```

- [ ] **Step 2: Run the focused tests and verify the integration failure**

Run: `python -m pytest -q tests/test_collector.py tests/test_collector_diagnostics.py`

Expected: FAIL because `_fetch_source` does not accept/record a state store and diagnostics has no `source_health` field.

- [ ] **Step 3: Implement integration**

Create one `SourceStateStore.from_environment()` per collection run, pass it only to RSS fetching, record each original source once after trying its configured URL and any existing China fallback, and add `source_health` to `collect_candidates` diagnostics. Close the SQLite connection in a `finally` block. Keep all collector exceptions as logged empty results.

- [ ] **Step 4: Run focused collector tests**

Run: `python -m pytest -q tests/test_source_state.py tests/test_collector.py tests/test_collector_diagnostics.py`

Expected: PASS.

- [ ] **Step 5: Commit integration**

```bash
git add src/collector.py tests/test_collector.py tests/test_collector_diagnostics.py
git commit -m "feat: 记录 RSS 来源健康诊断"
```

### Task 3: Expand verified RSS inputs and documentation

**Files:**
- Modify: `config/rss_sources.json`
- Modify: `tests/test_rss_sources.py`
- Modify: `project_docs/sources.md`
- Modify: `project_docs/architecture.md`
- Modify: `project_docs/configuration.md`
- Modify: `project_docs/operations.md`

**Interfaces:**
- Add only feeds verified to return RSS/Atom responses at implementation time: Hugging Face Blog, MIT Technology Review AI topic, and Ars Technica AI.
- Keep `tier` explicit for every new source; no source bypasses publishability preflight.

- [ ] **Step 1: Write failing source-config assertions**

Add assertions for the three source names, exact URLs, and `tier` values (`primary` for Hugging Face Blog; `media` for the two independent reporting feeds).

- [ ] **Step 2: Run the config tests and verify they fail**

Run: `python -m pytest -q tests/test_rss_sources.py`

Expected: FAIL because the three sources are not present.

- [ ] **Step 3: Add the feeds and document the state ledger**

Add the exact verified URLs, explain that RSS is the minimum-count fact plane, and document `SOURCE_STATE_DB_PATH=runtime/source-state.db`, its fields, and the diagnostic interpretation.

- [ ] **Step 4: Run config and documentation-adjacent tests**

Run: `python -m pytest -q tests/test_rss_sources.py tests/test_environment_templates.py tests/test_deployment_config.py`

Expected: PASS.

- [ ] **Step 5: Commit source expansion**

```bash
git add config/rss_sources.json tests/test_rss_sources.py project_docs/sources.md project_docs/architecture.md project_docs/configuration.md project_docs/operations.md
git commit -m "feat: 扩充稳定 RSS 事实来源"
```

### Task 4: Full verification and delivery

**Files:**
- No new application files; inspect all staged changes.

- [ ] **Step 1: Run the complete test suite**

Run: `python -m pytest -q`

Expected: all tests pass with no baseline regressions.

- [ ] **Step 2: Run repository checks**

Run: `git diff --check` and `git status --short`.

Expected: no whitespace errors; only intentional source, test, config, documentation, and plan files are changed; no runtime database or credentials are staged.

- [ ] **Step 3: Fast-forward `master`**

Merge the tested feature branch into local `master`, push `origin/master`, and remove the temporary worktree/branch only after deployment verification.

- [ ] **Step 4: Deploy and validate safely**

On the server: pull `master`, rebuild the `web` image because source files are copied into the image, run `docker compose up -d --force-recreate`, verify `/health`, then run `SKIP_WECHAT_DRAFT=1 python -m src.main`. Confirm `latest.json` contains `diagnostics.collection.source_health`, and that a `block` remains non-publishing when fewer than five valid items exist.
