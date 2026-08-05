# X 信息源扩容 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 X 公开信息源从 25 个扩展到 35 个，并把每期 X 候选硬上限提升到 5 条，同时保持现有 RSS、质量门禁、日报和微信发布链路不变。

**Architecture:** 继续使用 GitHub Actions 顺序运行 Playwright 网页探针，向 `x-feed` 分支写入静态 `x-feed-v1` 快照。VPS 只读取快照，继续复用现有 `XFeedCollector`、来源均衡、摘要、质量门禁和发布状态机；本次不引入 X API、数据库、Redis 或并行工作流。

**Tech Stack:** Python 3.12、Playwright 1.52、GitHub Actions、JSON 配置、现有 pytest 测试、Docker Compose。

## Global Constraints

- 只采集公开 X 主页，不使用 X API、Bearer Token、登录态或 Cookie。
- X 快照生成失败或单账号失败不能中断 RSS 日报链路。
- 每期日报最多 5 条 X 候选；不得因候选不足而放宽 X 上限。
- X 内容仍必须经过翻译、证据、编辑复核和质量门禁。
- 不提交 `.env`、密钥、日志、`docs/` 运行产物或探针原始浏览器数据。
- Git 提交信息使用中文。
- 每个任务完成后运行该任务列出的验证命令，再提交该任务的文件。

## 文件变更总览

| 文件 | 责任 |
| --- | --- |
| `config/x_sources.json` | 维护 35 个已验证的公开 X 来源及其 `tier`、`official` 标记。 |
| `src/collector.py` | 本次不修改；现有来源均衡已经通过 `DAILY_X_MAX_ITEMS` 读取每期 X 上限。 |
| `.github/workflows/x-feed.yml` | 将单次顺序探针任务时限从 12 分钟调整为 20 分钟。 |
| `.env.advanced.example` | 将推荐的 X 每期上限示例改为 5。 |
| `tests/test_x_web_feed.py` | 校验生产来源配置的总数和三类层级配额。 |
| `tests/test_x_feed_collector.py` | 校验 5 条 X 上限以及 RSS/X 合并行为不回归。 |
| `tests/test_environment_templates.py` | 校验高级配置示例包含 `DAILY_X_MAX_ITEMS=5`。 |
| `design/plans/2026-08-05-x-source-expansion-plan.md` | 保存本实施计划。 |

## Task 1: Add and Validate the Source Batch

**Files:**
- Modify: `config/x_sources.json`
- Test: `tests/test_x_web_feed.py`

**Interfaces:**
- Consumes: existing `x-sources-v1` JSON schema and `load_x_sources(Path)`.
- Produces: a 35-entry source list with exact tier counts `primary=20`, `research=10`, `media=5`.

- [ ] **Step 1: Add the ten candidate sources.**

  Add these entries while preserving all 25 existing entries and the current schema:

  ```json
  {"name": "xAI", "handle": "xAI", "tier": "primary", "official": true}
  {"name": "Meta AI", "handle": "AIatMeta", "tier": "primary", "official": true}
  {"name": "Google AI", "handle": "GoogleAI", "tier": "primary", "official": true}
  {"name": "Microsoft AI", "handle": "MicrosoftAI", "tier": "primary", "official": true}
  {"name": "Perplexity", "handle": "perplexity_ai", "tier": "primary", "official": true}
  {"name": "ByteDance Seed", "handle": "Seed_Dance", "tier": "primary", "official": true}
  {"name": "Demis Hassabis", "handle": "demishassabis", "tier": "research", "official": false}
  {"name": "Fei-Fei Li", "handle": "drfeifei", "tier": "research", "official": false}
  {"name": "Sebastian Raschka", "handle": "rasbt", "tier": "research", "official": false}
  {"name": "The AI Breakdown", "handle": "TheAIBreakdown", "tier": "media", "official": false}
  ```

  Before committing, run the existing public-page probe against every new handle. A handle is accepted only when its report has `schema_version=x-web-probe-v1`, `tweet_count>0`, and no invalid-target error. If a candidate fails, replace it with the first passing fallback in the same tier and record the replacement in the commit body. The fallback order is: `primary`: Cohere (`cohere`), Stability AI (`StabilityAI`); `research`: Lilian Weng (`lilianweng`), Ilya Sutskever (`ilyasut`); `media`: The Decoder (`TheDecoderAI`), Last Week in AI (`lastweekinai`).

  ```powershell
  python -m scripts.x_web_probe --target-url https://x.com/xAI --output-dir .tmp/x-source-probe/xAI
  python -m scripts.x_web_probe --target-url https://x.com/AIatMeta --output-dir .tmp/x-source-probe/AIatMeta
  python -m scripts.x_web_probe --target-url https://x.com/GoogleAI --output-dir .tmp/x-source-probe/GoogleAI
  python -m scripts.x_web_probe --target-url https://x.com/MicrosoftAI --output-dir .tmp/x-source-probe/MicrosoftAI
  python -m scripts.x_web_probe --target-url https://x.com/perplexity_ai --output-dir .tmp/x-source-probe/perplexity_ai
  python -m scripts.x_web_probe --target-url https://x.com/Seed_Dance --output-dir .tmp/x-source-probe/Seed_Dance
  python -m scripts.x_web_probe --target-url https://x.com/demishassabis --output-dir .tmp/x-source-probe/demishassabis
  python -m scripts.x_web_probe --target-url https://x.com/drfeifei --output-dir .tmp/x-source-probe/drfeifei
  python -m scripts.x_web_probe --target-url https://x.com/rasbt --output-dir .tmp/x-source-probe/rasbt
  python -m scripts.x_web_probe --target-url https://x.com/TheAIBreakdown --output-dir .tmp/x-source-probe/TheAIBreakdown
  ```

