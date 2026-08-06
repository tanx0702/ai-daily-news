# AI Daily News Agent 文档体系 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将仓库维护说明重构为中文 `AGENTS.md` 导航与硬约束，以及 `project_docs/` 下按采编发布流水线边界拆分的长期维护文档。

**Architecture:** 保留现有单体 Python 生产链路和 Flask 服务，不移动生产代码。创建 `project_docs/` 作为非公开维护文档目录；`AGENTS.md` 只描述项目分类、文档导航、不可违反的边界和验证入口，详细事实分别写入架构、后端、流水线、配置、来源、运维和工作流文档。

**Tech Stack:** Markdown、Python 3.12+、pytest、Docker Compose、Flask、nginx、cron；文档事实来源为 `src/`、`config/`、`.env*.example`、`Dockerfile`、`docker-compose.yml`、`app.py` 和现有测试。

## Global Constraints

- 所有维护文档使用中文；代码标识、环境变量、命令、路径和 API 名称保持原样。
- 本项目分类为单体 Python AI 新闻采编与发布流水线，不引入前后端 Monorepo、数据库或队列架构描述。
- `docs/` 是 nginx 直接发布的运行时产物目录；维护文档只能放在根目录 `project_docs/`。
- 生产主链路是 `python -m src.main`；`app.py` 是 Flask 微信/新闻服务入口。
- X 来源通过 GitHub Runner 生成的 `x-feed.json` 快照接入，不描述为直接调用 X API。
- v2/editorial、shadow 和 Tencent SCF 必须标识为辅助或历史链路，不能写成默认生产路径。
- 不读取、复制或提交真实 `.env`、密钥、日志、媒体产物和日报生成物。
- 完成前必须运行 `git diff --check`、文档路径检查和与本次文档变更相关的测试；未运行的命令不得声称通过。

---

### Task 1: 建立架构与后端约束文档

**Files:**
- Create: `project_docs/architecture.md`
- Create: `project_docs/backend.md`
- Reference: `src/main.py`, `src/collector.py`, `src/collectors/*.py`, `src/agents/*.py`, `src/domain/*.py`, `src/services/*.py`, `src/workflows/*.py`, `app.py`, `tests/`

**Interfaces:**
- Consumes: 当前源码目录和入口模块的职责。
- Produces: 供 `AGENTS.md` 和其他主题文档链接的模块边界、依赖方向和 Python/服务端规则。

- [ ] **Step 1: 编写 `architecture.md`**

记录项目分类和目录树，至少覆盖：

```text
src/main.py                 生产日报编排
src/collector.py            兼容入口与候选合并/筛选
src/collectors/             RSS 之外的 HN/GitHub/HF/arXiv/X 采集器
src/agents/                 v2 分析与编辑辅助代理
src/domain/                 候选、诊断和工作流状态模型
src/services/               快照、复盘、编辑审阅和反馈存取
src/workflows/              side-effect-free 与 production editorial 工作流
src/generator.py            日报 HTML
src/cover.py                封面生成降级链
src/wechat_draft.py         微信草稿创建
app.py                      Flask 微信回调和新闻 API
tests/                      单元、边界和工作流测试
```

明确生产主链路、辅助链路和历史链路，说明依赖方向为“入口编排调用领域/服务/适配器”，采集器不负责发布，Flask 不直接编排日报。

- [ ] **Step 2: 编写 `backend.md`**

按约束而非按框架罗列规则，至少包含：

```text
路由层       只做 HTTP/XML 解析、认证、响应映射和服务调用
采集适配层   负责单一外部来源；超时/HTTP/解析失败返回空候选并记录日志
领域/服务层  负责候选、证据、质量状态、报告和可序列化数据契约
编排层       负责阶段顺序、降级、运行锁、产物保存和退出码
发布适配层   只处理封面上传、正文构建和微信草稿 API
```

