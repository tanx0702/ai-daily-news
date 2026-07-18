# AI Daily News Agent

每日自动采集 AI 新闻，使用 LLM 完成翻译和摘要，生成网页日报与微信公众号草稿。草稿创建后仍由公众号后台手动发布。

## 当前架构

```text
cron (每天 8:00) -> docker compose exec -T web python -m src.main
                                             |
                                             v
                                           docs/
                                           |- index.html
                                           |- cover.jpg
                                           |- latest.json
                                           |- wechat.html
                                           `- archive/

nginx        -> 托管 docs/ 静态文件，反代 /wechat
Flask app.py -> 微信公众号 GET 验证和 POST 消息回调
wechat_draft -> 上传封面并创建微信公众号草稿
```

## 本地开发

```bash
pip install -r requirements.txt
python -m src.main
python app.py
```

日报产物会写入 `docs/index.html`、`docs/latest.json`、
`docs/wechat.html` 与 `docs/archive/<date>.html`。

## Docker 部署

```bash
git clone https://github.com/tanx0702/ai-daily-news.git /opt/ai-news
cd /opt/ai-news

cp .env.example .env
# 填写文本模型、图片模型、微信公众号和域名配置。
# 只有需要调参时，才从 .env.advanced.example 复制单个配置到 .env。

docker compose up -d
```

每日定时任务示例：

```bash
echo '0 8 * * * cd /opt/ai-news && /usr/bin/flock -n /tmp/ai-news-daily.lock docker compose exec -T web python -m src.main >> /opt/ai-news/logs/cron.log 2>&1' | crontab -
```

手动运行并直接查看日志：

```bash
cd /opt/ai-news
docker compose exec -T web python -m src.main
```

## 核心环境变量

首次部署只需要编辑 `.env.example` 中的 11 项：

| 变量 | 用途 |
|------|------|
| `LLM_API_KEY` | 文本摘要和标题生成的 API Key |
| `LLM_MODEL` | 文本模型名称 |
| `LLM_API_BASE` | 文本 OpenAI 兼容 API 地址 |
| `IMAGE_API_KEY` | 封面图片生成 API Key；留空时使用本地封面降级 |
| `IMAGE_MODEL` | 图片模型名称 |
| `IMAGE_API_BASE` | 图片生成 API 地址 |
| `WECHAT_APP_ID` | 公众号 AppID |
| `WECHAT_APP_SECRET` | 公众号 AppSecret |
| `WECHAT_TOKEN` | 公众号回调验证 Token |
| `DOMAIN` | 日报站点域名 |
| `PAGES_URL` | 日报完整 URL |

## 高级环境变量

`.env.advanced.example` 是注释形式的参考文件，不能直接替换 `.env`。
需要覆盖默认值时，只复制所需的一行到现有 `.env`，然后执行：

```bash
docker compose up -d --force-recreate
```

高级变量按以下用途分组：

- `QUALITY_LLM_*`：独立质检模型。未设置时会继承对应的 `LLM_*`，大多数部署不需要填写。
- 日报选择：候选池、来源和主题配额、新闻时效窗口、超时与任务锁。
- 采集源：Hacker News、GitHub、Hugging Face、arXiv 开关，以及可选的 `GITHUB_TOKEN` 和 `HF_TOKEN`。
- 证据质检：发布安全回填、质检超时和 Token 上限。`QUALITY_GATE_STRICT` 仅为兼容旧配置保留，不会阻止当天草稿创建。
- 图片与封面：原文图抓取、AI 封面、安全封面、重试和超时。
- 本地调试：跳过公众号草稿、日志目录、Flask 回调端口和公众号标题展示。

代码仍兼容旧的 `AGNES_*` 与 `OPENAI_*` 变量，供已有部署继续运行；新部署请只使用 `LLM_*`、`IMAGE_*` 与可选的 `QUALITY_LLM_*`，不要混用别名。

## 微信模块边界

| 文件 | 职责 |
|------|------|
| `app.py` | Flask 微信回调服务，处理 URL 验证、用户文本消息和客服回复 |
| `src/wechat_draft.py` | 上传封面素材、生成正文、创建公众号草稿 |
| `src/wechat.py` | 兼容入口；新代码不应继续依赖 |
| `src/tencent_push.py` / `src/tencent_scf/` | 历史 SCF 方案，不属于当前 Docker 主流程 |

## 测试

```bash
python -m pytest -q
```

## 兼容与安全

单条新闻的 high risk 质检结果不会停止整天任务：系统会从备用候选回填，或将证据稀疏的条目降级为原标题、来源和原文链接。不要提交真实 `.env`、API Key、公众号密钥或生成的 `docs/` 产物。
