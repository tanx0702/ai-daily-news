# 配置与环境变量

## 配置来源

配置由 `python-dotenv` 和 `src.llm_config` 解析。首次部署只复制 `.env.example`；需要改变默认行为时，从 `.env.advanced.example` 复制单个变量到现有 `.env`，不要整体覆盖。

修改 `.env` 后必须重建容器：

```bash
docker compose up -d --force-recreate
```

新配置使用 `LLM_*`、`QUALITY_LLM_*` 和 `IMAGE_*`。代码兼容旧的 `AGNES_*`/`OPENAI_*`，但新部署不要混用。

## 首次部署必填项

`.env.example` 只包含首次部署所需的 11 项：

| 分组 | 变量 | 用途 |
| --- | --- | --- |
| 文本模型 | `LLM_API_KEY` | 摘要/标题生成凭证 |
| 文本模型 | `LLM_MODEL` | 文本模型名称 |
| 文本模型 | `LLM_API_BASE` | OpenAI 兼容文本 API 地址 |
| 图片模型 | `IMAGE_API_KEY` | AI 封面凭证；为空时本地降级 |
| 图片模型 | `IMAGE_MODEL` | 图片模型名称 |
| 图片模型 | `IMAGE_API_BASE` | 图片 API 地址 |
| 微信 | `WECHAT_APP_ID` | 公众号 AppID |
| 微信 | `WECHAT_APP_SECRET` | 公众号 AppSecret |
| 微信 | `WECHAT_TOKEN` | 微信回调签名 Token |
| 站点 | `DOMAIN` | nginx 域名和证书名 |
| 站点 | `PAGES_URL` | 日报公开 URL |

真实 key、secret 和生产 `.env` 不能提交。

## 高级配置

### 模型与封面

| 变量 | 默认/继承 | 说明 |
| --- | --- | --- |
| `QUALITY_LLM_API_KEY` / `QUALITY_LLM_MODEL` / `QUALITY_LLM_API_BASE` | 未设置时继承 `LLM_*` | 质量门禁、证据检查和跨候选复核模型 |
| `DAILY_LLM_TIMEOUT` | `15` | 文本摘要单次超时 |
| `QUALITY_GATE_TIMEOUT` | `45` 或显式值 | 质量模型超时 |
| `QUALITY_GATE_MAX_TOKENS` | `1000` | 质量响应上限 |
| `EDITORIAL_REVIEW_MAX_TOKENS` | `5000` | 编辑复核响应上限 |
| `ENABLE_AI_COVER_GENERATION` | `1` | 无可信原文图时是否调用图片模型 |
| `COVER_RENDER_MODE` | `legacy` | `legacy` 为原文图/AI/本地链；`editorial` 为本地确定性模板 |
| `FORCE_LOCAL_COVER_ON_BAD_IMAGE` | `1` | AI 图质量可疑时改用本地封面 |
| `AI_COVER_MAX_RETRIES` / `IMAGE_GENERATION_TIMEOUT` | `5` / `30` | AI 封面重试和单次超时 |

### 编辑与采集

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DAILY_EDITORIAL_MODE` | `v1` | `v2_assist` 只辅助 v1，异常时回退 |
| `DAILY_TOP_N` | `10` | 目标日报条数 |
| `DAILY_CANDIDATE_POOL_N` | `30` | 初选候选池，需不小于目标条数 |
| `DAILY_MAX_ITEMS_PER_SOURCE` | `2` | 单一来源优先上限 |
| `DAILY_MAX_ITEMS_PER_TOPIC` | `2` | 同主题/事件优先上限 |
| `DAILY_MIN_PRIMARY_OR_RESEARCH` | `2` | 官方/研究来源最低优先条数 |
| `DAILY_NEWS_HOURS` | `36` | 新闻时间窗口 |
| `DAILY_ALLOW_UNDATED` | `0` | 是否接受无发布时间候选 |
| `DAILY_RSS_TIMEOUT` | `30` | 单个采集请求超时 |
| `ENABLE_HN_COLLECTOR` / `ENABLE_GITHUB_COLLECTOR` | `1` | HN/GitHub 采集开关 |
| `ENABLE_HF_COLLECTOR` / `ENABLE_ARXIV_COLLECTOR` | `1` | HF/arXiv 采集开关 |
| `ENABLE_X_COLLECTOR` | `1`（代码默认） | X 快照采集开关；省略时按代码默认开启 |
| `X_FEED_URL` | 仓库 `x-feed/x-feed.json` | X 快照 HTTPS 地址 |
| `X_FEED_MAX_AGE_HOURS` | `6` | X 快照最大年龄 |
| `DAILY_X_MAX_ITEMS` | `5` | 每期 X 候选上限 |
| `GITHUB_TOKEN` / `HF_TOKEN` | 空 | 可选限流凭证，不写日志 |

### 质量与发布

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ENABLE_LLM_QUALITY_GATE` | `1` | 发布前质量复核 |
| `QUALITY_GATE_STRICT` | `0` | 旧兼容标记，不单独决定阻断 |
| `ENABLE_PUBLISH_SAFETY_FILTER` | `1` | high risk 移除和 reserve 回填 |
| `DAILY_SAFETY_RESERVE_N` | `6` | 安全回填候选数 |
| `SKIP_WECHAT_DRAFT` | `0` | `1` 只生成产物，不调用微信草稿 API |
| `WECHAT_DRAFT_TITLE_PREFIX` | `今日要闻` | 草稿标题前缀 |
| `WECHAT_DRAFT_AUTHOR` | `要闻编辑室` | 草稿作者 |
| `WECHAT_USE_AI_TEMPLATE` | `0` | 是否让 LLM 改写微信模板，生产建议关闭 |

### 媒体与服务

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ENABLE_ARTICLE_IMAGE_FETCH` | `1` | 是否解析原文配图 |
| `ARTICLE_IMAGE_TIMEOUT` | `8` | 原文图下载/校验超时 |
| `NEWS_DATA_FILE` | Docker 为 `/app/docs/latest.json` | Flask 最新数据路径 |
| `PORT` | `5000` | 直接运行 Flask 的端口 |
| `APP_TIMEZONE` | `Asia/Shanghai` | 报告日期时区 |
| `LOG_DIR` | `logs` | 应用日志目录 |
| `DAILY_RUN_LOCK_PATH` | `docs/.daily_run.lock` | 运行锁路径 |
| `DAILY_RUN_LOCK_TTL_SECONDS` | `21600` | 运行锁有效期 |
| `ALLOW_INSECURE_WECHAT_TOKEN` | `0` | 仅本地调试跳过签名，生产必须为 0 |
| `EDITORIAL_REVIEW_USERNAME` / `EDITORIAL_REVIEW_PASSWORD` | 空 | 同时设置才启用私有审阅 |
| `SHADOW_HISTORY_DIR` | Docker 为 `/app/docs/debug/shadow` | shadow 和反馈目录 |

## 安全规则

- `.env.example` 是模板，不是实际密钥；高级模板只复制需要的覆盖项。
- 修改容器环境后执行 `docker compose up -d --force-recreate`。
- API key、AppSecret、微信 Token、Basic Auth 密码和 GitHub/HF token 不得进入 Git、日志或诊断 JSON。
- 图片配置缺失时允许本地封面降级；质量模型缺失时继承文本模型；微信凭证缺失时不能创建草稿或通过生产回调验证。
- 新增环境变量必须同步 `.env.advanced.example`、本文件和必要的运维/测试文档。
