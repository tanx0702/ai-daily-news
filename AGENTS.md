# AI Daily News Agent 项目导航与执行约束

本仓库是一个单体 Python AI 新闻采编与发布流水线，附带 Flask 微信/新闻服务和 Docker/nginx 部署。面向维护者的说明使用中文；代码标识、文件名、环境变量、API 字段和命令保持原样。

## 文档导航

开始任务前先读本文件，再按变更范围阅读：

- 项目结构和依赖边界：[project_docs/architecture.md](project_docs/architecture.md)
- Python/服务端约束：[project_docs/backend.md](project_docs/backend.md)
- 日报生产阶段与降级：[project_docs/pipeline.md](project_docs/pipeline.md)
- 环境变量和密钥规则：[project_docs/configuration.md](project_docs/configuration.md)
- RSS、HN、GitHub、HF、arXiv、X 来源：[project_docs/sources.md](project_docs/sources.md)
- 本地、Docker、cron、Flask 和诊断：[project_docs/operations.md](project_docs/operations.md)
- 测试、提交和文档同步：[project_docs/workflow.md](project_docs/workflow.md)

## 工程边界

```text
cron / 手动命令 -> python -m src.main -> 采集/编辑/摘要/质量/媒体/封面/产物/微信草稿
                                      -> docs/index.html、latest.json、cover.jpg、wechat.html、archive/

nginx -> 静态发布 docs/，并反代 /wechat、/health、/api/ 和 /editorial-review
Flask app.py -> 读取 latest.json，处理微信回调和受保护的 shadow 编辑反馈
```

- `src/main.py` 是生产日报主入口；`app.py` 是 Flask 服务入口。
- 采集层包含 RSS、Hacker News、GitHub、Hugging Face、arXiv 和 X 快照。RSS 请求状态写入 `runtime/source-state.db` 的本地 SQLite 账本，但账本只用于诊断且不能作为事实证据；X 通过 GitHub Runner 生成的 `x-feed.json` 接入，不是生产任务直接调用 X API。
- `src/agents`、`src/domain`、`src/services`、`src/workflows` 支持 v2/shadow/editorial 诊断和反馈闭环；它们不能改变已经接受的事实简报或 `DraftDecision`。
- `src/tencent_scf/` 是历史兼容代码，当前 Docker 主流程不依赖它。
- `docs/` 是 nginx 公开/运行时产物目录，不是维护文档目录；长期说明放在 `project_docs/`。
- `egress-proxy` 是可选的 Docker 内部 sing-box sidecar；仅在 `egress-proxy` profile 启用，不能发布宿主机端口，且只通过 `AI_NEWS_*_PROXY` 让 `web` 使用。私有节点仅支持 VLESS WebSocket + TLS 或 TCP + Reality。

## 必须遵守

