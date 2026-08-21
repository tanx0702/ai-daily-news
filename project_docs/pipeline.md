# 日报生产流水线

## 生产入口

生产定时任务执行 `python -m src.main`，实际编排位于 `src.main._run_pipeline()`。入口先通过 `src.run_guard.single_run_lock` 防止重复运行，再按报告日期和环境变量读取 `BriefingConfig`。单条来源或外部 API 异常应记录日志并继续；配置无效、没有候选、无法形成至少 5 条事实简报或运行锁冲突等情况会形成明确的阻止/失败结果。

## 阶段顺序

```text
0. 运行锁与报告日期
   -> 计算 Asia/Shanghai 报告日期，创建/检查 docs/.daily_run.lock

1. 采集与统一候选
   -> RSS + HN + GitHub + Hugging Face + arXiv + X 快照
   -> 日期窗口、AI 相关性、来源级规则、URL/标题去重、热度和新鲜度评分
   -> 在截取候选池前复用确定性来源发布性规则；可发布事件优先填充候选池，规则拒绝项只在可发布事件不足时补尾并记录原因，不调用 LLM，也不放宽后续事实门禁

1.25 规范来源证据与事件聚类
   -> 保留规范来源 URL、原始证据文本、带时区发布时间和 X 线程关系
   -> 默认 45 条候选先按确定性特征聚类，再对少量疑似重复使用只读质量 LLM 复核
   -> 跨语言、跨来源的同一事件只保留一个规范来源，其它来源作为 related evidence；歧义重复项进入 quarantine，永不回填
   -> 候选若在两个既有事件之间形成 confirmed/uncertain 桥接，保留更强事件，并把较弱事件及桥接来源一起隔离

1.5 事实新闻与署名观点候选
   -> 从唯一事件/观点生成最多 15 条简报，先保证至少 3 条事实新闻
   -> X 未达到默认软目标 5 条时优先尝试下一条合格 X 候选；软目标不是配额，候选不足或质检失败时不硬凑
   -> 社区 GitHub release 保留在完整队列中，但排在全部非 GitHub 事件之后，只在其它来源不足时回填
   -> 署名观点最多 3 条，同一作者最多 1 条，标题必须保留作者归因
   -> 每个显示声明必须绑定到显示的规范来源证据

1.x v2/shadow/editorial 诊断
   -> 仅记录受保护的诊断与反馈
   -> 不得改变已接受的简报或草稿决策

2. 生成与核验事实简报
   -> LLM 只翻译/摘要规范来源证据，不增加新闻事实；完整标题必须有明确主体、可断言动作和具体对象/结果，且在同一规范来源事实框架中可机械核验
   -> 内容 LLM 返回 title 和零至两个 brief 展示目标；没有标题外事实时允许空 brief，Python 生成完整 claim 绑定
   -> X 首次生成失败后的重建使用单条请求，并携带失败原因以及 @handle、名称和数字保护锚点
   -> 内容 LLM 超时、不可用、无效 JSON/schema、响应缺项、条目畸形、重复 index 与有效结构下的未翻译输出分别记录，不能统一压缩为 translation_failed
   -> 中文标题可保留产品名、模型名、缩写、仓库路径、版本号和单位；残留普通英文语法、动作或叙述词时按 translation_failed 重建一次，不能仅凭标题含有汉字放行
   -> 供应商可返回空字符串 brief；一至两项非空字符串列表只机械拼接后重新校验，三项以上或其它结构仍拒绝
   -> 第二次失败若使用完整中文原文回退，独立记录 source_fallback_used，并保留触发回退的原始原因码；若第二次重建仍为 title_claim_not_source_bound 或 title_missing_event_action，且英文源标题可机械抽取已登记实体或可原样保留的完整源标题主体、动作和另一原文细节锚点时，允许生成只含这些锚点的 title_only 回退，不翻译对象或补写摘要
   -> 确定性规则核验展示目标、来源 URL、逐字证据引文、名称/数字/动作和唯一事件；发布性、绑定和跨语言 rules_only 校验共享同一动作词表，不能各自维护漂移版本
   -> 摘要句引用仅落在原始标题范围时逐句删除；全部删除后转为 title_only，有增量句时保持 expanded
   -> 质量 LLM 只做只读语义增强；缺失、超时或无效响应时，硬规则通过的条目自动使用 rules_only，不请求人工复核
   -> 内容、质量和语义去重 OpenAI 客户端禁用 SDK 自动重试；内容生成只使用流水线已有的单次重建预算，质量和去重失败立即熔断或降级
   -> 跨语言 rules_only 必须有逐字实体锚点，且只包含可机械核验的数字、动作和语法词；已知产品名可作为逐字主体锚点以容纳语法转换，标题只翻译动作和语法，非实体、非数字细节必须保留原文锚点或删去；用途等翻译语义仍需质量 LLM 接受

2.25 发布前语义去重与回填
   -> 事实核验通过后，再与已经接受的简报比较，处理生成文本差异造成的漏重
   -> 同一事件保留更强来源：官方 > 研究 > 专业媒体 > 社区；同级来源优先非 X
   -> 更强候选可原子替换较弱条目，被移除条目不重新进入队列；流水线继续消费候选直到达到上限或队列耗尽
   -> LLM 超时、无效、不可用、熔断、预算耗尽或 uncertain 时保守移除较弱疑似重复，不进入人工复核

2.55 媒体解析
   -> 解析 og:image、twitter:image、JSON-LD 和正文候选图
   -> 下载/格式校验后还必须和已核验的标题、来源标题或引用共享完整模型名，或至少两个去品牌、去泛词后的事件锚点；不相关图或缺少图文上下文时使用 text_only 卡片

2.6 决策与确定性封面输入
   -> DraftDecision 是唯一 create|block 决策：5-15 条唯一有效简报、至少 3 条事实、最多 3 条观点、无遗留重复，且 X 规范来源最多 8 条
   -> 封面文字只使用已验证的最终标题或固定“今日AI要闻”；最终核验后不再调用 LLM 生成正文或重点

3. 日报页面
   -> docs/index.html、docs/archive/<date>.html、docs/wechat.html

4. 封面
   -> COVER_RENDER_MODE=legacy：原文图 -> AI 图 -> Pillow 本地降级
   -> COVER_RENDER_MODE=editorial：900x500 本地确定性模板、日期调色板和故事类型线图

5. 数据和诊断
   -> docs/latest.json
   -> schema v2：brief_items、draft_decision、draft_execution 和 diagnostics

6. 微信草稿
   -> DraftDecision=create 时才上传封面并创建公众号草稿
   -> 上传封面和可信新闻配图后，使用微信 CDN URL 重新生成最终正文；上传前预览 HTML 不进入真实草稿
   -> draft/add 只有明确 API 拒绝才有界重试；超时、断连或响应无法确认时立即记为 draft_create_uncertain，避免重复草稿
   -> 草稿只保留每条事实简报的规范原始来源链接，不设置公众号 `content_source_url`，也不展示站点“查看完整日报”入口
   -> SKIP_WECHAT_DRAFT=1 是唯一安全干跑边界：生成产物并记录 dry_run，不调用草稿 API
   -> block 或草稿执行 failed 时不创建草稿，任务返回非零
```

