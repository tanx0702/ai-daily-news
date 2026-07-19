# AGENTS.md

This file provides guidance to Codex when working with this repository.

## 项目概述

AI Daily News Agent 每日自动采集 AI 新闻，使用 LLM 翻译和摘要，生成 HTML 日报，并通过 nginx 页面、微信公众号客服消息和公众号草稿触达用户。

## 架构

VPS 使用 Docker Compose 部署：

```text
cron (每天 8:00) -> python -m src.main -> docs/
                                            |- index.html
                                            |- cover.jpg
                                            |- latest.json
                                            |- wechat.html
                                            `- archive/
Flask (app.py :5000) <- nginx 反代 /wechat <- 微信服务器回调
certbot -> Let's Encrypt SSL -> nginx
```

## 运行方式

### Docker 部署（生产）

```bash
# 1. 服务器上克隆仓库
git clone https://github.com/tanx0702/ai-daily-news.git /opt/ai-news
cd /opt/ai-news

# 2. 创建核心配置
cp .env.example .env
vim .env
# 仅填写文本模型、图片模型、微信公众号和域名。
# 调参时，从 .env.advanced.example 复制单个变量到 .env。

# 3. 启动
docker compose up -d

# 4. 设置每日定时任务
echo '0 8 * * * cd /opt/ai-news && /usr/bin/flock -n /tmp/ai-news-daily.lock docker compose exec -T web python -m src.main >> /opt/ai-news/logs/cron.log 2>&1' | crontab -
```

修改 `.env` 后必须重新创建容器：

```bash
docker compose up -d --force-recreate
```

日报 HTML 和诊断文件不会因单条 high risk 或候选不足而停止生成。高风险条目会从通过质检的备用候选回填；但当最终可发布条目少于 6 条、单一来源超过一半、存在非 `ready` 条目或 LLM 质检失败时，不创建微信公众号草稿，任务以非零状态退出。`QUALITY_GATE_STRICT` 仅为兼容旧配置保留，不再单独决定整天任务是否阻断。

### 本地开发

```bash
pip install -r requirements.txt
python -m src.main
python app.py
```

## 6 步管道（src/main.py）

```text
1. collector.collect_news()
   -> RSS 采集、AI 关键词过滤、去重、热度评分，并保留候选池
1.25 editorial_quality.annotate_editorial_candidates()
   -> 校验来源证据、识别事件键，并为候选计算可解释的编辑分
1.5 editorial_selection.select_editorial_candidates()
   -> 依来源、主题和独立事件配额选出日报条目与备用候选
2. summarizer.summarize_news()
   -> LLM 批量翻译标题和中文摘要（BATCH_SIZE=5），同时处理备用候选
2.25 editorial_review.review_editorial_candidates()
   -> 质量模型跨候选归并同一事件并重排；GitHub 近期 push 只能作为项目活跃度
2.5 quality_gate.review_daily()
   -> 按原始证据质检，high risk 单条移除并从合格备用候选回填
2.6 editorial_quality.assess_daily_edition()
   -> 生成 0-10 整期编辑质量诊断；9 分为人工发布建议目标，不影响草稿生成逻辑
3. generator.render_daily_html()
   -> Jinja2 渲染内嵌 HTML 模板
4. cover.generate_cover_from_news()
   -> 原文图优先，AI 封面和 Pillow 本地封面降级
5. 保存 docs/latest.json
   -> Flask 微信服务读取
6. wechat_draft.publish_daily_article()
   -> 创建微信草稿，后台手动发布
```

## 关键模块

| 模块 | 职责 | 关键实现 |
|------|------|----------|
| `src/collector.py` | RSS 采集 | 两级 AI 关键词过滤，中文 bigram / 英文 Jaccard 去重，来源与风险题材均衡 |
| `src/editorial_quality.py` | 编辑质量信号 | 来源证据、事件键、候选分与整期 9 分诊断 |
| `src/editorial_review.py` | 跨候选编辑复核 | 使用质检模型归并相同事件并重排候选，不新增新闻事实 |
| `src/llm_config.py` | LLM 配置解析 | 文本、质检、图片模型分开解析，优先 `LLM_*` / `QUALITY_LLM_*` / `IMAGE_*`，兼容旧别名 |
| `src/summarizer.py` | LLM 摘要 | 批量 5 条/次；数量或索引异常时整批降级逐条；GitHub 活跃项目不写成正式发布 |
| `src/quality_gate.py` | 发布前质检 | LLM/本地规则标记风险；high risk 单条可移除并回填 |
| `src/generator.py` | HTML 渲染 | 模板完全内嵌在 Python 字符串中 |
| `src/cover.py` | 封面图 | 原文图、可配置图片 API、Pillow 本地封面的降级链 |
| `app.py` | Flask 微信服务 | 双路由：GET 验证和 POST 消息，读取 latest.json |
| `src/wechat_draft.py` | 微信草稿创建 | 上传封面、生成正文、创建草稿 |
| `src/wechat.py` | 兼容入口 | 仅转发 `publish_daily_article`，新代码不要继续依赖 |
| `src/tencent_push.py` / `src/tencent_scf/` | 历史方案 | 当前 Docker 主流程不再调用 |

## 部署文件

| 文件 | 用途 |
|------|------|
| `Dockerfile` | Python 3.12-slim + gunicorn 运行 Flask |
| `docker-compose.yml` | web (Flask) + nginx |
| `nginx/nginx.conf.template` | 由 `DOMAIN` 渲染的静态文件、/wechat 反代和 SSL 配置 |

## 环境变量

`.env.example` 是首次部署时唯一需要复制的模板，只有以下 11 项激活变量：

| 分组 | 变量 |
|------|------|
| 文本模型 | `LLM_API_KEY`、`LLM_MODEL`、`LLM_API_BASE` |
| 图片模型 | `IMAGE_API_KEY`、`IMAGE_MODEL`、`IMAGE_API_BASE` |
| 微信公众号 | `WECHAT_APP_ID`、`WECHAT_APP_SECRET`、`WECHAT_TOKEN` |
| 站点地址 | `DOMAIN`、`PAGES_URL` |

`.env.advanced.example` 是注释形式的参考配置，不能覆盖 `.env`。只有需要改变默认行为时才复制单个变量到 `.env`：

- `QUALITY_LLM_*`：独立质检模型；未设置时继承 `LLM_*`。
- 编辑与采集：条数、候选池、来源和主题配额、时效窗口、采集器、可选 Token、超时和任务锁。
- 质检与发布：质量门禁、备用回填、`QUALITY_GATE_STRICT` 兼容标记和 LLM 限制。
- 媒体：正文原文图、AI 封面、封面安全策略、重试和超时。
- 调试与展示：干跑、日志、Flask 回调、草稿标题/作者和页脚链接。

代码继续兼容 `AGNES_*` 和 `OPENAI_*`，但新配置不得混用这些旧别名。图片配置缺失时会降级为本地封面；质检配置缺失时使用文本模型。

## 开发约定

- Python 3.12+，所有模块使用 `logging`。
- 采集和 API 失败不能中断整条流程，记录日志后继续。
- HTML 模板内嵌在 `src/generator.py`。
- RSS 源在 `config/rss_sources.json` 维护。
- 不要提交真实 `.env`、密钥、日志或 `docs/` 生成产物。