写出禁止事项：路由直接调用 LLM/采集/发布；collector 修改发布状态；外部 HTML/XML/用户输入未经安全处理直接拼接；日志泄露 API key/token/password；shadow/editorial 或 Tencent SCF 反向成为生产依赖；单条外部失败中断整条日报；无针对性测试的行为变更。

- [ ] **Step 3: 检查两份文档的交叉引用和事实一致性**

运行：

```powershell
Select-String -Path project_docs\architecture.md,project_docs\backend.md -Pattern 'TODO|TBD|待定'
git diff --check
```

Expected: 无占位词，`git diff --check` 无输出。

- [ ] **Step 4: 提交本任务**

```powershell
git add project_docs\architecture.md project_docs\backend.md
git diff --staged --check
git commit -m "docs: 补充项目架构与后端约束"
```

### Task 2: 编写日报流水线与来源文档

**Files:**
- Create: `project_docs/pipeline.md`
- Create: `project_docs/sources.md`
- Reference: `src/main.py`, `src/collector.py`, `src/quality_gate.py`, `src/editorial_quality.py`, `src/editorial_review.py`, `src/media_assets.py`, `src/cover.py`, `src/pipeline_artifacts.py`, `src/wechat_draft.py`, `src/collectors/x_feed.py`, `config/rss_sources.json`, `config/x_sources.json`

**Interfaces:**
- Consumes: Task 1 的工程边界。
- Produces: 阶段级输入输出、失败行为和全部采集来源的维护说明。

- [ ] **Step 1: 按真实执行顺序编写 `pipeline.md`**

以 `src/main.py::_run_pipeline()` 为事实来源，描述：运行锁与日期；RSS/HN/GitHub/HF/arXiv/X 采集；证据保留与编辑候选注释；来源/主题/独立事件配额；v1/v2_assist；LLM 批量摘要和逐条降级；quality gate high risk 移除与备用回填；跨候选编辑复核；最终事件去重；整期质量评分；原文媒体解析；封面；HTML/微信预览；`latest.json` 和诊断；发布 readiness；微信草稿或 dry run。

明确以下输出规则：HTML/诊断通常继续生成；少于 6 条、来源超过一半、非 `ready` 条目或 LLM 复核失败时不创建草稿并返回非零；`QUALITY_GATE_STRICT` 不再单独决定阻断；单条采集或 API 失败记录后继续。

- [ ] **Step 2: 编写 `sources.md`**

建立来源表并写明入口、开关和降级：