- 外部 RSS、X、GitHub、HF、arXiv、LLM、图片和微信 API 失败必须记录并降级，不能让单条故障无日志地中断整期日报。
- LLM 只能翻译和摘要原始证据，不能新增事实；GitHub 活跃度不能写成正式发布证据。
- 候选池截断前必须先用规范来源证据统一分类并冻结 `content_type`，再复用按类型分派的确定性来源发布性规则，并与最终 Validator 共用同一分派入口。无效证据和分类器明确拒绝的教程、推广、传闻等候选跳过内容 LLM；其它来源预检失败只降低尝试顺序，不能替代或放宽最终事实门禁。分类细节只写入私有 briefing 审计，公开产物仅保留聚合诊断。GitHub 只有具备项目说明和 release notes 的正式 release 可作为候选；社区 GitHub release 在最终尝试队列中只能排在非 GitHub 事件之后作为候补，不能仅凭新鲜度或 stars 抢占新闻名额。HF 的 likes/downloads/lastModified 只能标记为模型活跃度，arXiv 超时、429 和 5xx 最多有界重试一次后降级。
- 生产日报只能展示 5-20 条唯一内容，至少 3 条 `fact_event`；内容分为 `fact_event`、`ai_update` 和 `attributed_opinion`。动态/观点目标只控制尝试顺序，上限是硬配额；署名观点统一为 `title_only`，标题必须保留作者归因和标题 quote，不生成扩展摘要。跨语言、跨来源的同一事件只能占一个名额。候选先做确定性聚类，对少量疑似重复使用有预算的只读质量 LLM 复核，歧义重复和跨事件桥接冲突按来源强度隔离弱项且不得回填，每个显示声明都必须绑定显示的规范来源证据。
- 语义去重失败、超时、无效、熔断、预算耗尽或返回 `uncertain` 时，保守保留官方/研究/专业媒体/非 X 等更强来源，移除或隔离较弱疑似重复，不得转人工复核；强人物锚点只接受逐行提取的已确认完整姓名，未登记但带人物语境的名称只触发复核且不得由 LLM 升级为强主体，未知 Title Case 短语和单句职位/代词语境不得升级为强人物；发布前去重后继续消费候选回填，最终不足 5 条仍然 `block`。
- 事实简报分为 `brief_mode=title_only|expanded`：标题型快讯允许 `brief=""` 并正常计入 5-20 条；扩展型快讯只保留一至两句由原始标题之外证据支持的摘要。摘要句全部引用只落在规范来源原始标题范围内时删除并记录 `brief_restates_title`；无证据或新增事实仍重建一次后移除，不能靠清空摘要绕过门禁。
- 内容 LLM 只从 Python 提供的编号逐字引用片段中选择 quote ID，不直接生成 quote 或来源 URL；builder 必须确定性恢复规范 quote、URL 和完整 claim 绑定。title quote ID 无效时按畸形条目重建，title 有效但任一 brief quote ID 无效时删除全部摘要绑定并记录 `brief_quote_unresolved`，只允许降级为 `title_only`，不得模糊匹配或用整篇 evidence_text 兜底。
- `content_type=ai_update` 使用独立发布性门禁，不要求正式发布等硬新闻动作或必须包含数字；它必须有明确模型/项目主体，以及可机械核验的实验/榜单/数值细节，或受控的具体行为与能力对象，并保留逐条 quote、规范 URL 和保护锚点绑定。每个标题/摘要声明必须在同一条绑定 quote 内同时匹配谓词前主体、命名对象/指标，以及对应的行为/能力或指标维度与方向，不能跨句拼接，也不能把来源前缀、比较对象或泛技术词当成主体/具体细节；泛标题、推广、虚构结果、确定性行业结论和类型篡改必须拒绝，`fact_event` 继续使用原硬新闻门禁。
- 质量 LLM 是可选增强，缺失、超时或无效响应时，确定性事实规则通过的条目统一退回 `rules_only` 自动入选，不得转为人工复核或要求 LLM 修正事实；跨语言自动降级必须有逐字实体锚点，且只包含可机械核验的数字、动作和语法词，无法核验的翻译语义重建一次后移除。LLM 生成的中文标题可以保留产品名、模型名、缩写、仓库路径、版本号和单位，但残留普通英文语法、动作或叙述词时必须记录 `translation_failed` 并重建一次；确定性英文来源回退只允许已登记机构/模型、`@handle`、数字/版本等受控细节锚点，禁止用任意普通英文名词补足标题。内容、质量和语义去重客户端都关闭 SDK 隐式重试；`glm-5.3-flash` 的结构化内容、质量和语义去重请求必须使用 `reasoning_effort=low`，其它供应商不得接收该智谱专用参数；内容 LLM 的初次生成和重建都必须单条调用。内容 LLM 遇到 429、5xx 或网关不可用时立即打开本期熔断，后续条目直接走既有降级，不得逐条重复撞击中转站。普通请求超时只在连续 3 次后打开本期熔断，任意一次完成 JSON 解码的成功响应都将连续超时计数清零。等待上限由流水线的有界重建、熔断和 `rules_only` 降级控制。失败的 X 重建还必须携带失败原因和保护锚点；内容 LLM 超时、不可用、无效响应、条目缺失/畸形/重复与真实未翻译输出必须使用不同审计原因，中文原文回退另记 `source_fallback_used` 且不得覆盖原始失败原因。builder 允许空字符串 brief，或将一至两项非空字符串 brief 列表机械拼接后继续走完整校验，其它结构仍按畸形响应拒绝。
- `DraftDecision` 是唯一 `create|block` 决策，`DraftExecution` 只记录执行结果；旧发布模块、旧质量状态、来源占比阻断、9 分目标和人工复核均不是生产控制。
- 微信草稿只保留每条事实简报的规范原始来源链接，不设置 `content_source_url`，也不展示指向 `PAGES_URL` 的“查看完整日报”入口。真实草稿必须在封面和新闻配图上传微信后重新调用确定性渲染器；正文首图使用上传返回的微信 CDN URL，不能复用上传前含公网封面的 HTML。`draft/add` 超时、断连或响应无法确认时不得自动重试，避免重复创建草稿；只有明确 API 拒绝才允许有界重试。
- Flask 路由只做协议转换、签名/认证、读取产物和调用服务；禁止在路由中直接采集、调用 LLM、渲染或创建草稿。
- 新采集器、配置、质量门禁、发布出口、Docker/nginx/cron 或 Flask 边界变更，必须同步对应 `project_docs/`，并更新本文件的导航/约束（见 `project_docs/workflow.md`）。
- Python 3.12+，模块使用 `logging`；时间使用带时区值和项目统一日期工具；跨模块数据必须可序列化。

