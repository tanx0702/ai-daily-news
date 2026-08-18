# X Source Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the controlled X source registry from 35 to 51 non-duplicated accounts without weakening production quality rules.

**Architecture:** Keep `config/x_sources.json` as the only source registry. Add eight `research` and eight `media` accounts, all with `official=false`; the existing snapshot producer and collector consume the registry without code changes.

**Tech Stack:** JSON configuration, Python 3.12, pytest.

## Global Constraints

- Do not change the 20 existing primary official sources.
- Do not change X selection caps, snapshot freshness, or fact-quality gates.
- Keep all newly added accounts non-official.
- Update `project_docs/sources.md` with the source-selection boundary.

---

### Task 1: Expand and verify the source registry

**Files:**
- Modify: `tests/test_x_web_feed.py`
- Modify: `config/x_sources.json`
- Modify: `project_docs/sources.md`

**Interfaces:**
- Consumes: `load_x_sources(Path) -> list[dict]`
- Produces: 51 unique configured X sources with tier distribution `20/18/13`

- [x] **Step 1: Write the failing registry contract test**
- [x] **Step 2: Run the focused test and confirm it fails with 35 sources**
- [ ] **Step 3: Add the 16 approved accounts with `official=false`**
- [ ] **Step 4: Document the expansion boundary**
- [ ] **Step 5: Run focused and full tests**
- [ ] **Step 6: Commit, merge to master, deploy, and verify server loading**