## 候选与证据边界

- `src.collector.collect_news()` 返回的是候选池，不代表已经可以创建草稿。
- `src.collector.collect_candidates()` 会在候选池截断前执行确定性发布性预检。预检只改变候选尝试顺序：通过项先按原评分排序，拒绝项和无效证据项随后保留；诊断记录预检总数、通过数、拒绝数、无效证据数和原因分布。
- 每条候选保留规范来源 URL、来源、发布时间和原始证据；LLM 只能翻译/摘要这些证据，不能补造事实。
- 同一事件聚类后只生成一个最终事实简报；无法可靠判定的重复项隔离，不能回填到不足条目的日报。
- 语义去重默认只比较 48 小时窗口内的事件。确定性规则先比较 URL、X status ID、强实体、动作、限定数字和文本相似度；自动合并只接受双方来源标题中可断言的同类动作，`research`、`office` 等背景名词只可触发复核，不能单独合并。金额、百分比、年份、时长分别比较，只有同类别数字互相矛盾才判为不同事件。
- 高文本相似度不能单独证明同一事件；除冻结的规范化原始来源标题完全相同外，自动合并还必须有共享组织、人物或模型锚点。发布前比较不会使用 LLM 生成的中文标题触发完全匹配；只共享 `OpenAI` 等宽泛组织名不足以判定重复。
- 质量 LLM 返回 `same_event` 时，至少一个主体必须精确对应两边确定性规则共同抽取出的完整人物或模型实体；强人物锚点只来自逐行提取的已确认姓名。未登记但带人物语境的名称只触发复核，不能被 LLM 的 `same_event` 升级为强主体；此时按歧义重复隔离弱项。未知 Title Case 短语、职位、单句代词语境、`AI`、`executive`、实体片段和宽泛组织名只能作为辅助信息。
- 聚类和发布前去重共享同一个 `QUALITY_LLM_*` reviewer、调用预算和熔断状态。LLM 只返回 `same_event|distinct|uncertain` 关系，不得改写标题、摘要或证据。
- `BriefItem.brief_mode` 只能是 `title_only` 或 `expanded`。`title_only` 的 `brief` 为空且正常计入 5-15 条决策；`expanded` 保留一至两句标题外增量事实。每个完整标题或非空摘要句都以规范来源中的证据文本和 URL 绑定；同一展示目标允许多条引用，任何缺少目标、错误 URL 或证据引用都不能进入 `brief_items`。
- `src.domain`、`src.agents` 和 `src.workflows` 的 v2/shadow/editorial 模型只支持诊断和反馈，不替换生产事实简报契约。

