# AI Daily News Agent 文档体系设计

## 状态

已获确认，待用户审阅后实施。

## 目标

将仓库说明从一份逐渐过期的综合 `AGENTS.md`，重构为“顶层导航与硬约束 + 中文分主题文档”的维护体系。文档必须准确反映当前项目是一个单体 Python AI 新闻采编与发布流水线，而不是前后端 Monorepo。

本次只调整维护文档，不移动、重命名或重构生产代码。

## 读者与原则

- 主要读者是维护仓库的开发者、部署者和自动化代码代理。
- 顶层 `AGENTS.md` 只保留进入任务前必须知道的文档入口、架构边界、敏感文件规则和验证要求。
- 事实说明按维护边界拆分，避免所有模块、配置和部署细节再次堆积在一个文件中。
- 所有面向维护者的说明使用中文；代码标识、环境变量、命令、路径和 API 名称保持原样。
- 运行时生成的 `docs/` 目录与维护文档分离，避免文档被 nginx 当作日报站点内容发布。
- 文档描述生产主链路、v2/editorial 辅助链路和历史链路的边界，不把实验代码描述成默认生产行为。

## 工程分类

当前项目的工程分类为：

1. 采集层：RSS、Hacker News、GitHub、Hugging Face、arXiv 和 X 快照采集器。
2. 编辑决策层：证据保留、候选注释、来源/主题配额、事件归并、编辑复核和质量门禁。
3. 内容生产层：LLM 翻译摘要、HTML 日报、原文媒体、封面和诊断报告。
4. 发布触达层：静态日报、微信公众号草稿和微信客服回调。
5. 运行支撑层：Docker Compose、nginx、cron、运行锁、日志和 shadow/editorial 反馈。
6. 历史/实验层：v2 assist 工作流和 Tencent SCF 兼容代码，不默认等同于生产主链路。

## 文档目录

```text
AGENTS.md                         顶层导航、硬约束和任务前检查
project_docs/
  architecture.md                 目录、模块边界、依赖方向和工程分类
  backend.md                      Python 服务端与流水线开发约束
  pipeline.md                     日报生产数据流、阶段输入输出和降级行为
  configuration.md                核心/高级环境变量、默认值和密钥规则
  sources.md                      RSS、HN、GitHub、HF、arXiv、X 来源与开关
  operations.md                   本地运行、Docker、cron、Flask、微信和诊断产物
  workflow.md                     开发、测试、提交和文档同步规范
```

### `AGENTS.md`

必须包含：

- 项目一句话定位和工程分类。
- 文档导航和阅读顺序。
- 生产代码、辅助链路、历史代码和运行时产物的边界。
- 采集/API 失败的容错原则、质量门禁和发布安全约束。
- `.env`、密钥、日志和 `docs/` 生成物不可提交的规则。
- 变更采集器、配置、质量门禁、发布出口或部署时必须同步更新的文档。
- 最小验证命令和报告要求。

不得在 `AGENTS.md` 中重复完整环境变量表、完整流水线实现细节或每个测试文件说明。

### `project_docs/architecture.md`

描述单体 Python 工程的目录树和职责边界，覆盖 `src/collectors`、`src/agents`、`src/domain`、`src/services`、`src/workflows`、顶层编排模块、Flask 入口、配置和测试。明确 `src/main.py` 是生产编排入口，`app.py` 是微信/新闻服务入口，并标识历史 Tencent SCF 代码。

### `project_docs/backend.md`

定义本项目的服务端和 Python 开发约束。内容包括：采集器、领域模型、服务、工作流和编排层的依赖方向；`app.py` 路由只负责协议转换和调用服务；外部 RSS、X feed、LLM、图片和微信 API 必须经过明确的适配边界；跨模块数据使用稳定字段和可序列化结构；网络/API 失败必须记录并降级而不是无日志吞异常；日志不得泄露密钥；时间统一使用项目时区/UTC 约定；生产主链路不能被 shadow/editorial 或历史 SCF 代码反向依赖；新增行为必须有针对性测试。

