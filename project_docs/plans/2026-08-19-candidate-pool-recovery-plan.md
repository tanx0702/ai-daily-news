# Candidate Pool Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让确定性可发布候选在截断前优先进入候选池，并修复 GitHub、Hugging Face 和 arXiv 的来源证据与降级行为。

**Architecture:** 复用现有 `SourceEvidence` 和 `validate_source_publishability()`，在 `collect_candidates()` 内增加无 LLM 的预检分组和诊断，再按组内原评分排序回填候选池。来源适配器只输出符合自身证据契约的候选，外部故障继续记录并降级。

**Tech Stack:** Python 3.12、pytest、requests、feedparser、Docker Compose。

## Global Constraints

- 不放宽 5-15 条、规范来源证据、语义去重和 `DraftDecision` 门禁。
- 外部来源失败必须记录并降级，不能中断整期日报。
- GitHub 活跃度和 Hugging Face 热度不能写成正式发布证据。
- `SKIP_WECHAT_DRAFT=1` 是服务器验证的唯一安全干跑边界。
- 修改采集器、配置或运维行为必须同步 `project_docs/`。

---

### Task 1: 候选池发布性预检与回填

**Files:**
- Modify: `src/collector.py`
- Test: `tests/test_collector.py`

**Interfaces:**
- Consumes: `source_evidence_from_candidate(candidate) -> SourceEvidence | None`、`validate_source_publishability(source) -> PublishabilityResult`
- Produces: `collect_candidates(..., diagnostics=...)` 新增 `publishability_preflight_total`、`publishability_preflight_passed`、`publishability_preflight_rejected`、`publishability_preflight_invalid_evidence`、`publishability_preflight_reason_counts`

- [ ] 写失败测试：构造高分 `non_news_content` 和较低分可发布候选，断言 `limit=1` 返回可发布候选。
- [ ] 运行 `python -m pytest tests/test_collector.py -k publishability_preflight -q`，确认因现有热度截断行为失败。
- [ ] 在 `collect_candidates()` 中保存证据后执行确定性预检，分别排序通过和拒绝组，优先从通过组填充结果。
- [ ] 增加诊断断言，确认原因计数和无效证据不会静默丢失。
- [ ] 重跑目标测试并确认通过。

### Task 2: GitHub 与 Hugging Face 来源契约

**Files:**
- Modify: `src/collectors/github.py`
- Modify: `src/collectors/huggingface.py`
- Test: `tests/test_github_collector.py`
- Test: `tests/test_huggingface_collector.py`

**Interfaces:**
- Produces: GitHub release 标题 `<repo> releases <tag>`；HF 活跃度候选带不可发布的明确事件语义，不伪装成 release。

- [ ] 写失败测试：GitHub release 候选通过 `validate_source_publishability()`。
- [ ] 运行 GitHub 目标测试并确认因标题缺少 release 动作失败。
- [ ] 最小修改 GitHub release 标题并使测试通过。
- [ ] 写失败测试：只有 `lastModified`、点赞和下载量的 HF 模型不能通过发布性预检。
- [ ] 运行 HF 目标测试，确认失败原因与活跃度被当作发布候选有关。
- [ ] 添加明确的 HF 活跃度证据标记，由通用预检稳定拒绝，并重跑目标测试。

### Task 3: arXiv 有界重试

**Files:**
- Modify: `src/collectors/arxiv.py`
- Test: `tests/test_arxiv_collector.py`

**Interfaces:**
- Produces: arXiv GET 最多两次；超时、429、5xx 可重试，其它 4xx 直接降级；最终仍失败返回 `[]`。

- [ ] 写失败测试：第一次超时、第二次成功时返回论文候选并调用两次。
- [ ] 写失败测试：连续两次超时返回空列表且只调用两次。
- [ ] 运行目标测试并确认当前单次请求行为失败。
- [ ] 实现最多一次重试和短退避，保持原异常日志与空列表降级契约。
- [ ] 重跑 arXiv 测试并确认通过。

### Task 4: 文档、完整验证与部署

**Files:**
- Modify: `project_docs/pipeline.md`
- Modify: `project_docs/sources.md`
- Modify: `project_docs/operations.md`
- Modify: `.env.advanced.example` only if a new configuration is introduced

**Interfaces:**
- Produces: 与实际预检、来源契约、重试和服务器干跑一致的维护文档。

- [ ] 同步候选预检/回填、GitHub/HF 证据边界和 arXiv 降级说明。
- [ ] 运行 `python -m pytest -q`。
- [ ] 运行 `git diff --check` 和 `git status --short`，区分用户已有未跟踪文件。
- [ ] 检查 `git diff --staged` 后以中文 Conventional Commit 提交并推送 `master`。
- [ ] 服务器拉取 `master`，将 `DAILY_NEWS_HOURS` 改为 `36`，重建容器。
- [ ] 临时以 `SKIP_WECHAT_DRAFT=1` 执行 `python -m src.main`，验证不少于 5 条且未调用微信草稿 API。
- [ ] 恢复生产容器配置，检查 `/health`、容器状态和最新诊断。
