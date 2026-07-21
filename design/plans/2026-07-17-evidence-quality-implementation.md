# Evidence Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a daily WeChat draft while replacing or safely downgrading unsupported text and untrusted visuals item by item.

**Architecture:** Keep immutable source evidence before generation. Review display text against that evidence, select final articles from a larger quota-aware pool, validate media locally, and use a local text-free cover whenever source images are not trustworthy.

**Tech Stack:** Python 3.12, pytest, requests, Pillow, OpenAI-compatible APIs, Jinja2, WeChat Draft API.

## Global Constraints

- Daily HTML and WeChat drafts remain non-blocking; `SKIP_WECHAT_DRAFT=true` is the test-only opt-out.
- Retain `AGNES_*` and `OPENAI_*` compatibility.
- Never overwrite `source_*` fields after collection.
- No mandatory OCR dependency; lack of visual review must choose a safe local fallback.
- Test first for each production behavior and run `python -m pytest -q` before completion.

---

### Task 1: Immutable Evidence And Separate Quality Model

**Files:**
- Create: `src/evidence.py`, `tests/test_evidence.py`
- Modify: `src/collector.py`, `src/llm_config.py`, `src/main.py`, `.env.example`, `README.md`, `AGENTS.md`

**Interfaces:**
- `preserve_source_evidence(item: dict) -> dict`
- `source_evidence_text(item: dict) -> str`
- `resolve_quality_llm_config(...) -> LLMConfig`

- [ ] **Step 1: Write failing tests**

```python
def test_source_summary_survives_generated_summary_replacement():
    item = preserve_source_evidence({"title": "Original", "summary": "Original facts"})
    item["summary"] = "Generated text"
    assert item["source_summary"] == "Original facts"

def test_quality_model_overrides_text_model(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "writer")
    monkeypatch.setenv("QUALITY_LLM_MODEL", "reviewer")
    assert resolve_quality_llm_config().model == "reviewer"
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_evidence.py -q`

Expected: helpers do not exist.

- [ ] **Step 3: Implement minimal evidence/config support**

```python
def preserve_source_evidence(item: dict) -> dict:
    item.setdefault("source_title", str(item.get("title", "")))
    item.setdefault("source_summary", str(item.get("summary", "")))
    item.setdefault("source_url", str(item.get("url", "")))
    item.setdefault("source_name", str(item.get("source", "")))
    return item
```

Call the helper for every candidate before summarization. Add `QUALITY_LLM_*` precedence with fallback to text configuration.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/test_evidence.py tests/test_summarizer_batch_validation.py -q`

Commit: `git add src/evidence.py src/collector.py src/llm_config.py src/main.py tests/test_evidence.py .env.example README.md AGENTS.md && git commit -m "feat: 保留新闻原始证据并拆分质检模型"`

### Task 2: Source Tiers And Candidate Selection

**Files:**
- Create: `src/editorial_selection.py`, `tests/test_editorial_selection.py`
- Modify: `config/rss_sources.json`, `src/collector.py`, `src/main.py`, `tests/test_collector.py`

**Interfaces:**
- `assign_source_tier(item: dict, source_config: dict | None) -> dict`
- `select_editorial_candidates(items: list[dict], target_count: int, pool_size: int) -> tuple[list[dict], list[dict], dict]`

- [ ] **Step 1: Write failing selection tests**

```python
def test_selection_caps_a_publisher_at_two_items():
    selected, _, _ = select_editorial_candidates(_items_from_one_source(), 4, 8)
    assert sum(item["source"] == "A" for item in selected) == 2

def test_selection_retains_primary_or_research_when_available():
    selected, _, _ = select_editorial_candidates(_mixed_tier_items(), 6, 12)
    assert sum(item["source_tier"] in {"primary", "research"} for item in selected) >= 2
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_editorial_selection.py -q`

Expected: selector module does not exist.

- [ ] **Step 3: Implement quota-aware pool selection**

Add tiers to source config and collector-specific sources. Use `DAILY_CANDIDATE_POOL_N=30`, a source cap of two, a topic cap of two, and a primary/research minimum of two when eligible. Return ordered reserves rather than discarding them.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/test_editorial_selection.py tests/test_collector.py tests/test_main_publish_filter.py -q`

Commit: `git add src/editorial_selection.py src/collector.py src/main.py config/rss_sources.json tests/test_editorial_selection.py tests/test_collector.py && git commit -m "feat: 扩充候选池并限制来源重复"`

### Task 3: Evidence-Based Review And Single-Item Replacement

**Files:**
- Modify: `src/summarizer.py`, `src/quality_gate.py`, `src/main.py`
- Create: `tests/test_evidence_quality_gate.py`
- Modify: `tests/test_summarizer_batch_validation.py`, `tests/test_quality_gate_publish_filter.py`

**Interfaces:**
- `review_daily(..., reserves: list[dict]) -> tuple[list[dict], list[dict], dict]`
- Item `quality_state` is `ready`, `replace`, or `source_only`.

- [ ] **Step 1: Write failing evidence-review tests**

```python
def test_quality_input_compares_source_and_generated_summaries():
    payload = _build_llm_input([{"source_summary": "Only a rename.", "summary": "New integrations."}])
    assert payload[0]["original_summary"] == "Only a rename."

def test_unsupported_item_is_replaced_from_reserve():
    final, _, report = review_daily(_unsafe_selected(), reserves=[_safe_reserve()])
    assert final[0]["source_title"] == "Safe reserve"
    assert report["replaced_count"] == 1
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_evidence_quality_gate.py -q`