该文档还应列出明确禁止事项：在路由中直接写采集、LLM、文件发布或微信 API 逻辑；在 collector 中修改发布状态；以 GitHub 活跃度替代正式发布证据；直接拼接外部 HTML/用户输入进入 XML 或正文；把真实 `.env`、响应密钥或完整外部响应写入日志；为解决单次 API 失败而中断整条日报流水线。

### `project_docs/pipeline.md`

以 `src/main.py` 为事实来源，记录采集、来源证据、候选编辑、LLM 摘要、质量门禁、跨候选复核、媒体解析、封面、HTML、`latest.json`、诊断报告和微信草稿的阶段顺序。记录 high risk 回填、发布门槛、LLM 失败降级、运行锁和非零退出条件。

### `project_docs/configuration.md`

区分 `.env.example` 的首次部署配置和 `.env.advanced.example` 的可选覆盖。按文本 LLM、质量 LLM、图片、采集/编辑、质量/发布、媒体、调试/服务分组，说明默认行为、覆盖方式、容器重建要求和旧别名兼容边界。不得复制真实密钥。

### `project_docs/sources.md`

记录 `config/rss_sources.json` 和 `config/x_sources.json` 的职责。覆盖 RSS、HN、GitHub、Hugging Face、arXiv 和 X feed 的采集入口、环境变量开关、时间窗口、失败隔离、X 快照的 GitHub Runner 来源、schema/freshness 校验和候选上限。明确 X 不是直接调用 X API。

### `project_docs/operations.md`

记录本地开发命令、Docker Compose 服务、cron 与 flock、Flask 健康检查和微信回调、静态日报产物、shadow/editorial 诊断入口、干跑方式、日志位置以及安全发布注意事项。把 `docs/` 定义为运行时产物目录，并说明哪些文件可以临时检查、哪些不能提交。

### `project_docs/workflow.md`

记录任务开始前的阅读顺序、变更范围控制、测试选择、`pytest` 命令、`git diff --check`、敏感文件检查、中文 Conventional Commit 约定和文档同步矩阵。完成任务时必须报告实际执行的验证命令和结果，不能把未运行的检查说成通过。

## 文档同步矩阵

| 变更类型 | 必须同步 |
| --- | --- |
| 新增/移除采集源或采集器 | `project_docs/sources.md`、`project_docs/architecture.md`、必要时 `configuration.md` |
| 修改主流程阶段、降级或发布门槛 | `project_docs/pipeline.md`、`AGENTS.md` |
| 新增/修改环境变量 | `project_docs/configuration.md`、对应 `.env*.example`、必要时 `operations.md` |
| 修改 Docker、nginx、cron、Flask 或微信边界 | `project_docs/operations.md`、`AGENTS.md` |
| 新增 domain/service/workflow/agent 模块 | `project_docs/architecture.md`、必要时 `backend.md` |
| 修改 Python 分层、Flask 路由、外部 API 适配或错误/日志约束 | `project_docs/backend.md`、`AGENTS.md` |
| 修改测试、提交或协作要求 | `project_docs/workflow.md`、`AGENTS.md` |

## 非目标

- 不把维护文档放入 nginx 公开的日报产物中。
- 不删除、移动或重构现有 Python 模块。
- 不修改 `.env`、真实密钥、日志、封面或其他 `docs/` 生成产物。
- 不将 v2 assist、shadow/editorial 或 Tencent SCF 宣传为默认生产主链路。
- 不复制参考项目的 React、FastAPI、数据库、队列或 Monorepo 规则。

## 验收标准

- 新 `AGENTS.md` 能在几分钟内告诉维护者应该先读哪些文档、生产入口是什么、哪些文件不能碰。
- `project_docs/` 中的每个主题都能从当前代码和配置找到对应事实来源。
- X 来源及其快照工作流、最新服务/工作流/领域模块、质量门禁和封面模式均有明确说明。
- 文档不会把运行时 `docs/` 产物和维护文档混淆。
- `python -m pytest -q`、`git diff --check` 和文档链接/路径检查的实际结果在实施完成后可复核。
