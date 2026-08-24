# 新闻来源与采集器

## 来源总览

采集统一由 `src.collector.collect_news()` 编排。RSS 来源由 `config/rss_sources.json` 定义；非 RSS 来源由 `src/collectors/` 下的适配器负责。所有来源最终转换为带 `id`、`title`、`url`、`source`、`source_type`、`published_at`、`summary`、`metrics` 和 `scores` 的候选结构，再统一合并、过滤、事件聚类和排序。展示前必须将每个显示声明绑定到其规范来源证据。

| 来源 | 代码入口 | 配置/开关 | 主要信号和限制 |
| --- | --- | --- | --- |
| RSS | `src/collector.py::_fetch_source` | `config/rss_sources.json`、`DAILY_NEWS_HOURS`、`DAILY_RSS_TIMEOUT`、`SOURCE_STATE_DB_PATH` | AI 关键词、发布时间窗口、URL/标题去重；配置中的 `tier`/`region` 参与编辑平衡；每次请求写入来源健康账本 |
| Hacker News | `src/collectors/hackernews.py` | `ENABLE_HN_COLLECTOR`、`HN_DETAILS_TIMEOUT` | score/comments 和原文链接；低信号 HN-only 条目降权 |
| GitHub | `src/collectors/github.py` | `ENABLE_GITHUB_COLLECTOR`、可选 `GITHUB_TOKEN` | 只有近期正式 release、项目说明和可读 release notes 同时存在才形成候选；stars/push 只能作为社区信号 |
| Hugging Face | `src/collectors/huggingface.py` | `ENABLE_HF_COLLECTOR`、可选 `HF_TOKEN` | likes/downloads/lastModified 统一标记为 `model_activity`，不能自动等同模型发布 |
| arXiv | `src/collectors/arxiv.py` | `ENABLE_ARXIV_COLLECTOR` | 论文日期、摘要和技术信号；超时、429、5xx 有界重试一次，仍失败则降级为空 |
| X | `src/collectors/x_feed.py` | `ENABLE_X_COLLECTOR`、`X_FEED_URL`、可选 `X_FEED_LOCAL_PATH`、`X_FEED_MAX_AGE_HOURS`、`DAILY_X_TARGET_ITEMS`、`DAILY_X_MAX_ITEMS` | 优先读取 VPS 本机认证采集生成的 JSON 快照；本机文件缺失、损坏或过期时回退 GitHub Runner 的公开快照；最多六小时有效，默认优先尝试形成五条合格 X 内容，最终最多八条可将 X 用作规范来源 |

## RSS 配置

`config/rss_sources.json` 维护新闻源名称、RSS URL、区域和可选来源等级。新增 RSS 源必须：

1. 提供稳定的 RSS/Atom URL 和可追溯来源名称。
2. 确认条目具有可解析发布时间；没有日期的内容默认被过滤。
3. 在 `tests/test_rss_sources.py` 或对应采集测试中覆盖配置结构。
4. 同步本文件和 `project_docs/architecture.md`，必要时同步环境变量说明。

RSS 候选在 `src.collector.py` 中做两级 AI 关键词过滤、发布时间窗口过滤和中文 bigram/英文 Jaccard 标题去重。采集器失败不会阻断其它 RSS 源。当前在原有官方与媒体源之外，补充了 `Hugging Face Blog`、`MIT Technology Review AI` 和 `Ars Technica AI` 三个已验证的专题 feed，用来增加非 X 的官方技术信息和专业媒体事实供给；它们仍须通过统一发布时间、发布性和事实门禁。

每次 RSS 请求都会在 `SOURCE_STATE_DB_PATH` 指定的 SQLite 账本中记录最近尝试/成功时间、状态、连续失败次数、条目数、延迟、错误摘要和内容 hash。状态只用于诊断来源是否失效、空载或不稳定，不参与放宽发布门禁，也不能作为新闻证据。默认路径为 `runtime/source-state.db`，Docker 将 `runtime/` 持久化挂载到容器，账本不得提交到 Git。

## 社区和研究来源

### Hacker News

`HackerNewsCollector` 先取得 AI 相关条目，再补抓 Hacker News 详情和原文链接。score、comments 和是否有跨源确认只用于热度/可信度评分，绝不能作为新闻摘要或正式发布证据。若 HN 条目有可验证的外链，事实简报使用外链作为规范 URL 和发布者，HN 只记录为发现渠道；只有标题、积分或评论元数据的条目不得进入日报。

### GitHub

`GitHubCollector` 采集近期 AI 项目和仓库活跃度。GitHub 最近 push 只能说明项目活跃，不能被摘要写成“官方发布”或新产品发布；正式发布声明仍需原始官方证据或跨源确认。`GITHUB_TOKEN` 只用于提高 API 限额，不能写入日志。

正式候选标题使用 `<owner/repo> releases <tag>`，其中动作、仓库名和版本号均直接来自 GitHub Release API；候选还必须具有 release URL、发布时间、项目用途和足够长度的 release notes。普通 push、stars 和缺少变更说明的 tag 不进入正式候选。

通过来源发布性预检的 GitHub release 仍属于社区候选。最终简报尝试队列会把它们排在全部非 GitHub 事件之后，只有官方、研究、专业媒体、X 等其它合格事件不足时才回填；stars 和新鲜度不能让小项目 release 提前占满日报名额。

### Hugging Face

