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
vim .env  # 填入你的 LLM_API_KEY/LLM_MODEL/LLM_API_BASE、IMAGE_*、WECHAT_* 等

# 3. 启动
docker compose up -d

# 4. 设置每日定时任务
echo '0 8 * * * cd /opt/ai-news && /usr/bin/flock -n /tmp/ai-news-daily.lock docker compose exec -T web python -m src.main >> /opt/ai-news/logs/cron.log 2>&1' | crontab -
```

日报不会因单条 high risk 或候选不足停止创建微信草稿。高风险条目从通过质检的备用候选回填；证据不足的条目降级为仅原标题、来源和原文链接。`QUALITY_GATE_STRICT` 仅为兼容旧配置保留，不再阻断整天任务。

### 本地开发

```bash
pip install -r requirements.txt
python -m src.main        # 生成 docs/index.html
python app.py             # 启动 Flask（:5000，用于调试微信回调）
```

## 6 步管道（src/main.py）

```
1. collector.collect_news()      → RSS 采集 + AI 关键词过滤 + 去重 + 热度评分；保留候选池
1.5 editorial_selection.select_editorial_candidates() → 依来源/主题配额选出日报条目和备用候选
2. summarizer.summarize_news()   → LLM 批量翻译标题 + 中文摘要（BATCH_SIZE=5），同时处理备用候选
2.5 quality_gate.review_daily()  → 按原始证据质检；high risk 单条移除并从合格备用候选回填，证据稀疏时 source_only 降级
3. generator.render_daily_html() → Jinja2 渲染内嵌 HTML 模板
4. cover.generate_cover_from_news() → AI 封面图（失败降级到 Pillow 渐变色）
5. 保存 docs/latest.json        → 供 Flask 微信服务读取
6. wechat_draft.publish_daily_article() → 创建微信草稿（后台手动发布）
```

## 关键模块

| 模块 | 职责 | 关键实现 |
|------|------|----------|
| `src/collector.py` | RSS 采集 | 两级 AI 关键词过滤，中文 bigram / 英文 Jaccard 去重，单发布源/风险题材均衡 |
| `src/llm_config.py` | LLM 配置解析 | 文本/质检/图片模型分开解析，`LLM_*` / `QUALITY_LLM_*` / `IMAGE_*` 优先，兼容 `AGNES_*` / `OPENAI_*` |
| `src/summarizer.py` | LLM 摘要 | 批量 5 条/次，要求 index 强校验；数量/索引异常时整批降级逐条 |
| `src/quality_gate.py` | 发布前质检 | LLM/本地规则标记风险；high risk 单条可从发布列表移除并回填 |
| `src/generator.py` | HTML 渲染 | 模板完全内嵌在 Python 字符串中，Jinja2 从字符串渲染 |
| `src/cover.py` | 封面图 | 可配置图片生成 API → Pillow 渐变色降级（6 套配色按日期 hash） |
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
| `LLM_API_KEY` | 文本 LLM Key，用于摘要、今日重点和封面标题；兼容读取 `AGNES_API_KEY` / `OPENAI_API_KEY` | — |
| `LLM_MODEL` | 文本 LLM 模型名称；兼容读取 `AGNES_MODEL` / `OPENAI_MODEL` | `agnes-2.0-flash` |
| `LLM_API_BASE` | 文本 OpenAI 兼容 API 地址；兼容读取 `AGNES_API_BASE` / `OPENAI_API_BASE` | `https://apihub.agnes-ai.com/v1` |
| `QUALITY_LLM_API_KEY` | 发布前质检 LLM Key | 空时继承 `LLM_API_KEY` |
| `QUALITY_LLM_MODEL` | 发布前质检模型名称 | 空时继承 `LLM_MODEL` |
| `QUALITY_LLM_API_BASE` | 发布前质检 API 地址 | 空时继承 `LLM_API_BASE` |
| `IMAGE_API_KEY` | 图片生成 API Key，用于 AI 封面图；兼容读取 `AGNES_IMAGE_API_KEY` / `AGNES_API_KEY` / `OPENAI_IMAGE_API_KEY` / `OPENAI_API_KEY` | — |
| `IMAGE_MODEL` | 图片生成模型名称；兼容读取 `AGNES_IMAGE_MODEL` / `OPENAI_IMAGE_MODEL` | `agnes-image-2.1-flash` |
| `IMAGE_API_BASE` | 图片生成 API 地址；兼容读取 `AGNES_IMAGE_API_BASE` / `AGNES_API_BASE` / `OPENAI_IMAGE_API_BASE` / `OPENAI_API_BASE`；可填基础地址、`/v1` 地址或完整 `/v1/images/generations` endpoint | `https://apihub.agnes-ai.com` |
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
| `DAILY_SAFETY_RESERVE_N` | 旧的采集保底数量；候选池下限仍会兼容该值 | `6` |
| `DAILY_CANDIDATE_POOL_N` | 编辑选择和回填候选池数量 | `30` |
| `DAILY_MAX_ITEMS_PER_SOURCE` | 单一来源优先最多入选条数 | `2` |
| `DAILY_MAX_ITEMS_PER_TOPIC` | 单一主题优先最多入选条数 | `2` |
| `DAILY_MIN_PRIMARY_OR_RESEARCH` | 官方/研究来源优先最低条数 | `2` |
| `DAILY_RSS_TIMEOUT` | RSS 超时(秒) | `30` |
| `HN_DETAILS_TIMEOUT` | Hacker News 明细抓取总超时(秒) | `90` |
| `DAILY_LLM_TIMEOUT` | LLM 超时(秒) | `15` |
| `ENABLE_LLM_QUALITY_GATE` | 是否启用发布前质检 | `true` |
| `ENABLE_PUBLISH_SAFETY_FILTER` | high risk 单条是否移除并从备用候选回填 | `true` |
| `QUALITY_GATE_STRICT` | 旧配置兼容标记，不再阻止整天微信草稿 | `false` |
| `ENABLE_ARTICLE_IMAGE_FETCH` | 是否启用正文原文图抓取 | `true` |
| `ARTICLE_IMAGE_TIMEOUT` | 正文图片下载、解码和校验超时(秒) | `8` |
| `ENABLE_AI_COVER_GENERATION` | 无可信原文图时是否启用 AI 生图封面 | `true` |
| `FORCE_LOCAL_COVER_ON_BAD_IMAGE` | AI 图疑似含错误文字/Logo 时是否改用本地无字封面 | `true` |
| `AI_COVER_MAX_RETRIES` | 图片服务临时错误最大重试次数 | `5` |
| `IMAGE_GENERATION_TIMEOUT` | 单次图片生成请求硬超时(秒) | `30` |
| `SKIP_WECHAT_DRAFT` | 本地/CI 干跑：保留产物但跳过草稿 API | `false` |
| `DAILY_RUN_LOCK_PATH` | 定时任务锁文件路径 | `docs/.daily_run.lock` |

## 开发约定

- Python 3.12+，所有模块使用 `logging`
- 采集/API 失败不中断流程，记录日志继续
- HTML 模板内嵌在 `src/generator.py` 中
- RSS 源在 `config/rss_sources.json` 维护
