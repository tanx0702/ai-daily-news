# 后端与 Python 开发约束

## 适用范围

本项目的“后端”包括日报批处理、采集器、LLM/图片/微信外部 API 适配以及 Flask 服务。约束目标是保持候选证据、事实简报、草稿决策和运行时产物的边界清晰，而不是套用传统 Web 三层或 Monorepo 结构。

## 分层职责

```text
Flask 路由       HTTP/XML 解析、签名/认证、响应映射、调用服务
采集器适配       单一来源请求、响应解析、候选标准化、来源级失败隔离
领域/服务         候选、规范来源证据、事实简报、诊断、反馈和可序列化契约
编排入口         阶段顺序、聚类/隔离、确定性降级、运行锁、产物保存和退出码
发布适配         封面上传、正文构建、微信公众号草稿 API
```

依赖方向遵守：

- `app.py` 读取 `docs/latest.json` 和服务层数据，不反向调用日报主编排。
- `src/main.py` 可以调用采集、编辑、摘要、质量、媒体、封面、产物和微信模块；单个模块不得自行改变整个流水线的阶段顺序。
- `src/collectors/` 只负责一个来源的获取和标准化，返回候选列表；去重、配额和最终选择由统一编辑层完成。
- `src/domain/` 提供跨模块稳定模型；`src/services/` 保存快照、反馈和审阅结果；`src/workflows/` 默认保持无副作用。
- 外部 RSS、X feed、GitHub、HF、arXiv、LLM、图片和微信请求必须通过明确的适配函数/类，不能散落在路由、模板或领域模型中。

## 外部请求与失败处理

- 网络超时、HTTP 错误、JSON/XML 解析错误和供应商返回异常必须记录上下文日志，并返回空候选、原文降级、跳过该媒体或阻止草稿等明确结果。
- 单个来源失败不能阻断其它来源；封面、原文图和 AI 摘要失败不能删除已经可生成的日报 HTML。歧义重复候选必须隔离，不能作为回填来源。
- 任何重试都必须有上限和超时；不能通过无限重试掩盖供应商故障或拖垮每日任务。
- 外部响应不得未经验证直接改变已接受的 `BriefItem`、`DraftDecision` 或 `DraftExecution`；内容 LLM 必须返回 `title` 和零至两个摘要展示目标，Python 生成完整 `EvidenceBinding.claim`。`BriefItem.brief_mode=title_only` 要求空 brief，`expanded` 要求一至两句非空 brief；摘要引用只在原始标题范围内时逐句删除。质量 LLM 不可用或响应无效时，确定性事实规则通过的条目必须使用 `rules_only` 自动入选；跨语言自动降级还必须有逐字实体锚点，且不能包含规则无法核验的用途等翻译语义。不得请求人工复核或 LLM 修正事实。
- 跨来源语义去重必须先运行冻结原始证据的确定性特征判断，生成的中文标题和摘要不能作为标题完全匹配或实体锚点。只有共享主体和动作但无法确定的少量 pair 才允许调用质量 LLM；`same_event` 必须精确绑定两边规则均抽取到的完整人物或模型实体，宽泛组织、角色词和实体子串不能单独证明同一事件。复核器只能返回严格关系 JSON，不能重写内容；聚类和发布前去重共享有上限的调用预算与熔断状态。
- 语义复核不可用、超时、响应无效、预算耗尽或返回 `uncertain` 时，不得把降级草稿交给人工复核。系统保守保留来源优先级更高的候选，隔离或移除较弱疑似重复，并继续从剩余候选回填。

## Flask 路由边界

- `/wechat` 只负责微信签名验证、XML 解析、消息路由和 XML 回复；生产环境必须配置 `WECHAT_TOKEN`。
- `/health` 只返回服务可用性和已保存的草稿决策/执行结果，不主动触发采集或发布。
- `/api/news` 只读取 `NEWS_DATA_FILE` 指向的 `latest.json`，异常时返回可诊断的错误，不泄露路径以外的敏感配置。
- `/editorial-review` 和 `/editorial-review/feedback` 只有在同时配置用户名和密码时才暴露，并使用 Basic Auth；不得使用 URL token 暴露 shadow 数据。
- 路由中禁止直接调用 LLM、图片模型、RSS、X、GitHub、微信草稿 API、文件渲染或 shell 命令。

## 数据、时间和日志