| 来源 | 入口 | 开关/配置 | 关键规则 |
| --- | --- | --- | --- |
| RSS | `config/rss_sources.json` + `src/collector.py` | `DAILY_NEWS_HOURS`、`DAILY_RSS_TIMEOUT` | AI 关键词、日期窗口、去重 |
| Hacker News | `src/collectors/hackernews.py` | `ENABLE_HN_COLLECTOR` | 社区信号、详情超时、低质量降权 |
| GitHub | `src/collectors/github.py` | `ENABLE_GITHUB_COLLECTOR`、`GITHUB_TOKEN` | 项目活跃度只能作信号 |
| Hugging Face | `src/collectors/huggingface.py` | `ENABLE_HF_COLLECTOR`、`HF_TOKEN` | likes/downloads 信号和异常保护 |
| arXiv | `src/collectors/arxiv.py` | `ENABLE_ARXIV_COLLECTOR` | 论文日期和 AI 主题 |
| X | `src/collectors/x_feed.py` | `ENABLE_X_COLLECTOR`、`X_FEED_URL`、`X_FEED_MAX_AGE_HOURS`、`DAILY_X_MAX_ITEMS` | GitHub Runner JSON 快照、`x-feed-v1`、HTTPS/status URL、freshness 校验 |
```

补充 `config/x_sources.json` 的官方/研究/媒体账号分层，并说明 X 采集失败或快照过期只跳过 X，不影响其他来源。

- [ ] **Step 3: 检查流水线和来源文档引用**

运行：

```powershell
Select-String -Path project_docs\pipeline.md,project_docs\sources.md -Pattern 'TODO|TBD|待定'
Select-String -Path project_docs\pipeline.md,project_docs\sources.md -Pattern 'src/main.py|x_feed.py|x_sources.json'
git diff --check
```

Expected: 无占位词，关键事实来源均被引用，无空白 diff 错误。

- [ ] **Step 4: 提交本任务**

```powershell
git add project_docs\pipeline.md project_docs\sources.md
git diff --staged --check
git commit -m "docs: 记录日报流水线与采集来源"
```

### Task 3: 编写配置与运维文档

**Files:**
- Create: `project_docs/configuration.md`
- Create: `project_docs/operations.md`
- Reference: `.env.example`, `.env.advanced.example`, `Dockerfile`, `docker-compose.yml`, `nginx/nginx.conf.template`, `app.py`, `src/run_guard.py`, `src/logger_config.py`, `src/pipeline_artifacts.py`

**Interfaces:**
- Consumes: Task 1/2 的模块、阶段和来源命名。
- Produces: 首次部署、调参、运行诊断和发布操作的中文手册。

- [ ] **Step 1: 编写 `configuration.md`**

按以下分组说明变量名、默认值/继承关系、何时需要覆盖和是否需要重建容器：

```text
核心凭证     LLM_*、IMAGE_*、WECHAT_*、DOMAIN、PAGES_URL
质量模型     QUALITY_LLM_*、ENABLE_LLM_QUALITY_GATE、QUALITY_GATE_STRICT
编辑采集     DAILY_TOP_N、DAILY_CANDIDATE_POOL_N、来源/主题配额、时间窗口、采集开关
安全发布     ENABLE_PUBLISH_SAFETY_FILTER、DAILY_SAFETY_RESERVE_N、SKIP_WECHAT_DRAFT
媒体封面     ENABLE_ARTICLE_IMAGE_FETCH、ENABLE_AI_COVER_GENERATION、COVER_RENDER_MODE、超时/重试
服务诊断     NEWS_DATA_FILE、PORT、APP_TIMEZONE、LOG_DIR、EDITORIAL_REVIEW_*、运行锁
```

明确 `.env.example` 只填 11 项首次部署变量；`.env.advanced.example` 不能直接覆盖 `.env`；修改容器环境后执行 `docker compose up -d --force-recreate`；`AGNES_*`/`OPENAI_*` 仅兼容旧部署，新配置不混用；真实 `.env` 不入库。

- [ ] **Step 2: 编写 `operations.md`**

记录可复制执行的命令：

```powershell
pip install -r requirements.txt
python -m pytest -q
python -m src.main
python app.py
docker compose up -d
docker compose exec -T web python -m src.main
docker compose up -d --force-recreate
```

说明 Docker 的 `web`/`nginx` 服务、`docs/` 与 `logs/` 挂载、cron + `flock`、`/health`、`/api/news`、`/wechat`、编辑审阅认证、`SKIP_WECHAT_DRAFT=1` 干跑、`docs/latest.json`/`debug/shadow`/`wechat.html`/`archive/` 产物，以及不能提交密钥、日志和生成物。

- [ ] **Step 3: 检查命令和路径**

运行：

```powershell
Select-String -Path project_docs\configuration.md,project_docs\operations.md -Pattern 'TODO|TBD|待定'
Test-Path Dockerfile; Test-Path docker-compose.yml; Test-Path nginx\nginx.conf.template
git diff --check
```

Expected: 无占位词，三个部署文件均返回 `True`，无 diff 检查错误。

- [ ] **Step 4: 提交本任务**

```powershell
git add project_docs\configuration.md project_docs\operations.md
git diff --staged --check
git commit -m "docs: 补充配置与运维手册"
```

### Task 4: 编写协作规范并重写 AGENTS.md

**Files:**
- Create: `project_docs/workflow.md`
- Modify: `AGENTS.md`
- Reference: `README.md`, `requirements.txt`, `requirements-dev.txt`, `tests/`, `AGENTS.md` 设计说明

**Interfaces:**
- Consumes: Tasks 1-3 的文档路径、边界、命令和同步规则。
- Produces: 维护者进入任务时的唯一导航和可执行协作规范。

- [ ] **Step 1: 编写 `workflow.md`**

包含：任务开始先读 `AGENTS.md` 与受影响的 `project_docs`；先定位事实来源再改文档/代码；保持提交范围最小；Python 3.12+、`logging`、异常降级和测试约束；针对性 pytest 与全量 pytest 的选择；`git status --short`、`git diff --check`、敏感文件检查；中文 Conventional Commit；完成报告必须列出实际运行命令和结果；新增采集器/配置/质量门禁/发布出口必须同步对应文档。

- [ ] **Step 2: 重写 `AGENTS.md` 为导航和硬约束**

新文件保持中文，使用以下骨架：

```markdown
# AI Daily News Agent 项目导航与执行约束

