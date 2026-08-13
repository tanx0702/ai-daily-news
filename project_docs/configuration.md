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
| `QUALITY_LLM_API_KEY` / `QUALITY_LLM_MODEL` / `QUALITY_LLM_API_BASE` | 未设置时继承 `LLM_*` | 可选的事实简报语义核验增强；不可用或无效响应时，确定性规则通过的条目使用 `rules_only`；跨语言自动降级要求逐字实体锚点且只含可机械核验语义，不转人工复核 |
| `DAILY_LLM_TIMEOUT` | `90` | 文本摘要单次超时 |
| `QUALITY_GATE_TIMEOUT` | `45` 或显式值 | 质量模型超时 |
| `ENABLE_AI_COVER_GENERATION` | `1` | 无可信原文图时是否调用图片模型 |
| `COVER_RENDER_MODE` | `legacy` | `legacy` 为原文图/AI/本地链；`editorial` 为本地确定性模板 |
| `FORCE_LOCAL_COVER_ON_BAD_IMAGE` | `1` | AI 图质量可疑时改用本地封面 |
| `AI_COVER_MAX_RETRIES` / `IMAGE_GENERATION_TIMEOUT` | `5` / `30` | AI 封面重试和单次超时 |

### 编辑与采集

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DAILY_TOP_N` | `15` | 事实简报上限；仅允许 5-15，5-14 为正常短版 |
| `DAILY_MIN_ITEMS` | `5` | 创建草稿所需的最少唯一事实简报；少于此值 block |
| `DAILY_CANDIDATE_POOL_N` | `45` | 聚类前候选池，必须不小于 `DAILY_TOP_N`；歧义重复项隔离且不可回填 |
| `DAILY_MAX_ITEMS_PER_SOURCE` | `2` | 候选排序偏好，不是最终来源占比阻断 |
| `DAILY_MAX_ITEMS_PER_TOPIC` | `2` | 候选排序偏好；最终重复由事件聚类处理 |
| `DAILY_MIN_PRIMARY_OR_RESEARCH` | `2` | 候选排序偏好，不替代规范来源证据绑定 |
| `SEMANTIC_DEDUP_WINDOW_HOURS` | `48` | 跨来源语义事件去重的发布时间窗口；必须为正数 |
| `SEMANTIC_DEDUP_MAX_LLM_CALLS` | `20` | 聚类和发布前去重共享的质量 LLM 调用预算；允许 0-100，0 表示只用规则和保守隔离 |
| `SEMANTIC_DEDUP_TIMEOUT` | 继承 `QUALITY_GATE_TIMEOUT`，默认 `45` | 单次语义重复复核超时；必须为正数 |
| `DAILY_NEWS_HOURS` | `36` | 新闻时间窗口 |
| `DAILY_ALLOW_UNDATED` | `0` | 是否接受无发布时间候选 |
| `DAILY_RSS_TIMEOUT` | `30` | 单个采集请求超时 |
| `ENABLE_HN_COLLECTOR` / `ENABLE_GITHUB_COLLECTOR` | `1` | HN/GitHub 采集开关 |
| `ENABLE_HF_COLLECTOR` / `ENABLE_ARXIV_COLLECTOR` | `1` | HF/arXiv 采集开关 |
| `ENABLE_X_COLLECTOR` | `1`（代码默认） | X 快照采集开关；省略时按代码默认开启 |
| `X_FEED_URL` | 仓库 `x-feed/x-feed.json` | X 快照 HTTPS 地址 |
| `X_FEED_MAX_AGE_HOURS` | `6` | X 快照最大年龄 |
| `DAILY_X_TARGET_ITEMS` | `min(3, DAILY_X_MAX_ITEMS)` | X 规范来源软目标；达到前优先尝试，未通过质检时不硬凑 |
| `DAILY_X_MAX_ITEMS` | `5` | 最终最多五条可将 X 用作规范来源 |
| `GITHUB_TOKEN` / `HF_TOKEN` | 空 | 可选限流凭证，不写日志 |

生产任务在采集、LLM 和微信等任何外部调用前校验以下硬约束：

```text
5 <= DAILY_MIN_ITEMS <= DAILY_TOP_N <= 15
DAILY_CANDIDATE_POOL_N >= DAILY_TOP_N
0 <= DAILY_X_MAX_ITEMS <= 5
0 <= DAILY_X_TARGET_ITEMS <= DAILY_X_MAX_ITEMS
0 < X_FEED_MAX_AGE_HOURS <= 6
0 < SEMANTIC_DEDUP_WINDOW_HOURS
0 <= SEMANTIC_DEDUP_MAX_LLM_CALLS <= 100
0 < SEMANTIC_DEDUP_TIMEOUT
```

任一约束不满足时，本次运行不会静默修正配置，也不会开始外部调用；`draft_decision` 为 `null`，`DraftExecution` 记录 `failed/invalid_configuration`，进程返回非零。

### 事实核验与草稿

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SKIP_WECHAT_DRAFT` | `0` | 唯一安全干跑边界；`1` 只生成产物并记录 `dry_run`，绝不调用微信草稿 API |
| `WECHAT_DRAFT_TITLE_PREFIX` | `今日要闻` | 草稿标题前缀 |
| `WECHAT_DRAFT_AUTHOR` | `要闻编辑室` | 草稿作者 |

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