- 候选跨模块传递时保留 `id`、`title`、`url`、`source`、`source_type`、`published_at`、`summary` 和证据字段；X 候选还保留受限数字的 tweet/thread/reply/quote ID。最终 `BriefItem` 还必须保留事件键、规范来源证据、每个显示声明的证据绑定、`brief_mode` 和 `brief_reason`。
- 写入 JSON 的对象必须可序列化；日期由 `src.pipeline_artifacts.json_serial` 统一转换，不把 Python 对象直接写入产物。
- 网络时间和持久化时间使用带时区的 `datetime`；日报日期由 `src.time_utils` 按 `APP_TIMEZONE`（生产默认 `Asia/Shanghai`）计算。
- 所有模块使用 `logging`；日志可以包含来源名、候选数、阶段状态和错误类别，但不得包含 `LLM_API_KEY`、`IMAGE_API_KEY`、微信 secret/token、密码、Authorization 头或完整供应商响应。
- 生产诊断应写入结构化的质量/来源/选择结果，不把原始密钥或未脱敏外部响应复制到 `docs/debug`。

## 安全与发布边界

- 原文 HTML、RSS 摘要、X 文本和用户微信消息必须经过现有清理/转义流程，不能直接拼接进 HTML、XML 或公众号正文。
- GitHub push、点赞、下载量等只能作为活跃度或社区信号，不能独立证明官方发布事实。
- `DraftDecision.action` 是微信草稿创建的唯一前置决策；旧的 `publication.ready`、`quality_state`、来源占比阻断、9 分目标和人工复核不是生产控制。
- `DraftExecution` 仅报告 `draft_created`、`dry_run`、`blocked` 或 `failed`；`SKIP_WECHAT_DRAFT=1` 是唯一安全干跑边界，被 block 或失败的运行必须返回非零。
- 微信发布适配必须接收媒体解析后的展示项，先上传封面和可信新闻配图，再使用返回的微信 URL 调用确定性渲染器生成最终正文；封面文件缺失、上传结果缺少 `media_id`/CDN URL，或最终正文首图不是该 URL 时，不得调用 draft/add。`draft/add` 超时、断连、无效响应等模糊结果统一记为 `draft_create_uncertain` 并停止，不能盲目重试；只有明确拒绝才允许有界重试。
- 质量审计应区分 `missing_target_binding`、`quote_not_found`、`source_url_mismatch`、`protected_token_missing`、`action_not_supported`、`semantic_review_rejected`、`quality_llm_unavailable` 和 `quality_llm_invalid_response`，不能把所有失败压缩为笼统的 `unsupported_claim`。内容生成审计还要区分 `content_llm_timeout`、`content_llm_unavailable`、`invalid_builder_response`、`builder_item_missing`、`builder_item_malformed`、`builder_item_duplicate` 与有效结构下的 `translation_failed`；中文原文或仅含已登记实体/完整源标题主体、动作和原文细节锚点的确定性 title_only 回退以独立 `source_fallback_used` 标志记录，保留触发回退的原因码。摘要轨迹记录输入、删除句、最终 brief、模式和原因。供应商可返回空字符串 brief，或一至两项非空字符串列表并由适配层机械拼接；三项以上或其它对象结构仍记为 `builder_item_malformed`。
- 语义事件审计应记录 `duplicate_of`、`relationship`、`comparison_mode`、`semantic_duplicate`、`semantic_duplicate_unresolved` 以及 reviewer 的成功、超时、无效、不可用、熔断和预算耗尽计数；聚类时并入 related evidence 的每个来源还要生成独立的 `candidate_type=clustered_duplicate` 记录，不得记录完整模型响应。
- `.env`、API key、微信凭证、日志、`docs/` 生成物和真实外部响应不得提交。
- v2/shadow/editorial review 和 Tencent SCF 不得成为生产简报的必需依赖；辅助流程只能记录受保护诊断，不能改变已接受的条目或决策。

## 测试要求

- 新采集器至少覆盖成功响应、超时/异常响应、无效数据、日期窗口和标准化字段。
- 修改质量门禁、发布 readiness、工作流状态或微信边界时，补充对应的风险/拒绝/成功路径测试。
- 行为变更先运行受影响的精确测试，再按变更风险运行完整 `python -m pytest -q`；若基线已有失败，必须在报告中区分，不得声称全绿。
- 文档和配置变更也要运行 `git diff --check` 与敏感文件检查。

## 明确禁止

- 在 Flask 路由中编排采集、摘要、渲染或发布。
- 在 collector 中写入微信草稿、改变最终发布状态或生成公开 HTML。
- 以单一来源、GitHub 活跃度或未经验证的外部文本替代原始证据。
- 为单次外部 API 失败抛出未记录的异常，导致整条日报无输出。
- 把真实 `.env`、密钥、Authorization、密码或完整外部响应写入日志或诊断文件。
- 为了通过测试而修改当前日期、质量门槛或生产默认值；测试应使用可控时间或明确的固定输入。