`HuggingFaceCollector` 采集近期模型/论文/社区信号。likes 和 downloads 只影响热度，不能替代模型卡、论文或官方公告等内容证据。接口限流或字段异常时返回空列表并继续日报。

Hub API 的 `lastModified`、累计 downloads 和 likes 使用 `hf_activity_type=model_activity` 明确标记为活跃度雷达。若标题和模型卡证据没有可机械核验的发布动作，该条目会在候选池确定性预检中排到正式发布事件之后，不能为了补足条目把普通模型更新写成发布新闻。

### arXiv

`ArxivCollector` 采集近期 AI 论文，保留论文 URL、摘要和时间。论文是研究来源，不自动等同产品发布；编辑层需要根据主题、影响和可验证性决定是否进入日报。

arXiv API 对超时、HTTP 429 和 5xx 最多重试一次，重试前短暂退避；其它 4xx 不重试。两次请求仍失败时记录原因并返回空列表，不阻断 RSS、HN、GitHub、HF 或 X。

## X 快照来源

X 不通过日报生产进程直接调用 X。试运行期间，如果设置了 `X_FEED_LOCAL_PATH`，生产采集先读取 VPS 上由 `scripts/x_authenticated_feed.py` 生成的本机快照；本机快照必须新鲜且符合 `x-feed-v1`，否则回退读取 `X_FEED_URL`。默认 HTTPS 地址仍为仓库 `x-feed` 分支中的 `x-feed.json`，由 GitHub Runner/相关工作流生成，作为回滚路径。工作流按 UTC `07` 分、`02/06/10/14/18/22` 点运行，对应 Asia/Shanghai 的 `02:07、06:07、10:07、14:07、18:07、22:07`。

网页探针优先读取允许的 X GraphQL 响应；当响应没有可用推文时，再从已渲染的公开 `cellInnerDiv`/`article` 卡片回退提取。当前页面可能不再提供旧版推文 `data-testid` 或 Schema.org 标记，但正文容器与规范 `/status/<id>` 链接仍需同时可读取；评估器继续要求正文和数字状态 ID，避免把导航或登录区域写入快照。

`config/x_sources.json` 是受控账号白名单，账号按 `primary`、`research`、`media` 分层，并标记 `official`。自然人还可显式标记 `opinion_eligible=true`，该字段只授予署名观点候选资格，不改变来源权威等级。快照中的每条记录必须满足：

当前白名单包含 20 个 `primary` 官方账号、28 个 `research` 研究/技术账号和 16 个 `media` 专业资讯账号。个人研究者、记者和资讯作者统一标记 `official=false`，只能提供候选线索或本人公开陈述；仅 `opinion_eligible=true` 的自然人可进入署名观点预检，不能自动升级为机构官方事实。

- schema 为 `x-feed-v1`，并且顶层包含有效 `generated_at` 和 `tweets` 列表。
- 快照生成时间在 `X_FEED_MAX_AGE_HOURS`（默认 6 小时）内，允许最多 5 分钟的时钟偏差。
- `tweet_id` 为数字，文本、source name、author、source tier 和创建时间非空。
- URL 是 `https://x.com/<handle>/status/<tweet_id>` 或 `www.x.com` 的对应 status URL。
- Runner 将 X GraphQL 的 legacy 日期规范化为 UTC ISO 8601，并保留受限数字的 `thread_id`、`reply_to_id` 和 `quoted_id`；生产 collector 再映射为事实证据的线程关系字段。无效、非 ASCII 或超长 ID 只清空对应关系，不能影响其它候选。
- 转换后的候选 `source_type` 为 `x`，来源名带 `(X)`，官方账号记录 `x_official=True`。
- 转换前会确定性跳过转发/`RT @...`、招聘课程报名和祝贺推广等非独立新闻内容，并记录跳过原因；这些内容不能在观点预检失败后回落成 `fact_event`。

通过上述过滤的 X 候选按确定性顺序分类：先判断合格的署名观点并标为 `attributed_opinion`；否则，只要正文包含明确的硬新闻动作（例如发布、上线、收购或任命），就保持 `fact_event`；只有在没有这类动作时，才将同时包含可机械核验的模型/版本、数字结果（如百分比、排名或速度）或具体技术进展的内容标为 `ai_update`。推广、转发、纯链接和泛评论即使含有技术词或数字，也不得升级为 `ai_update`，不满足动态条件的候选保持 `fact_event` 或在前置过滤中被跳过。

快照读取失败、schema 不合法、时间过期或单条记录不完整时只跳过对应 X 候选/整份 X 快照，不影响 RSS、HN、GitHub、HF 和 arXiv。有效的本机空快照是权威结果，不会被旧的远程快照覆盖；本机文件失效才允许回退远程来源。X 候选仍须经过事件聚类和规范来源证据绑定；歧义重复项隔离且不得回填。`DAILY_X_TARGET_ITEMS` 只在未达到软目标时提高 X 候选的尝试顺序，不能绕过质检或硬凑。认证网页自动化仅用于短期试运行，账号、Cookie、SQLite 会话和代理配置必须保存在服务器 root 私有目录，试运行结束后删除并恢复 GitHub 快照路径。

## 新增或修改来源的检查清单

- 更新对应 JSON 配置或采集器代码。
- 增加成功、超时/异常、无效字段和日期窗口测试。
- 在 `src/collector.py` 中确认 `source_type`、证据字段和合并行为正确。
- 更新本文件、`project_docs/architecture.md`，以及新增环境变量时的 `configuration.md`。
- 运行受影响采集器测试和 `git diff --check`。