### 服务器出网代理

`egress-proxy` 是可选的 Docker 内部 sing-box sidecar。默认不开启，`web` 继续直接出网；服务器需要它时，使用 `docker compose --profile egress-proxy up -d --force-recreate` 启动。代理没有宿主机端口，只有 Compose 网络中的 `web` 可通过 `proxy:7890` 访问。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `COMPOSE_PROFILES` | 空 | 服务器设为 `egress-proxy`，确保常规 `docker compose up` 也启动 sidecar |
| `AI_NEWS_HTTP_PROXY` / `AI_NEWS_HTTPS_PROXY` | 空 | 同时设为 `http://proxy:7890` 后，`web` 的外部 HTTP(S) 请求经 sidecar 转发 |
| `AI_NEWS_NO_PROXY` | `localhost,127.0.0.1,web,nginx,proxy` | 不能经由 sidecar 的容器内地址 |
| `AI_NEWS_PROXY_BINARY_PATH` | 本地不可执行占位文件 | 宿主机私有 sing-box Linux 二进制路径；生产必须使用 root 可读的真实二进制 |
| `AI_NEWS_PROXY_CONFIG_PATH` | 仓库中的阻断样例 | 宿主机私有 sing-box JSON 配置；必须由受限节点链接生成，不得作为 `.env` 值保存 |

生成器 `python -m scripts.generate_sing_box_config` 仅接受单个 `VLESS WebSocket + TLS` 或 `VLESS TCP + Reality` 节点。Reality 节点必须提供 `sni`、`pbk` 和 `sid`，并使用 `headerType=none`。它拒绝跳过 TLS 证书验证、非 `none` 加密、无效端口和未支持的 TLS fingerprint。它只可在服务器写入 `/root/ai-news-proxy/config.json`；订阅 URL、节点 URL、生成配置和二进制均不是仓库资产。漏配私有配置时，仓库内阻断样例会使代理拒绝出网，不会静默直连。

## 安全规则

- `.env.example` 是模板，不是实际密钥；高级模板只复制需要的覆盖项。
- 修改容器环境后执行 `docker compose up -d --force-recreate`。
- API key、AppSecret、微信 Token、Basic Auth 密码和 GitHub/HF token 不得进入 Git、日志或诊断 JSON。
- 订阅 URL、单节点 `vless://` 链接、生成的 sing-box 配置和服务器下载的二进制也不得进入 Git、`.env`、日志、诊断 JSON 或 `docs/`。
- 图片配置缺失时允许本地封面降级；质量模型缺失、超时或响应无效时，确定性事实规则通过的条目统一使用 `rules_only`，质量模型明确语义否决时才重建一次后排除；微信凭证缺失时不能创建草稿或通过生产回调验证。
- `DraftDecision` 是唯一 `create|block` 决策，`DraftExecution` 另行记录执行结果。旧质量门禁开关、来源占比阻断、9 分目标和人工复核不是生产配置或控制。
- 新增环境变量必须同步 `.env.advanced.example`、本文件和必要的运维/测试文档。