## 决策与执行契约

`DraftDecision` 是唯一生产决策，动作只能是 `create` 或 `block`。它依据 5-15 条唯一、已核验的内容，要求至少 3 条事实新闻、最多 3 条署名观点、每位观点作者最多 1 条，每个声明拥有规范来源证据，并限制最终把 X 用作规范来源的条目不超过 8 条。`DAILY_X_TARGET_ITEMS` 只控制候选尝试顺序，不是 `DraftDecision` 的来源配额；少于软目标不会 block。少于 5 条时 block；5-14 条是正常短版。

`DraftExecution` 不改变决策，只记录 `draft_created`、`dry_run`、`blocked` 或 `failed`。`SKIP_WECHAT_DRAFT=1` 是唯一安全干跑边界。旧 `src/publication.py`、`src/quality_gate.py`、`publication.ready`、`quality_state`、来源占比阻断、9 分目标和人工复核均不是生产控制。

## 失败与降级

| 失败位置 | 继续生成 | 降级行为 |
| --- | --- | --- |
| 单个 RSS/HN/GitHub/HF/arXiv/X 来源 | 是 | 记录日志，跳过该来源 |
| X 快照过期/ schema 错误 | 是 | 只跳过 X，保留其它来源 |
| 内容 LLM 超时、不可用、整包无效或条目缺失/畸形/重复 | 是 | SDK 不自动重试；流水线最多重建一次。X 使用携带原因和保护锚点的单条请求，其它来源保持有界批量；失败项记录精确原因，中文原文回退另记独立标志且不覆盖原因 |
| 原文媒体下载失败 | 是 | 生成 text-only 新闻卡片 |
| AI 封面失败 | 是 | 使用本地确定性封面/旧链路降级 |
| 质量 LLM 不可用/无效 | 是 | 有逐字实体锚点且只含可机械核验数字/动作的跨语言条目可用 `rules_only` 自动入选；其余条目重建一次后移除，不进行人工复核 |
| 语义去重 LLM 不可用/超时/无效/预算耗尽 | 是 | 保守保留较强来源并移除或隔离较弱疑似重复；继续从队列回填，不进入人工复核 |
| 少于 5 条有效唯一简报或发现遗留重复 | 是 | 保存 HTML、schema v2 JSON、诊断，记录 `block`，返回非零 |
| 微信封面缺失、上传无 CDN URL 或最终正文未使用微信封面 | 是 | 不创建半成品草稿；保存 HTML、schema v2 JSON、诊断，记录 `failed`，返回非零 |
| 微信 draft/add 结果不确定 | 是 | 不自动重试；记录 `draft_create_uncertain`，返回非零，由维护者先检查公众号后台 |
| 微信草稿执行失败 | 是 | 保存 HTML、schema v2 JSON、诊断，记录 `failed`，返回非零 |
| 无任何候选/无法建立运行结果 | 否 | 记录错误并终止本次任务 |

## 主要产物

| 路径 | 消费者 | 说明 |
| --- | --- | --- |
| `docs/index.html` | nginx/读者 | 最新日报页面 |
| `docs/archive/<date>.html` | nginx/读者 | 按日期归档 |
| `docs/wechat.html` | 微信正文预览/调试 | 草稿正文的本地预览 |
| `docs/cover.jpg` | 日报和微信 | 当前封面 |
| `docs/latest.json` | Flask/审阅工具 | schema v2 的 `brief_items`、决策、执行结果和诊断；v1 仅冷启动只读兼容 |
| `docs/debug/` | 维护者 | 来源健康、聚类、核验和 shadow 诊断 |
| `docs/media/` | 渲染/微信 | 原文媒体缓存 |

这些文件是运行时生成物，不应作为源代码提交。

`docs/debug/<date>-briefing.json` 的 `candidate_audit` 仅供维护者逐条追溯：它保留事件的结构化原始证据、每次构建稿、证据绑定、验证或重建结果、`original_brief`、`removed_brief_sentences`、`final_brief`、`brief_mode`、`brief_reason`，以及最终状态和原因码。聚类阶段并入 related evidence 的每个来源使用独立 `clustered_duplicate` 条目记录原始证据、`duplicate_of`、`relationship` 和 `comparison_mode`。审计不写入 `docs/latest.json`、公开 HTML 或微信草稿，也不得包含密钥或完整第三方 API 原始响应。
