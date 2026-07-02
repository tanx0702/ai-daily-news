# 🤖 AI Daily News Agent

每天自动采集 AI 圈重要新闻，生成精美日报，通过微信推送。

> 用户发送「日报」到公众号，即可收到当天 AI 新闻摘要；点击链接查看完整日报。

## 功能

- **多源采集** — 从 Hacker News、TechCrunch、The Verge、Ars Technica、36氪、机器之心等 RSS 源并行抓取
- **智能筛选** — 自动过滤非 AI 相关内容，去重排序
- **LLM 翻译 + 摘要** — 调用 Agnes-2.0-Flash 将英文标题翻译为中文，并生成中文摘要
- **AI 封面** — 调用 Agnes Image 2.1 Flash 根据当日新闻自动生成封面图
- **精美日报** — 生成手机端适配的 HTML 日报页面，支持暗色模式
- **历史归档** — 每天自动归档，首页展示最近 7 天历史
- **微信推送** — 用户发送「日报」关键词，通过客服消息接口推送当天新闻摘要
- **零运维** — 完全基于 GitHub Actions + Cloudflare Workers，免费运行

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/YOUR_USERNAME/ai-daily-news.git
cd ai-daily-news
```

### 2. 配置 GitHub Pages

1. 进入仓库 Settings → Pages
2. Source 选择 `gh-pages` 分支的 `/ (root)` 文件夹
3. 保存后页面将部署到 `https://YOUR_USERNAME.github.io/ai-daily-news/`

### 3. 配置 GitHub Secrets

进入仓库 Settings → Secrets and variables → Actions，添加：

| Secret | 说明 |
|--------|------|
| `AGNES_API_KEY` | Agnes AI API Key（用于新闻摘要 + 封面图） |
| `AGNES_MODEL` | 模型名称，默认 `agnes-2.0-flash` |
| `AGNES_API_BASE` | API 基础地址（可选，默认 `https://apihub.agnes-ai.com/v1`） |
| `WECHAT_APP_ID` | 微信公众号 AppID |
| `WECHAT_APP_SECRET` | 微信公众号 AppSecret |
| `NEWS_WORKER_URL` | Cloudflare Worker 地址（用于微信个人推送） |

### 4. 配置 Cloudflare Worker

1. 注册 [Cloudflare](https://dash.cloudflare.com) 账号
2. 购买域名（推荐 `.xyz`，Cloudflare Registrar 首年约 1 元）
3. 创建 Worker，绑定 KV 命名空间
4. 部署 `workers/weixin-worker.js`（见项目代码）
5. 在公众号后台配置服务器地址：`https://<worker>.<domain>.workers.dev/callback`

### 5. 注册微信公众号

1. 打开 https://mp.weixin.qq.com ，注册订阅号
2. 在"开发 → 基本配置"中获取 AppID 和 AppSecret
3. 在"开发 → 服务器配置"中配置回调地址（指向 Cloudflare Worker）

### 6. 手动触发

进入 Actions 页面，点击 "Run workflow" 手动执行一次测试。

## 本地开发

```bash
pip install -r requirements.txt
python -m src.main
```

生成的日报页面保存在 `docs/index.html`。

## RSS 源配置

编辑 `config/rss_sources.json` 添加或移除源：

```json
{
    "sources": [
        {
            "name": "Hacker News AI",
            "url": "https://hnrss.org/frontpage?q=AI+OR+LLM+OR+GPT+OR+Claude",
            "region": "overseas"
        }
    ]
}
```

## 项目结构

```
├── .github/workflows/daily.yml   # GitHub Actions 定时调度
├── config/rss_sources.json       # RSS 源配置
├── docs/                         # 生成的日报 HTML
├── workers/
│   └── weixin-worker.js          # Cloudflare Worker（微信回调 + 新闻存储）
├── src/
│   ├── main.py                  # 主程序
│   ├── collector.py             # RSS 采集
│   ├── summarizer.py            # LLM 翻译 + 摘要
│   ├── cover.py                 # AI 封面图生成
│   ├── generator.py             # HTML 渲染
│   ├── wechat.py                # 微信推送
│   └── wechat_push.py           # 新闻导出到 Worker
├── requirements.txt
└── docs/PRD.md                  # 产品需求文档
```

## 技术栈

| 环节 | 技术 |
|------|------|
| 语言 | Python 3.10+ |
| RSS 解析 | feedparser |
| HTML 模板 | Jinja2 |
| LLM | Agnes-2.0-Flash (OpenAI 兼容 API) |
| 图像生成 | Agnes Image 2.1 Flash |
| 调度 | GitHub Actions |
| 托管 | GitHub Pages (gh-pages 分支) |
| 微信推送 | Cloudflare Workers + KV |
| 推送 | 微信客服消息 API |

## License

MIT
