# 运行、部署与诊断

## 本地开发

Python 3.12+ 环境中，默认使用安全干跑命令：

```bash
pip install -r requirements.txt
python -m pytest -q
SKIP_WECHAT_DRAFT=1 python -m src.main
python app.py
```

`SKIP_WECHAT_DRAFT=1` 是唯一安全的干跑边界：只生成日报产物和 schema v2 的 `latest.json`，绝不调用公众号草稿 API。被 block 或草稿执行失败的运行仍返回非零：

```bash
SKIP_WECHAT_DRAFT=1 python -m src.main
```

PowerShell：

```powershell
$env:SKIP_WECHAT_DRAFT='1'
python -m src.main
```

`python -m src.main` 负责采编和产物；在生产定时任务中且 `DraftDecision=create` 时才会创建草稿。`python app.py` 只启动 Flask，不会自动采集新闻。

## Docker Compose

当前生产主方案有两个常驻服务和一个按需启用的出网服务：

```text
web       Python 3.12-slim + Gunicorn 单 worker，提供 Flask
nginx     nginx:alpine，提供 HTTPS、静态 docs 和回调/API 反代
proxy     可选 sing-box sidecar，只在 egress-proxy profile 启用时供 web 出网
```

首次启动：

```bash
cp .env.example .env
# 填写 LLM、图片、微信和 DOMAIN/PAGES_URL
docker compose up -d
```

修改 `.env` 后：

```bash
docker compose up -d --force-recreate
```

`web` 挂载 `docs`、`logs`、`src`、`app.py`、`templates`、`config`、可写的 `runtime` 和只读的 `AI_NEWS_X_FEED_DIR` 本机快照目录；`runtime/source-state.db` 因此在容器重建后仍保留。`nginx` 只读挂载 `docs`、nginx 模板和 `/etc/letsencrypt`。nginx 依赖 `web` 的 `/health` healthcheck 通过后启动。

### 服务器出网代理

当服务器无法直接访问 RSS、模型或镜像仓库，但已有可用的 VLESS 节点时，使用单节点静态配置启动 sidecar。订阅 URL 不能用于首次启动，因为 sidecar 尚未运行时无法下载订阅。

仅接受 `WebSocket + TLS` 或 `TCP + Reality` 节点；后者必须包含 `sni`、`pbk`、`sid` 且使用 `headerType=none`。服务器保留以下私有文件：

```text
/root/ai-news-proxy/vless-node.txt       单个 vless:// 链接，权限 600
/root/ai-news-proxy/bin/sing-box         Linux amd64 静态二进制，权限 700
/root/ai-news-proxy/config.json          生成配置，权限 600
```

从仓库根目录生成配置并启动服务：

```bash
python -m scripts.generate_sing_box_config /root/ai-news-proxy/vless-node.txt /root/ai-news-proxy/config.json
chmod 600 /root/ai-news-proxy/config.json
docker compose --profile egress-proxy up -d --force-recreate
```

在 `.env` 中仅填写无凭据的路径和内部代理地址：

```dotenv
COMPOSE_PROFILES=egress-proxy
AI_NEWS_HTTP_PROXY=http://proxy:7890
AI_NEWS_HTTPS_PROXY=http://proxy:7890
AI_NEWS_NO_PROXY=localhost,127.0.0.1,web,nginx,proxy,api.weixin.qq.com
AI_NEWS_PROXY_BINARY_PATH=/root/ai-news-proxy/bin/sing-box
AI_NEWS_PROXY_CONFIG_PATH=/root/ai-news-proxy/config.json
```

不发布 `proxy` 端口，不把上述私有文件复制到仓库或 `docs/`。`app` 网络保持 internal；`nginx` 同时连接 internal 的 `app` 和仅用于发布 `80/443` 的 `public` 网络，`web` 仍通过 `app` 访问 Flask。`proxy` 只连接 `egress` 网络，只有双接入的 `web` 可访问 `proxy:7890`。默认 `NO_PROXY` 包含 `api.weixin.qq.com`，让微信草稿 API 走服务器直连并使用公众号 IP 白名单；其它外部请求仍可通过机场代理。漏配私有配置时，仓库内阻断样例会拒绝出网，不会静默直连。cron 仍执行 `docker compose exec -T web ...`；只要 profile 已启动，`web` 在 cron 中继承的代理环境保持有效。