- [ ] **Step 2: Add a production-config contract test.**

  In `tests/test_x_web_feed.py`, load `config/x_sources.json` and assert:

  ```python
  assert len(sources) == 35
  assert Counter(item["tier"] for item in sources) == {
      "primary": 20,
      "research": 10,
      "media": 5,
  }
  assert all(item["url"].startswith("https://x.com/") for item in sources)
  assert len({item["handle"].lower() for item in sources}) == 35
  ```

- [ ] **Step 3: Run the focused source tests.**

  Run `python -m pytest tests/test_x_web_feed.py -q`. Expected: all tests pass and the production-config contract test reports 35 sources.

- [ ] **Step 4: Commit the source batch and contract test.**

  ```bash
  git add config/x_sources.json tests/test_x_web_feed.py
  git commit -m "feat: 扩展 X AI 信息源"
  ```

## Task 2: Raise the Daily X Candidate Cap

**Files:**
- Modify: `tests/test_x_feed_collector.py`
- Modify: `.env.advanced.example`
- Modify: `tests/test_environment_templates.py`

**Interfaces:**
- Consumes: `collector._apply_source_balance(items, top_n=10)` and the `DAILY_X_MAX_ITEMS` environment variable.
- Produces: a tested, documented daily cap of 5 without changing the collector function signature.

- [ ] **Step 1: Update the focused cap test.**

  Change the existing test environment from `DAILY_X_MAX_ITEMS=3` to `DAILY_X_MAX_ITEMS=5`, expand its X fixture to 8 items, and assert exactly 5 selected X items. Run `python -m pytest tests/test_x_feed_collector.py::test_source_balance_limits_x_candidates_to_configured_daily_cap -q`; it should pass because the current collector already reads the environment variable. The production behavior changes when the VPS receives the new explicit environment setting, not through a collector code change.

- [ ] **Step 2: Update the recommended configuration example.**

  Change only the commented example in `.env.advanced.example`:

  ```dotenv
  # DAILY_X_MAX_ITEMS=5
  ```

  Do not edit `.env` in Git or any production secret file.

- [ ] **Step 3: Extend the environment-template assertion.**

  In `tests/test_environment_templates.py`, assert that the advanced template contains the exact commented value `DAILY_X_MAX_ITEMS=5` and that the core `.env.example` remains free of advanced collector settings.

- [ ] **Step 4: Run focused cap and template tests.**

  Run `python -m pytest tests/test_x_feed_collector.py tests/test_environment_templates.py -q`. Expected: all focused tests pass and no RSS balance assertions change.

- [ ] **Step 5: Commit the cap and documentation change.**

  ```bash
  git add .env.advanced.example tests/test_x_feed_collector.py tests/test_environment_templates.py
  git commit -m "feat: 提高每期 X 候选上限"
  ```

## Task 3: Extend the GitHub Feed Time Budget

**Files:**
- Modify: `.github/workflows/x-feed.yml`
- Modify: `tests/test_x_web_feed.py`

