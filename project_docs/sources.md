# 新闻来源与采集器

## 来源总览

采集统一由 `src.collector.collect_news()` 编排。RSS 来源由 `config/rss_sources.json` 定义；非 RSS 来源由 `src/collectors/` 下的适配器负责。所有来源最终转换为带 `id`、`title`、`url`、`source`、`source_type`、`published_at`、`summary`、`metrics` 和 `scores` 的候选结构，再统一合并、过滤、事件聚类和排序。展示前必须将每个显示声明绑定到其规范来源证据。

| 来源 | 代码入口 | 配置/开关 | 主要信号和限制 |
| --- | --- | --- | --- |
| RSS | `src/collector.py::_fetch_source` | `config/rss_sources.json`、`DAILY_NEWS_HOURS`、`DAILY_RSS_TIMEOUT` | AI 关键词、发布时间窗口、URL/标题去重；配置中的 `tier`/`region` 参与编辑平衡 |
| Hacker News | `src/collectors/hackernews.py` | `ENABLE_HN_COLLECTOR`、`HN_DETAILS_TIMEOUT` | score/comments 和原文链接；低信号 HN-only 条目降权 |
| GitHub | `src/collectors/github.py` | `ENABLE_GITHUB_COLLECTOR`、可选 `GITHUB_TOKEN` | stars/push 等项目活跃度；只能作为社区/活跃度信号，不等同正式发布 |
| Hugging Face | `src/collectors/huggingface.py` | `ENABLE_HF_COLLECTOR`、可选 `HF_TOKEN` | likes/downloads 和模型卡证据；低信号条目降权 |
| arXiv | `src/collectors/arxiv.py` | `ENABLE_ARXIV_COLLECTOR` | 论文日期、摘要和技术信号；仍需经过时效和编辑筛选 |
| X | `src/collectors/x_feed.py` | `ENABLE_X_COLLECTOR`、`X_FEED_URL`、`X_FEED_MAX_AGE_HOURS`、`DAILY_X_TARGET_ITEMS`、`DAILY_X_MAX_ITEMS` | GitHub Runner 每四小时生成的公开 JSON 快照；最多六小时有效，默认优先尝试形成三条合格 X 简报，最终最多五条可将 X 用作规范来源 |

## RSS 配置

`config/rss_sources.json` 维护新闻源名称、RSS URL、区域和可选来源等级。新增 RSS 源必须：

1. 提供稳定的 RSS/Atom URL 和可追溯来源名称。
2. 确认条目具有可解析发布时间；没有日期的内容默认被过滤。
3. 在 `tests/test_rss_sources.py` 或对应采集测试中覆盖配置结构。
4. 同步本文件和 `project_docs/architecture.md`，必要时同步环境变量说明。

RSS 候选在 `src.collector.py` 中做两级 AI 关键词过滤、发布时间窗口过滤和中文 bigram/英文 Jaccard 标题去重。采集器失败不会阻断其它 RSS 源。

## 社区和研究来源

### Hacker News

`HackerNewsCollector` 先取得 AI 相关条目，再补抓 Hacker News 详情和原文链接。score、comments 和是否有跨源确认只用于热度/可信度评分，绝不能作为新闻摘要或正式发布证据。若 HN 条目有可验证的外链，事实简报使用外链作为规范 URL 和发布者，HN 只记录为发现渠道；只有标题、积分或评论元数据的条目不得进入日报。

### GitHub

`GitHubCollector` 采集近期 AI 项目和仓库活跃度。GitHub 最近 push 只能说明项目活跃，不能被摘要写成“官方发布”或新产品发布；正式发布声明仍需原始官方证据或跨源确认。`GITHUB_TOKEN` 只用于提高 API 限额，不能写入日志。

### Hugging Face

`HuggingFaceCollector` 采集近期模型/论文/社区信号。likes 和 downloads 只影响热度，不能替代模型卡、论文或官方公告等内容证据。接口限流或字段异常时返回空列表并继续日报。

### arXiv

`ArxivCollector` 采集近期 AI 论文，保留论文 URL、摘要和时间。论文是研究来源，不自动等同产品发布；编辑层需要根据主题、影响和可验证性决定是否进入日报。

## X 快照来源

X 不通过生产任务直接调用 X API。生产采集读取 `X_FEED_URL`，默认地址为仓库 `x-feed` 分支中的 `x-feed.json`；该快照由 GitHub Runner/相关工作流生成。工作流按 UTC `07` 分、`02/06/10/14/18/22` 点运行，对应 Asia/Shanghai 的 `02:07、06:07、10:07、14:07、18:07、22:07`；06:07 的快照必须先于 08:00 日报完成发布。

`config/x_sources.json` 是受控账号白名单，账号按 `primary`、`research`、`media` 分层，并标记 `official`。快照中的每条记录必须满足：

- schema 为 `x-feed-v1`，并且顶层包含有效 `generated_at` 和 `tweets` 列表。
- 快照生成时间在 `X_FEED_MAX_AGE_HOURS`（默认 6 小时）内，允许最多 5 分钟的时钟偏差。
- `tweet_id` 为数字，文本、source name、author、source tier 和创建时间非空。
- URL 是 `https://x.com/<handle>/status/<tweet_id>` 或 `www.x.com` 的对应 status URL。
- Runner 将 X GraphQL 的 legacy 日期规范化为 UTC ISO 8601，并保留受限数字的 `thread_id`、`reply_to_id` 和 `quoted_id`；生产 collector 再映射为事实证据的线程关系字段。无效、非 ASCII 或超长 ID 只清空对应关系，不能影响其它候选。
- 转换后的候选 `source_type` 为 `x`，来源名带 `(X)`，官方账号记录 `x_official=True`。

快照下载失败、schema 不合法、时间过期或单条记录不完整时只跳过对应 X 候选/整份 X 快照，不影响 RSS、HN、GitHub、HF 和 arXiv。X 候选仍须经过事件聚类和规范来源证据绑定；歧义重复项隔离且不得回填。`DAILY_X_TARGET_ITEMS` 只在未达到软目标时提高 X 候选的尝试顺序，不能绕过质检或硬凑；失败的 X 重建使用携带失败原因和保护锚点的单条内容 LLM 请求，`@handle` 可从规范 X status URL 机械恢复。官方 X 账号可帮助确认品牌声明，但不能越过事实简报核验或 `DraftDecision`。

## 新增或修改来源的检查清单

- 更新对应 JSON 配置或采集器代码。
- 增加成功、超时/异常、无效字段和日期窗口测试。
- 在 `src/collector.py` 中确认 `source_type`、证据字段和合并行为正确。
- 更新本文件、`project_docs/architecture.md`，以及新增环境变量时的 `configuration.md`。
- 运行受影响采集器测试和 `git diff --check`。
