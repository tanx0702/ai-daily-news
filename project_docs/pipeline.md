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

1.25 规范来源证据与事件聚类
   -> 保留规范来源 URL、原始证据文本和带时区发布时间
   -> 默认 45 条候选先按事件聚类；歧义重复项进入 quarantine，永不回填

1.5 事实简报候选
   -> 从唯一事件生成最多 15 条简报，按排序偏好选择候选
   -> 每个显示声明必须绑定到显示的规范来源证据

1.x v2/shadow/editorial 诊断
   -> 仅记录受保护的诊断与反馈
   -> 不得改变已接受的简报或草稿决策

2. 生成与核验事实简报
   -> LLM 只翻译/摘要规范来源证据，不增加新闻事实
   -> 确定性规则核验每个声明、来源 URL、证据引文和唯一事件
   -> 质量 LLM 是可选增强；同语言条目在缺失、超时或无效响应时使用 rules_only；中文 claim 绑定非中文 quote 时必须语义 accept，否则重建一次后排除，不请求人工复核或 LLM 修正

2.55 媒体解析
   -> 解析 og:image、twitter:image、JSON-LD 和正文候选图
   -> 可信原文图标记 original/trusted；失败则使用 text_only 卡片

2.6 决策与确定性封面输入
   -> DraftDecision 是唯一 create|block 决策：5-15 条唯一有效简报、无遗留重复，且 X 规范来源最多 5 条
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
   -> SKIP_WECHAT_DRAFT=1 是唯一安全干跑边界：生成产物并记录 dry_run，不调用草稿 API
   -> block 或草稿执行 failed 时不创建草稿，任务返回非零
```

## 候选与证据边界

- `src.collector.collect_news()` 返回的是候选池，不代表已经可以创建草稿。
- 每条候选保留规范来源 URL、来源、发布时间和原始证据；LLM 只能翻译/摘要这些证据，不能补造事实。
- 同一事件聚类后只生成一个最终事实简报；无法可靠判定的重复项隔离，不能回填到不足条目的日报。
- 每个显示声明都以规范来源中的显示证据文本和 URL 绑定；任何不完整绑定都不能进入 `brief_items`。
- `src.domain`、`src.agents` 和 `src.workflows` 的 v2/shadow/editorial 模型只支持诊断和反馈，不替换生产事实简报契约。

## 决策与执行契约

`DraftDecision` 是唯一生产决策，动作只能是 `create` 或 `block`。它依据 5-15 条唯一、已核验的事实简报，要求每个声明拥有规范来源证据，并限制最终把 X 用作规范来源的条目不超过 5 条。少于 5 条时 block；5-14 条是正常短版。

`DraftExecution` 不改变决策，只记录 `draft_created`、`dry_run`、`blocked` 或 `failed`。`SKIP_WECHAT_DRAFT=1` 是唯一安全干跑边界。旧 `src/publication.py`、`src/quality_gate.py`、`publication.ready`、`quality_state`、来源占比阻断、9 分目标和人工复核均不是生产控制。

## 失败与降级

| 失败位置 | 继续生成 | 降级行为 |
| --- | --- | --- |
| 单个 RSS/HN/GitHub/HF/arXiv/X 来源 | 是 | 记录日志，跳过该来源 |
| X 快照过期/ schema 错误 | 是 | 只跳过 X，保留其它来源 |
| LLM 批量摘要失败 | 是 | 逐条重试；失败项保留原始信息 |
| 原文媒体下载失败 | 是 | 生成 text-only 新闻卡片 |
| AI 封面失败 | 是 | 使用本地确定性封面/旧链路降级 |
| 质量 LLM 不可用/无效 | 是 | 同语言条目严格使用 `rules_only`；中文 claim 绑定非中文 quote 时重建一次后移除，不进行人工复核或 LLM 修正 |
| 少于 5 条有效唯一简报或发现遗留重复 | 是 | 保存 HTML、schema v2 JSON、诊断，记录 `block`，返回非零 |
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