## 定时任务

cron 在宿主机运行，不在 Compose 容器内运行。生产示例：

```bash
0 8 * * * cd /opt/ai-news && /usr/bin/flock -n /tmp/ai-news-daily.lock docker compose exec -T web python -m src.main >> /opt/ai-news/logs/cron.log 2>&1
```

cron 的 `flock` 与应用内 `DAILY_RUN_LOCK_PATH` 是双层保护：前者防止宿主机命令并发，后者防止应用进程重复执行。锁 TTL 默认 6 小时；只有确认任务异常残留时才调整或清理。

生产任务使用 `DAILY_NEWS_HOURS=36` 作为默认新闻窗口。服务器曾为排查临时覆盖为 `24` 时，应从 `.env` 删除该覆盖或恢复为 `36`，然后执行 `docker compose up -d --force-recreate`。

候选池诊断中的 `publishability_preflight_*` 用于判断采集后是否有足够的可发布事件。`publishability_preflight_rejected` 较高而 `publishability_preflight_passed` 较低表示候选主要是教程、观点、活动元数据或缺少完整事件动作，不应通过降低最终事实门禁解决。

`diagnostics.source_health` 记录每个 RSS 源的最近状态、连续失败次数、条目数和延迟。`status=empty` 表示 feed 可访问但本轮没有解析到条目，`timeout`/`error`/`invalid_feed` 表示外部请求或格式故障；这些状态用于定位供给问题，不会改变事实门禁。需要迁移账本时可通过 `SOURCE_STATE_DB_PATH` 指向 `runtime/` 下的其它私有路径。

默认 X 快照由 GitHub Actions 独立生成，不在 VPS cron 中执行。临时认证试运行时，VPS 额外在日报前生成本机快照，生产容器通过 `X_FEED_LOCAL_PATH` 优先读取；本机快照失败或过期会自动回退 GitHub 快照。`.github/workflows/x-feed.yml` 仍每 4 小时在 Asia/Shanghai 的 `02:07、06:07、10:07、14:07、18:07、22:07` 触发，作为回滚路径。试运行 cron 示例：

```bash
7 2,6,10,14,18,22 * * * /usr/bin/flock -n /tmp/ai-news-x.lock /opt/ai-news/scripts/run_x_authenticated_feed.sh >> /root/ai-news-x-poc/collector.log 2>&1
```

该 cron 只适用于短期试运行；Cookie、SQLite 会话、日志和输出目录必须保持 root-only，试运行结束后移除 cron 并取消 `X_FEED_LOCAL_PATH`。

若工作流报告所有来源 `tweet_count=0`，先检查报告中的 `extraction_method` 和失败截图。网页探针在 GraphQL 响应为空时会读取已渲染的公开 `cellInnerDiv`/`article` 卡片，并要求正文和 `/status/` 数字 ID；截图能看到推文而报告仍为 0 时，优先检查 `scripts/x_web_probe.py` 的 DOM 选择器和 Runner 浏览器版本。X 工作流仍会发布带当前时间的空快照，生产任务会跳过 X，不应回退使用过期快照。

## HTTP 入口

| 地址 | 处理者 | 用途 |
| --- | --- | --- |
| `/`、`/archive/*`、`/cover.jpg`、`/wechat.html` | nginx 静态文件 | 日报和媒体 |
| `/wechat` | nginx -> Flask | 微信 GET 验证和 POST XML 回调 |
| `/health` | nginx -> Flask | Compose healthcheck 和运维状态 |
| `/api/news` | nginx -> Flask | 读取 `latest.json` 的 JSON 接口 |
| `/editorial-review` | nginx -> Flask | Basic Auth 保护的 shadow 审阅 |
| `/editorial-review/feedback` | nginx -> Flask | 记录人工标签和备注 |
| `/debug/*` | nginx | 固定返回 404，不公开诊断 |