## 项目定位
单体 Python AI 新闻采编与发布流水线，附带 Flask 微信回调和 Docker/nginx 部署。

## 文档导航
- 架构：project_docs/architecture.md
- 后端约束：project_docs/backend.md
- 流水线：project_docs/pipeline.md
- 配置：project_docs/configuration.md
- 来源：project_docs/sources.md
- 运维：project_docs/operations.md
- 工作流：project_docs/workflow.md

## 生产入口与边界
`python -m src.main` 是日报主入口；`app.py` 是服务入口；`docs/` 是生成产物；v2/editorial 与 Tencent SCF 不是默认主链路。

## 必须遵守
- 外部 API/采集失败必须记录并降级
- 质量门禁和发布 readiness 不得被绕过
- 不提交 `.env`、密钥、日志和生成产物
- 改动采集器、配置、质量门禁、发布或部署时同步文档

## 最小验证
`python -m pytest -q`、`git diff --check`、敏感文件和文档链接检查。
```

保留当前项目确实需要的 Docker 重建、微信草稿发布门槛和封面模式提示；删除重复的完整配置表与阶段实现细节。

- [ ] **Step 3: 验证文档链接和禁提交规则**

运行：

```powershell
$requiredDocs = @(
  'project_docs\architecture.md',
  'project_docs\backend.md',
  'project_docs\pipeline.md',
  'project_docs\configuration.md',
  'project_docs\sources.md',
  'project_docs\operations.md',
  'project_docs\workflow.md'
)
$requiredDocs | ForEach-Object { if (-not (Test-Path $_)) { throw "Missing $_" } }
Select-String -Path AGENTS.md,project_docs\*.md -Pattern 'TODO|TBD|待定'
git diff --check
```

Expected: 所有路径存在，无占位词，无 diff 检查错误。

- [ ] **Step 4: 运行相关测试**

运行：

```powershell
python -m pytest -q tests\test_deployment_config.py tests\test_environment_templates.py tests\test_x_feed_collector.py tests\test_x_web_feed.py tests\test_app.py
python -m pytest -q
```

Expected: 两次命令均成功；若环境依赖或外部服务导致失败，记录实际失败原因，不修改测试结论。

- [ ] **Step 5: 检查最终范围并提交**

运行：

```powershell
git status --short
git diff --stat
git diff --check
git add AGENTS.md project_docs
git diff --staged --check
git commit -m "docs: 重构项目导航与开发规范"
```

Expected: 暂存范围只包含 `AGENTS.md`、`project_docs/` 和本任务需要的文档；不包含 `.env`、`logs/`、日报生成物或无关改动。

## Plan Self-Review

- 设计说明中的全部文档主题均有任务覆盖。
- 后端开发约束作为独立文档纳入导航、目录和同步矩阵。
- X 来源、X 快照时效、生产主链路、shadow/editorial、历史 SCF、封面模式和发布门槛均有明确任务来源。
- 所有任务都有具体文件、命令和预期结果，没有 `TODO`、`TBD` 或“适当处理”类占位步骤。
- 文档只创建/修改维护文档，不改变生产 Python 代码。
