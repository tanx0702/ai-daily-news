# 项目架构与目录导航

## 工程分类

AI Daily News Agent 是一个单体 Python AI 新闻采编与发布流水线，附带一个轻量 Flask 服务和 Docker/nginx 部署。它不是前后端 Monorepo，也没有外部数据库、消息队列或长期任务服务作为当前主链路的前置依赖；本地 SQLite 只持久化来源健康状态，不保存新闻事实或发布决策。

工程按运行职责分为：

1. 采集层：从 RSS、Hacker News、GitHub、Hugging Face、arXiv 和 X 快照取得候选。
2. 简报层：保留规范来源证据，聚类唯一事件/观点，隔离歧义重复项，生成并核验可显示的事实新闻与署名观点。
3. 内容生产层：调用 LLM 生成中文摘要，解析原文媒体，渲染日报、微信预览和封面。
4. 发布触达层：写入静态日报产物、读取 `latest.json`，创建微信公众号草稿并响应微信客服消息。
5. 运行支撑层：Docker Compose、nginx、cron、运行锁、日志和 shadow/editorial 反馈。
6. 诊断/历史层：v2/shadow/editorial review 和 Tencent SCF 仅供受保护的诊断或兼容，不改变生产简报或决策。

## 目录结构

```text
.
├─ app.py                         Flask 微信回调、健康检查和新闻/审阅接口
├─ config/
│  ├─ rss_sources.json            RSS 源定义
│  └─ x_sources.json              X 账号白名单、来源等级和观点资格
├─ src/
│  ├─ main.py                     生产日报编排入口
│  ├─ collector.py                采集兼容入口、合并、筛选、评分和最终去重
│  ├─ source_state.py             RSS 来源健康状态 SQLite 账本
│  ├─ source_normalization.py      规范来源 URL、发布者和 HN 发现渠道投影
│  ├─ collectors/                 HN/GitHub/HF/arXiv/X 等独立采集器
│  ├─ briefing/                   事实简报配置、聚类、核验、决策和 latest.json schema v2
│  ├─ agents/                     v2/shadow 分析和候选适配代理（仅诊断）
│  ├─ domain/                     诊断和工作流状态模型
│  ├─ services/                   生产快照、审阅、复盘和反馈记录
│  ├─ workflows/                  side-effect-free 诊断工作流
│  ├─ editorial_*.py              历史编辑辅助与受保护诊断
│  ├─ summarizer.py               历史摘要兼容工具，不参与最终核验后的正文修改
│  ├─ media_assets.py              原文配图解析和媒体状态
│  ├─ generator.py                日报 HTML 和微信正文模板
│  ├─ pipeline_artifacts.py       HTML、预览和诊断产物保存
│  ├─ cover.py                    原文图、AI 封面和本地封面降级链
│  ├─ wechat_draft.py             封面上传、正文和公众号草稿创建
│  ├─ llm_config.py               文本、质量和图片模型配置解析
│  ├─ run_guard.py                单次日报运行锁
│  └─ tencent_scf/                历史 Serverless 兼容入口
├─ tests/                         单元、边界、采集器和工作流测试
├─ project_docs/                  面向维护者的中文工程文档
├─ docs/                          nginx 发布的运行时日报、媒体和诊断产物
├─ runtime/                       来源状态账本和 X 本机快照等私有运行时状态
├─ Dockerfile                     Flask/Gunicorn 容器镜像
├─ docker-compose.yml             web + nginx 服务编排
└─ nginx/                         静态文件、反代和 TLS 模板
```

## 依赖方向

生产主链路由 `src/main.py` 编排，依赖采集、事件聚类、事实简报、摘要、媒体、封面、产物和微信模块。采集器只产生候选，不创建发布产物；RSS 每次尝试会把成功、空结果、超时或解析失败写入 `src/source_state.py` 管理的本地 SQLite 账本，并将只读快照附加到采集诊断；该账本不能作为事实证据。X 采集器在候选进入事实/观点分类前过滤转发和推广内容，并只为注册来源传递受信任的 `x_source_name`；规范证据不能接受未受信任候选自报的发布者身份。`src/briefing/` 只接受绑定规范来源证据的唯一事件，并产生唯一的草稿决策；产物模块负责落盘；微信模块只负责公众号 API 边界。

X 的认证请求位于日报进程之外的 VPS 快照 runner。`scripts/twscrape_xclid_compat.py` 只在该 runner 内包装 `twscrape` 的动画索引解析：它受限扫描 `abs.twimg.com` 的直接 legacy bundle，找到内联索引后继续复用上游交易标识算法，未匹配时退回原解析器。账号 Cookie、SQLite 会话和代理不进入生产容器配置或公开产物；生产采集器仍只消费经过 schema 和时效校验的 `x-feed-v1` 文件。

X 候选的内容分类是确定性的，优先级为 `attributed_opinion → fact_event → ai_update`：白名单自然人的原帖还须同时命中明确 AI 主题与实质立场，才归为 `attributed_opinion`；若存在明确硬新闻动作，则保持为 `fact_event`；仅当没有硬新闻动作且正文包含可机械核验的模型/版本、数值或具体技术进展时，才归为 `ai_update`。候选预检和最终 Validator 共用按内容类型分派的来源发布性入口，推广、转发、纯链接和泛评论不得因出现技术词或数字而升级；不满足动态资格的候选继续按 `fact_event` 处理或被前置过滤。

```text
cron / 手动命令
        │
        ▼
src.main._run_pipeline
        ├─ collector + collectors
        ├─ collector + event clustering
        ├─ briefing builder + deterministic validator + DraftDecision
        ├─ workflows / agents（仅 v2/shadow/editorial 诊断）
        ├─ briefing builder + validator（optional quality LLM）
        ├─ media_assets + cover
        ├─ pipeline_artifacts + latest schema v2
        └─ wechat_draft

app.py ──读取──> docs/latest.json
  ├─ /wechat              微信服务器验证和消息回调
  ├─ /health              服务和已保存的 DraftDecision/DraftExecution 状态
  ├─ /api/news            最新新闻 JSON
  └─ /editorial-review*   受 Basic Auth 保护的 shadow 审阅和反馈
```

`app.py` 不重新运行日报采集或 LLM 摘要；它只读取已保存的产物并提供服务端回调/审阅能力。`src/workflows/`、`src/agents/` 和受保护的 editorial review 只保存诊断或反馈，不能修改已接受的简报或 `DraftDecision`。

## 代码边界

- 新采集源放入 `src/collectors/`，并由 `src/collector.py` 统一编排和合并。
- 领域状态和跨模块稳定结构放入 `src/domain/`，不得在 Flask 路由或模板中重复定义。
- 可复用的快照、复盘、反馈和审阅读写放入 `src/services/`。
- 纯编辑流程放入 `src/workflows/`，不得在其中写 HTML、文件、微信或外部 API 副作用。
- 发布判断统一由 `src.briefing.decision.decide_draft()` 产生的 `DraftDecision` 表示；它只接受 `create|block`，不能由来源、模板、人工复核或诊断流程绕过。
- `src/publication.py`、`src/quality_gate.py`、`publication.ready` 和 `quality_state` 属于旧路径，不是生产控制。
- `src/tencent_scf/` 仅用于历史兼容和排查；新 Docker 主流程不得依赖它。
- `docs/` 中的 `index.html`、`latest.json`、封面、`wechat.html`、`archive/`、`debug/` 和 `media/` 都是运行时产物，不是源码文档。
