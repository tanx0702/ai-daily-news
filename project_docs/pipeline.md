# 日报生产流水线

## 生产入口

生产定时任务执行 `python -m src.main`，实际编排位于 `src.main._run_pipeline()`。入口先通过 `src.run_guard.single_run_lock` 防止重复运行，再按报告日期和环境变量读取配置。单条来源或外部 API 异常应记录日志并继续；只有没有候选、没有可选条目或运行锁冲突等无法生成结果的情况才会直接失败。

## 阶段顺序

```text
0. 运行锁与报告日期
   -> 计算 Asia/Shanghai 报告日期，创建/检查 docs/.daily_run.lock

1. 采集与统一候选
   -> RSS + HN + GitHub + Hugging Face + arXiv + X 快照
   -> 日期窗口、AI 相关性、来源级规则、URL/标题去重、热度和新鲜度评分

1.25 证据与编辑质量标注
   -> preserve_source_evidence()
   -> 来源证据、发布时间、事件键、来源等级和可解释编辑分

1.5 初选与备用候选
   -> 来源、主题、独立事件配额
   -> selected_candidates + reserve_candidates

1.x 可选 production editorial
   -> DAILY_EDITORIAL_MODE=v2_assist 时运行 v2 Collector/Analyst/Editorial
   -> ready/write 候选不足或异常时完整回退 v1

2. LLM 摘要
   -> 候选池批量翻译标题和中文摘要（默认批量 5）
   -> 数量/索引异常或批量失败时逐条降级；失败项保留原始标题、来源和链接

2.5 质量门禁与跨候选复核
   -> quality_gate.review_daily() 按原始证据标记风险
   -> high risk 单条移除并从备用候选回填
   -> editorial_review 归并同一事件并重排，不新增新闻事实

2.55 媒体解析
   -> 解析 og:image、twitter:image、JSON-LD 和正文候选图
   -> 可信原文图标记 original/trusted；失败则使用 text_only 卡片

2.6 整期质量与封面主题
   -> assess_daily_edition() 生成 0-10 编辑质量诊断
   -> 生成重点摘要/封面标题（有文本 LLM 时）

3. 日报页面
   -> docs/index.html、docs/archive/<date>.html、docs/wechat.html

4. 封面
   -> COVER_RENDER_MODE=legacy：原文图 -> AI 图 -> Pillow 本地降级
   -> COVER_RENDER_MODE=editorial：900x500 本地确定性模板、日期调色板和故事类型线图

5. 数据和诊断
   -> docs/latest.json
   -> source health、selection、quality、media、publication 和 shadow 诊断

6. 微信草稿
   -> publication.ready 时上传封面并创建公众号草稿
   -> SKIP_WECHAT_DRAFT=1 时只完成日报产物并标记 dry_run
   -> readiness 不满足时不创建草稿，任务返回非零
```

## 候选与证据边界

- `src.collector.collect_news()` 返回的是候选池，不代表已经可以发布。
- 每条候选保留原始 URL、来源、发布时间和摘要证据；LLM 只能重写标题/摘要表达，不能补造原文没有的事实。
- 选题阶段同时保留正式条目和备用条目，备用池用于质量门禁移除后的回填，不用于降低发布标准。
- 同一事件可能来自多个来源；统一合并后记录 `cross_source_count` 和来源列表，最终编辑去重仍需保留独立事件。
- `src.domain` 的 `NewsCandidate`、`SourceEvidence` 等模型是 v2 辅助视图，不替换生产 v1 字典字段。

## 质量与发布门槛

质量门禁的目标是按原始证据隔离高风险条目，同时尽量让日报文件和诊断继续生成。发布 readiness 由 `src.publication.evaluate_publish_readiness()` 统一判定：

- 最终可发布条目至少 6 条。
- 所有条目的 `quality_state` 必须为 `ready`。
- 单一来源不得超过最终条目的 50%。
- LLM 质量复核必须为 `passed` 或 `partial`；`failed` 或未通过状态不能创建草稿。
- 整期风险不能为 `high`。

`QUALITY_GATE_STRICT` 是旧配置兼容标记，不再单独决定整天任务是否阻断。单条 high risk 会被移除并尝试备用回填；没有足够合格备用候选时保留较少条目并阻止草稿，而不是伪造可发布状态。

## 失败与降级

| 失败位置 | 继续生成 | 降级行为 |
| --- | --- | --- |
| 单个 RSS/HN/GitHub/HF/arXiv/X 来源 | 是 | 记录日志，跳过该来源 |
| X 快照过期/ schema 错误 | 是 | 只跳过 X，保留其它来源 |
| LLM 批量摘要失败 | 是 | 逐条重试；失败项保留原始信息 |
| 原文媒体下载失败 | 是 | 生成 text-only 新闻卡片 |
| AI 封面失败 | 是 | 使用本地确定性封面/旧链路降级 |
| 单条 high risk | 是 | 移除并从 reserve 回填 |
| 不满足 publication readiness | 是 | 保存 HTML、JSON、诊断，但不创建微信草稿并返回非零 |
| 无任何候选/无法建立运行结果 | 否 | 记录错误并终止本次任务 |

## 主要产物

| 路径 | 消费者 | 说明 |
| --- | --- | --- |
| `docs/index.html` | nginx/读者 | 最新日报页面 |
| `docs/archive/<date>.html` | nginx/读者 | 按日期归档 |
| `docs/wechat.html` | 微信正文预览/调试 | 草稿正文的本地预览 |
| `docs/cover.jpg` | 日报和微信 | 当前封面 |
| `docs/latest.json` | Flask/审阅工具 | 新闻、质量、媒体、选择和发布状态 |
| `docs/debug/` | 维护者 | 来源健康、质量和 shadow 诊断 |
| `docs/media/` | 渲染/微信 | 原文媒体缓存 |

这些文件是运行时生成物，不应作为源代码提交。
