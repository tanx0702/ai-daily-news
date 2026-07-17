# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目概述

AI Daily News Agent — 每日自动采集 AI 新闻，LLM 翻译+摘要，生成 HTML 日报，通过 nginx 页面、微信公众号客服消息和公众号草稿触达用户。

## 架构

VPS（Docker Compose 部署）：
```
cron（每天 8:00）→ python -m src.main → docs/
                                        ├── index.html     ← nginx 托管 :80/:443
                                        ├── cover.jpg
                                        ├── latest.json    ← Flask 读取
                                        ├── wechat.html    ← 微信正文预览
                                        └── archive/
Flask (app.py :5000) ← nginx 反代 /wechat → 微信服务器回调
certbot → Let's Encrypt SSL 证书 → nginx
```

## 运行方式

### Docker 部署（生产）

```bash
# 1. 服务器上克隆仓库
git clone https://github.com/tanx0702/ai-daily-news.git /opt/ai-news
cd /opt/ai-news

# 2. 创建 .env 文件（参考 .env.example）
cp .env.example .env
vim .env  # 填入你的 AGNES_API_KEY, WECHAT_APP_ID 等

# 3. 启动
docker compose up -d

# 4. 设置每日定时任务
echo '0 8 * * * cd /opt/ai-news && /usr/bin/flock -n /tmp/ai-news-daily.lock docker compose exec -T web python -m src.main >> /opt/ai-news/logs/cron.log 2>&1' | crontab -
```

如果希望发布前质检出现 high risk 时不要创建微信草稿，在服务器 `.env` 中设置：

```bash
QUALITY_GATE_STRICT=1
```

### 本地开发

```bash
pip install -r requirements.txt
python -m src.main        # 生成 docs/index.html
python app.py             # 启动 Flask（:5000，用于调试微信回调）
```

## 6 步管道（src/main.py）

```
1. collector.collect_news()      → RSS 采集 + AI 关键词过滤 + 去重 + 热度评分，返回 top_n 条
2. summarizer.summarize_news()   → LLM 批量翻译标题 + 中文摘要（BATCH_SIZE=5）
3. generator.render_daily_html() → Jinja2 渲染内嵌 HTML 模板
4. cover.generate_cover_from_news() → AI 封面图（失败降级到 Pillow 渐变色）
5. 保存 docs/latest.json        → 供 Flask 微信服务读取
6. wechat_draft.publish_daily_article() → 创建微信草稿（后台手动发布）
```

## 关键模块

| 模块 | 职责 | 关键实现 |
|------|------|----------|
| `src/collector.py` | RSS 采集 | 两级 AI 关键词过滤，中文 bigram / 英文 Jaccard 去重 |
| `src/summarizer.py` | LLM 摘要 | 批量 5 条/次，要求 index 强校验；数量/索引异常时整批降级逐条 |
| `src/generator.py` | HTML 渲染 | 模板完全内嵌在 Python 字符串中，Jinja2 从字符串渲染 |
| `src/cover.py` | 封面图 | Agnes Image API → Pillow 渐变色降级（6 套配色按日期 hash） |
| `app.py` | Flask 微信服务 | 双路由（GET 验证/POST 消息），客服消息推送，读 latest.json |
| `src/wechat_draft.py` | 微信公众号草稿创建 | 上传封面素材、生成正文、创建草稿；后台手动发布 |
| `src/wechat.py` | 兼容入口 | 仅转发 `publish_daily_article`，新代码不要继续依赖 |
| `src/tencent_push.py` | SCF 推送（旧） | 历史方案，当前 Docker 主流程不再调用 |
| `src/tencent_scf/` | 腾讯云 SCF（旧） | 历史方案，当前 Docker 主流程不再调用 |

## 部署文件

| 文件 | 用途 |
|------|------|
| `Dockerfile` | Python 3.12-slim + gunicorn 运行 Flask |
| `docker-compose.yml` | web (Flask) + nginx |
| `nginx/nginx.conf` | nginx 配置：静态文件 + 反代 /wechat + SSL |

## 环境变量

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `AGNES_API_KEY` | LLM 摘要 + 封面图 | — |
| `AGNES_MODEL` | 模型名称 | `agnes-2.0-flash` |
| `AGNES_API_BASE` | API 地址 | `https://apihub.agnes-ai.com/v1` |
| `WECHAT_APP_ID` | 公众号 AppID | — |
| `WECHAT_APP_SECRET` | 公众号 AppSecret | — |
| `WECHAT_TOKEN` | 回调验证 Token | — |
| `ALLOW_INSECURE_WECHAT_TOKEN` | 本地调试时允许缺失 Token 跳过验签，生产必须为 `0` | `0` |
| `WECHAT_DRAFT_TITLE_PREFIX` | 公众号草稿标题前缀 | `今日要闻` |
| `WECHAT_DRAFT_AUTHOR` | 公众号草稿作者署名 | `要闻编辑室` |
| `DOMAIN` | 域名 | `tankex.xyz` |
| `PAGES_URL` | 日报完整 URL | `https://{DOMAIN}` |
| `APP_TIMEZONE` | 日报日期展示时区 | `Asia/Shanghai` |
| `DAILY_TOP_N` | 新闻条数 | `10` |
| `DAILY_RSS_TIMEOUT` | RSS 超时(秒) | `30` |
| `HN_DETAILS_TIMEOUT` | Hacker News 明细抓取总超时(秒) | `90` |
| `DAILY_LLM_TIMEOUT` | LLM 超时(秒) | `15` |
| `ENABLE_LLM_QUALITY_GATE` | 是否启用发布前质检 | `true` |
| `QUALITY_GATE_STRICT` | high risk 时是否阻止创建微信草稿 | `false` |
| `ENABLE_ARTICLE_IMAGE_FETCH` | 是否启用正文原文图抓取 | `true` |
| `ENABLE_AI_COVER_GENERATION` | 无可信原文图时是否启用 AI 生图封面 | `true` |
| `FORCE_LOCAL_COVER_ON_BAD_IMAGE` | AI 图疑似含错误文字/Logo 时是否改用本地无字封面 | `true` |
| `DAILY_RUN_LOCK_PATH` | 定时任务锁文件路径 | `docs/.daily_run.lock` |

## 开发约定

- Python 3.12+，所有模块使用 `logging`
- 采集/API 失败不中断流程，记录日志继续
- HTML 模板内嵌在 `src/generator.py` 中
- RSS 源在 `config/rss_sources.json` 维护
