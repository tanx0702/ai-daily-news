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

`web` 挂载 `docs`、`logs`、`src`、`app.py`、`templates` 和 `config`；`nginx` 只读挂载 `docs`、nginx 模板和 `/etc/letsencrypt`。nginx 依赖 `web` 的 `/health` healthcheck 通过后启动。

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
AI_NEWS_NO_PROXY=localhost,127.0.0.1,web,nginx,proxy
AI_NEWS_PROXY_BINARY_PATH=/root/ai-news-proxy/bin/sing-box
AI_NEWS_PROXY_CONFIG_PATH=/root/ai-news-proxy/config.json
```

不发布 `proxy` 端口，不把上述私有文件复制到仓库或 `docs/`。`nginx` 只连接 `app` 网络，`proxy` 只连接 `egress` 网络，只有双接入的 `web` 可访问 `proxy:7890`。漏配私有配置时，仓库内阻断样例会拒绝出网，不会静默直连。cron 仍执行 `docker compose exec -T web ...`；只要 profile 已启动，`web` 在 cron 中继承的代理环境保持有效。

## 定时任务

cron 在宿主机运行，不在 Compose 容器内运行。生产示例：

```bash
0 8 * * * cd /opt/ai-news && /usr/bin/flock -n /tmp/ai-news-daily.lock docker compose exec -T web python -m src.main >> /opt/ai-news/logs/cron.log 2>&1
```

cron 的 `flock` 与应用内 `DAILY_RUN_LOCK_PATH` 是双层保护：前者防止宿主机命令并发，后者防止应用进程重复执行。锁 TTL 默认 6 小时；只有确认任务异常残留时才调整或清理。

X 快照由 GitHub Actions 独立生成，不在 VPS cron 中执行。`.github/workflows/x-feed.yml` 每 4 小时在 Asia/Shanghai 的 `02:07、06:07、10:07、14:07、18:07、22:07` 触发；06:07 批次为 08:00 日报提供最新快照。若当天日报日志出现 X 快照过期，应先检查该工作流是否已成功发布 `x-feed` 分支的 `x-feed.json`。

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

HTML/JSON/诊断通常会在草稿被阻止时继续生成。查看 `latest.json.draft_decision`、`latest.json.draft_execution` 和 `diagnostics`，不得手动绕过决策。旧 publication、质量状态、来源占比阻断、9 分目标和人工复核不是生产控制。

## 排查顺序

1. `git status --short`，确认没有 `.env`、日志或产物准备提交。
2. `docker compose ps` 和 `docker compose logs web --tail=200`。
   启用出网代理时，同时检查 `docker compose --profile egress-proxy ps` 和 `docker compose logs proxy --tail=100`。
3. 访问 `/health`，确认 Flask、微信回调配置和已保存的草稿决策/执行结果。
4. 检查 `docs/latest.json` 的 `brief_items`、`draft_decision`、`draft_execution`、`diagnostics.source_health`。
5. 查看 `docs/debug/shadow` 中的候选快照和编辑复核结果。
6. 单独运行 `docker compose exec -e SKIP_WECHAT_DRAFT=1 -T web python -m src.main`；不要把真实微信草稿 API 调用当作测试步骤。

## 部署边界

- `docker-compose.yml` + `nginx/nginx.conf.template` 是当前生产主方案。
- `Caddyfile` 是历史/替代反代配置，不与当前 Compose nginx 同时启用。
- TLS 证书由宿主机 `/etc/letsencrypt` 提供；nginx 负责 HTTP 到 HTTPS 跳转和静态文件服务。
- `egress-proxy` 仅是 `web` 的内部 HTTP(S) 出口，不能映射宿主机端口；其节点、配置和二进制只能位于服务器私有目录。
- 不要把 `docs/debug`、`.env`、日志、真实媒体缓存或 API 响应复制到公开站点或提交到 Git。