Expected: current quality input compares the generated summary to itself and reserves are unsupported.

- [ ] **Step 3: Implement state transitions**

Use the quality resolver, record `llm_review_status=passed|failed|skipped`, mark unsupported claims `replace`, mark sparse but topical sources `source_only`, and refill rejected items from reviewed reserves. Return a smaller current list only after reserves are exhausted.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/test_evidence_quality_gate.py tests/test_quality_gate_publish_filter.py tests/test_summarizer_batch_validation.py tests/test_summarizer_highlights.py -q`

Commit: `git add src/summarizer.py src/quality_gate.py src/main.py tests/test_evidence_quality_gate.py tests/test_quality_gate_publish_filter.py tests/test_summarizer_batch_validation.py && git commit -m "fix: 按原始证据质检并单条回填"`

### Task 4: Validated And Unique Article Media

**Files:**
- Modify: `src/media_assets.py`, `src/wechat_draft.py`
- Create: `tests/test_media_assets.py`
- Modify: `tests/test_wechat_boundary.py`

**Interfaces:**
- `validate_media_candidate(url: str, timeout: int) -> dict`
- Trusted media exposes normalized JPEG bytes, hashes, dimensions, and reasons.

- [ ] **Step 1: Write failing media tests**

```python
def test_second_article_with_same_media_hash_becomes_text_only():
    items, _ = resolve_article_media(_two_items_with_same_image(), timeout=1)
    assert items[0]["media_state"] == "trusted"
    assert items[1]["image_type"] == "text_only"

def test_validator_reencodes_supported_image_to_jpeg():
    assert validate_media_candidate("https://example.test/image.webp", 1)["jpeg_bytes"].startswith(b"\xff\xd8")
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_media_assets.py -q`

Expected: media validation is URL-score only.

- [ ] **Step 3: Implement media validation**

Download with a bounded byte limit, decode with Pillow, reject tiny/logo/placeholder candidates, calculate exact and perceptual hashes, re-encode trusted media to JPEG, and prevent reuse across selected items. Upload normalized bytes in the WeChat adapter and report every rejection reason.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/test_media_assets.py tests/test_wechat_boundary.py -q`

Commit: `git add src/media_assets.py src/wechat_draft.py tests/test_media_assets.py tests/test_wechat_boundary.py && git commit -m "fix: 校验去重新闻配图并统一上传格式"`

### Task 5: Safe Cover, Dry Run, And Diagnostics

**Files:**
- Modify: `src/cover.py`, `src/main.py`, `src/pipeline_artifacts.py`, `src/quality_gate.py`
- Create: `tests/test_cover_validation.py`
- Modify: `tests/test_cover_strategy.py`, `tests/test_main_publish_filter.py`, `tests/test_pipeline_artifacts.py`, `.env.example`, `README.md`, `AGENTS.md`

**Interfaces:**
- Covers accept only `media_state="trusted"` candidates and report their source/reasons.
- `SKIP_WECHAT_DRAFT=true` skips only the external WeChat draft API.

- [ ] **Step 1: Write failing cover/dry-run tests**

```python
def test_cover_rejects_reused_or_generic_media():
    subject = select_cover_subject([_item(media_state="rejected")])
    assert subject["mode"] == "fallback"

def test_skip_wechat_draft_preserves_artifact_generation(monkeypatch):
    monkeypatch.setenv("SKIP_WECHAT_DRAFT", "true")
    _run_pipeline()
    mock_publish_daily_article.assert_not_called()
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_cover_validation.py tests/test_main_publish_filter.py -q`

Expected: covers accept URL-score media and skip mode does not exist.

- [ ] **Step 3: Implement safe fallbacks**

Reject untrusted source media for covers, then use the existing local text-free cover. Treat image-provider `400`, `401`, `403`, unsupported-model, and endpoint errors as non-retryable. Always render artifacts; bypass only the external WeChat API when dry run is enabled. Include source health, item states, media outcomes, LLM status, and cover source in artifacts.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/test_cover_strategy.py tests/test_cover_validation.py tests/test_main_publish_filter.py tests/test_pipeline_artifacts.py -q`

Commit: `git add src/cover.py src/main.py src/pipeline_artifacts.py src/quality_gate.py tests/test_cover_validation.py tests/test_cover_strategy.py tests/test_main_publish_filter.py tests/test_pipeline_artifacts.py .env.example README.md AGENTS.md && git commit -m "feat: 支持日报干跑与完整质量诊断"`

### Task 6: Full Verification

**Files:**
- Modify if needed: `design/2026-07-17-daily-news-evidence-quality-design.md`
- Test: all `tests/`

- [ ] **Step 1: Run full suite**

Run: `python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Run a dry integration pipeline**

Run: `SKIP_WECHAT_DRAFT=true ENV=development python -m src.main`

Expected: HTML, preview, latest JSON, and debug reports exist; no WeChat draft request occurs; reports contain source, evidence, media, and fallback states.

- [ ] **Step 3: Commit verification changes**

Commit: `git add design/ tests/ src/ config/ .env.example README.md AGENTS.md && git commit -m "test: 验证日报质量治理完整流程"`