公众号草稿中的链接只指向每条事实简报的规范原始来源。`PAGES_URL` 仍用于公开日报站点配置，但不会写入微信 `content_source_url`，正文也不展示“查看完整日报”入口。`docs/wechat.html` 是上传前预览；真实草稿会使用媒体解析后的展示项，在封面和新闻配图上传后重新渲染，正文首图必须使用微信返回的 CDN URL。指定的 `docs/cover.jpg` 缺失、上传结果缺少 `media_id`/URL 或正文仍引用公网封面时，本次草稿执行失败且不调用 draft/add。若 draft/add 超时、断连或响应无法确认，执行结果记为 `draft_create_uncertain` 且不自动重试，维护者应先在公众号后台确认是否已生成草稿。

微信回调必须校验 `WECHAT_TOKEN` 签名；`ALLOW_INSECURE_WECHAT_TOKEN=1` 只能用于本地排查。editorial review 只有用户名和密码同时配置时才启用，不能使用 URL token。

## 运行时产物

| 路径 | 内容 | 处理规则 |
| --- | --- | --- |
| `docs/index.html` | 最新日报 | 公开页面 |
| `docs/archive/*.html` | 日期归档 | 公开页面 |
| `docs/wechat.html` | 微信正文预览 | 本地检查排版 |
| `docs/cover.jpg` | 当前封面 | 日报/草稿共用 |
| `docs/latest.json` | schema v2 的 `brief_items`、`draft_decision`、`draft_execution` 和诊断 | Flask 主要输入；v1 仅冷启动只读兼容 |
| `docs/debug/` | 聚类、核验、媒体和 shadow 诊断 | nginx 不公开，不提交；不改变简报或决策 |
| `docs/media/` | 原文媒体缓存 | 运行时缓存，不提交 |
| `logs/` | 应用和 cron 日志 | 不提交，检查时脱敏 |
| `runtime/source-state.db` | RSS 来源健康 SQLite 账本 | 私有运行状态；Compose 持久化，不提交，不作为事实证据 |

HTML/JSON/诊断通常会在草稿被阻止时继续生成。查看 `latest.json.draft_decision`、`latest.json.draft_execution` 和 `diagnostics`，不得手动绕过决策。旧 publication、质量状态、来源占比阻断、9 分目标和人工复核不是生产控制。

## 排查顺序

1. `git status --short`，确认没有 `.env`、日志或产物准备提交。
2. `docker compose ps` 和 `docker compose logs web --tail=200`。
   启用出网代理时，同时检查 `docker compose --profile egress-proxy ps` 和 `docker compose logs proxy --tail=100`。
3. 访问 `/health`，确认 Flask、微信回调配置和已保存的草稿决策/执行结果。
4. 检查 `docs/latest.json` 的 `brief_items`、`brief_mode`、`draft_decision`、`draft_execution`、`diagnostics.source_health`。
5. 查看 `docs/debug/<date>-briefing.json` 的摘要删除轨迹和 `docs/debug/shadow` 中的候选快照。
6. 单独运行 `docker compose exec -e SKIP_WECHAT_DRAFT=1 -T web python -m src.main`；不要把真实微信草稿 API 调用当作测试步骤。

## 部署边界

- `docker-compose.yml` + `nginx/nginx.conf.template` 是当前生产主方案。
- `Caddyfile` 是历史/替代反代配置，不与当前 Compose nginx 同时启用。
- TLS 证书由宿主机 `/etc/letsencrypt` 提供；nginx 负责 HTTP 到 HTTPS 跳转和静态文件服务。
- `egress-proxy` 仅是 `web` 的内部 HTTP(S) 出口，不能映射宿主机端口；其节点、配置和二进制只能位于服务器私有目录。
- 不要把 `docs/debug`、`.env`、日志、真实媒体缓存或 API 响应复制到公开站点或提交到 Git。
