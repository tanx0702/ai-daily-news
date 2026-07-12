# AI Daily News Agent

每日自动采集 AI 新闻，经过 LLM 翻译和摘要后生成 HTML 日报，并通过微信公众号客服消息与公众号草稿能力触达用户。

## 当前架构

生产部署基于 Docker Compose：

```text
cron（每天 8:00） -> docker compose exec web python -m src.main
                                      |
                                      v
                                  docs/
                                  ├── index.html
                                  ├── cover.jpg
                                  ├── latest.json
                                  ├── wechat.html
                                  └── archive/

nginx       -> 托管 docs/ 静态文件，并反代 /wechat
Flask app.py -> 处理微信公众号 GET 验证和 POST 消息回调
src.wechat_draft -> 创建微信公众号草稿，后台手动发布
```

## 功能

- 多源采集：RSS、Hacker News、GitHub、Hugging Face、arXiv 等候选源
- 编辑筛选：AI 关键词过滤、去重、热度评分、最终编辑去重
- 中文摘要：LLM 批量翻译标题并生成克制的中文新闻摘要
- 发布前质检：对高风险品牌声明、传闻和不确定表述进行降级或拦截
- 页面生成：生成 `docs/index.html`、历史归档和微信预览 HTML
- 封面生成：优先调用 Agnes Image API，失败时可降级到本地封面
- 微信触达：`app.py` 处理客服消息；`src.wechat_draft` 创建公众号草稿

## 本地开发

```bash
pip install -r requirements.txt
python -m src.main
python app.py
```

默认输出：

- `docs/index.html`：完整日报
- `docs/latest.json`：Flask 回调读取的数据
- `docs/wechat.html`：公众号正文预览
- `docs/archive/<date>.html`：历史归档

## Docker 部署

```bash
cp .env.example .env
# 填写 AGNES_API_KEY、WECHAT_APP_ID、WECHAT_APP_SECRET、WECHAT_TOKEN 等

docker compose up -d
```

服务器 cron 示例：

```bash
0 8 * * * cd /opt/ai-news && docker compose exec web python -m src.main >> /var/log/ai-news.log 2>&1
```

## 微信模块边界

| 文件 | 责任 |
|------|------|
| `app.py` | Flask 微信回调服务。处理 URL 验证、用户文本消息和客服消息回复。 |
| `src/wechat_draft.py` | 微信公众号草稿创建。上传封面素材、生成正文、创建草稿。 |
| `src/wechat.py` | 兼容入口，仅转发 `publish_daily_article`，新代码不要继续依赖。 |
| `src/tencent_push.py` / `src/tencent_scf/` | 旧 SCF 方案，保留历史，不再由当前 Docker 主流程调用。 |

## 关键环境变量

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `AGNES_API_KEY` | LLM 摘要和封面图 | - |
| `AGNES_MODEL` | LLM 模型名称 | `agnes-2.0-flash` |
| `AGNES_API_BASE` | OpenAI 兼容 API 地址 | `https://apihub.agnes-ai.com/v1` |
| `WECHAT_APP_ID` | 公众号 AppID | - |
| `WECHAT_APP_SECRET` | 公众号 AppSecret | - |
| `WECHAT_TOKEN` | 微信回调验证 Token | - |
| `ALLOW_INSECURE_WECHAT_TOKEN` | 本地调试时允许缺失 Token 跳过验签，生产必须为 `0` | `0` |
| `DOMAIN` | 站点域名 | `tankex.xyz` |
| `PAGES_URL` | 日报完整 URL | `https://{DOMAIN}` |
| `APP_TIMEZONE` | 日报日期展示时区 | `Asia/Shanghai` |
| `DAILY_TOP_N` | 入选新闻条数 | `10` |
| `DAILY_RSS_TIMEOUT` | RSS 超时秒数 | `30` |
| `DAILY_LLM_TIMEOUT` | LLM 超时秒数 | `15` |
| `ENABLE_LLM_QUALITY_GATE` | 是否启用发布前质检 | `true` |
| `QUALITY_GATE_STRICT` | 高风险时是否阻止创建微信草稿 | `false` |
| `DAILY_RUN_LOCK_PATH` | 定时任务锁文件路径 | `docs/.daily_run.lock` |

## 测试

```bash
python -m unittest discover -s tests
```

如果本地 Python 环境未安装 Flask，`app.py` 相关测试会被明确跳过；完整依赖安装后会执行。

## spec-superflow

项目已接入 `spec-superflow`。较大的功能、重构和发布链路调整可从 `workflow-start` 开始；详细说明见 `SPEC_SUPERFLOW.md`。