**Interfaces:**
- Consumes: the existing sequential `python -m scripts.x_web_feed` job.
- Produces: a 20-minute job timeout while preserving the four-hour schedule, `x-feed` branch publication, and no VPS credentials.

- [ ] **Step 1: Add the workflow timeout assertion.**

  Extend `test_x_feed_workflow_publishes_a_scheduled_snapshot_without_vps_access` with an assertion that the workflow contains `timeout-minutes: 20`.

- [ ] **Step 2: Update only the workflow timeout.**

  Change `timeout-minutes: 12` to `timeout-minutes: 20`. Do not change the schedule, concurrency group, permissions, branch name, or publication commands.

- [ ] **Step 3: Run workflow and source tests.**

  Run `python -m pytest tests/test_x_web_feed.py -q`. Expected: all tests pass, including the schedule, timeout, no-VPS-access, and source-config assertions.

- [ ] **Step 4: Commit the workflow budget change.**

  ```bash
  git add .github/workflows/x-feed.yml tests/test_x_web_feed.py
  git commit -m "ci: 延长 X 快照采集时限"
  ```

## Task 4: Full Regression Verification

**Files:**
- No source changes; verify the files committed in Tasks 1-3.

- [ ] **Step 1: Run the complete suite.**

  Run `python -m pytest -q`. Expected: the full suite passes with zero failures.

- [ ] **Step 2: Review the final diff.**

  Run `git diff origin/master...HEAD --check` and `git diff origin/master...HEAD --stat`. Confirm only the source config, X workflow timeout, advanced example, and related tests changed; no `.env`, `docs/`, logs, or browser artifacts are included.

- [ ] **Step 3: Push the implementation branch.**

  ```bash
  git push -u origin codex/x-source-expansion
  ```

## Task 5: GitHub Snapshot Validation

**Files:**
- No repository changes; validate the published `x-feed` snapshot.

- [ ] **Step 1: Trigger the workflow manually.**

  ```bash
  gh workflow run "X Feed" --ref codex/x-source-expansion
  run_id=$(gh run list --workflow x-feed.yml --branch codex/x-source-expansion --limit 1 --json databaseId --jq '.[0].databaseId')
  gh run watch "$run_id" --exit-status
  ```

  Expected: the run completes successfully within 20 minutes and publishes `x-feed/x-feed.json`.

- [ ] **Step 2: Inspect the static snapshot.**

  ```bash
  curl -fsSL https://raw.githubusercontent.com/tanx0702/ai-daily-news/x-feed/x-feed.json \
    | jq '{schema_version, source_count, successful_source_count, failed_source_count, tweet_count, failures}'
  ```

  Expected: `schema_version` is `x-feed-v1`, `source_count` is 35, `tweet_count` is greater than 0, and failures are explicit. Any candidate handle with a probe failure must be removed or replaced in a follow-up commit before deployment.

## Task 6: VPS Rollout and One Real Run

**Files:**
- VPS `/opt/ai-news/.env` only; never commit or copy it to Git.

- [ ] **Step 1: Set the runtime cap without exposing secrets.**

  On the VPS, add or update only `DAILY_X_MAX_ITEMS=5` in `/opt/ai-news/.env`. Preserve all existing model, WeChat, domain, and cover settings.

- [ ] **Step 2: Deploy the merged `master` branch.**

  After the PR is merged, run:

  ```bash
  cd /opt/ai-news
  git checkout master
  git pull --ff-only origin master
  docker compose up -d --force-recreate --no-build
  docker compose ps
  ```

  Expected: `ai-news-web` is healthy and `ai-news-nginx` is running.

- [ ] **Step 3: Run the daily pipeline once.**

  ```bash
  docker compose exec -T web python -m src.main
  ```

  Expected: `docs/latest.json` and debug artifacts are generated. A WeChat draft is created only when the existing publication readiness gate is true; otherwise the run reports the blocking reasons and keeps the HTML daily report.

- [ ] **Step 4: Verify X participation and publication status.**

  Set `REPORT_DATE=$(date -u +%F)` and check `docs/latest.json` for X source counts, `docs/debug/$REPORT_DATE-pipeline.json` for source health and selection IDs, and the latest shadow report for the X candidate count. Confirm the final X count is no greater than 5 and the HTTPS endpoint remains `200`.