## 配置与安全

- 首次部署只复制 `.env.example`；高级覆盖从 `.env.advanced.example` 逐项复制。
- `SOURCE_STATE_DB_PATH` 默认 `runtime/source-state.db`，只保存 RSS 来源健康状态；Compose 必须持久化挂载 `runtime/`，数据库文件不得提交。
- `QUALITY_LLM_*` 未设置时继承 `LLM_*`，用于可选的事实核验和语义去重增强；语义去重默认 48 小时窗口、单期共享最多 20 次 LLM 调用，新增配置仍以 `.env.advanced.example` 为参考。
- 默认简报配置为 `DAILY_TOP_N=20`、`DAILY_MIN_ITEMS=5`、`DAILY_MIN_FACT_ITEMS=3`、`DAILY_TARGET_UPDATE_ITEMS=5`、`DAILY_MAX_UPDATE_ITEMS=8`、`DAILY_TARGET_OPINION_ITEMS=5`、`DAILY_MAX_OPINION_ITEMS=8`、`DAILY_CANDIDATE_POOL_N=60`、`DAILY_X_TARGET_ITEMS=5`、`DAILY_X_MAX_ITEMS=8`、`X_FEED_MAX_AGE_HOURS=6`。X 快照每四小时生成，最多六小时有效；软目标只控制尝试顺序，不能绕过质检或硬凑；署名观点仅接受 `opinion_eligible=true`、命中确定性 AI 主题和实质立场的自然人原帖，同一作者每期最多一条。
- 修改 `.env` 后执行 `docker compose up -d --force-recreate`。
- 不提交 `.env`、API key、微信密钥/token、Basic Auth 密码、日志、媒体缓存、`docs/` 生成物或完整外部 API 响应。
- 不提交订阅 URL、单节点 `vless://` 链接、生成的 sing-box 配置或服务器私有二进制；它们只可保存在 `/root/ai-news-proxy/` 等 root 受限目录。
- `COVER_RENDER_MODE` 默认 `legacy`；`editorial` 是本地确定性封面模式。图片/质量配置缺失时允许按文档描述降级。
- 生产环境必须配置 `WECHAT_TOKEN` 并进行签名验证；`ALLOW_INSECURE_WECHAT_TOKEN=1` 只允许本地排查。
- `SKIP_WECHAT_DRAFT=1` 是唯一安全干跑边界；被 block 或草稿执行失败必须返回非零，不能以测试为名调用真实微信草稿 API。

## 最小验证

完成任何变更前后，按风险运行受影响测试或完整测试：

```powershell
python -m pytest -q
git diff --check
git status --short
```

报告必须列出实际运行的命令和结果；基线已有失败时，必须单独说明失败测试及其与本次变更的关系，不得声称未通过的检查为通过。

提交前检查 `git diff --staged`。普通提交使用中文 Conventional Commit；合并提交统一使用英文 `Merge` 标识，例如 `Merge: 合并 X 来源采集`，通过 GitHub Pull Request 合并时保留 GitHub 默认的 `Merge pull request #...` 格式。不要使用 `git commit --no-verify` 绕过检查。

## 事实简报审计

- 每个事实简报候选必须在 `docs/debug/<date>-briefing.json` 留下结构化原始证据、构建/验证轨迹和最终原因码；摘要轨迹还记录 `original_brief`、`removed_brief_sentences`、`final_brief`、`brief_mode` 和 `brief_reason`。聚类阶段被合并的每个来源也必须留下独立的 `clustered_duplicate` 记录。该审计不进入公开产物、微信草稿或 Git。
