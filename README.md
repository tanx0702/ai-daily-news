# 🤖 AI Daily News Agent

每天自动采集 AI 圈重要新闻，生成精美日报，通过微信公众号推送。

> 每天早上 8 点，你的微信公众号收到一篇 AI 日报，点开看摘要，点"阅读全文"看完整日报。

## 功能

- **多源采集** — 从 Hacker News、TechCrunch、MIT Technology Review、机器之心、量子位等 RSS 源并行抓取
- **智能筛选** — 自动过滤非 AI 相关内容，去重排序
- **LLM 摘要** — 调用 Agnes-2.0-Flash 为每条新闻生成中文摘要
- **精美日报** — 生成手机端适配的 HTML 日报页面，支持暗色模式
- **AI 封面** — 调用 Agnes Image 2.1 Flash 根据当日新闻自动生成封面图
- **历史归档** — 每天自动归档，首页展示最近 7 天历史
- **微信推送** — 通过公众号群发图文消息，点击"阅读全文"跳转 GitHub Pages 完整日报
- **零运维** — 完全基于 GitHub Actions，无需服务器，免费运行

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/YOUR_USERNAME/ai-daily-news.git
cd ai-daily-news
```

### 2. 配置 GitHub Pages

1. 进入仓库 Settings → Pages
2. Source 选择 `main` 分支的 `/docs` 文件夹
3. 保存后页面将部署到 `https://YOUR_USERNAME.github.io/ai-daily-news/`

### 3. 配置 GitHub Secrets

进入仓库 Settings → Secrets and variables → Actions，添加：

| Secret | 说明 |
|--------|------|
| `AGNES_API_KEY` | Agnes AI API Key（用于新闻摘要） |
| `AGNES_MODEL` | 模型名称，默认 `agnes-2.0-flash` |
| `AGNES_API_BASE` | API 基础地址（可选，默认 `https://apihub.agnes-ai.com/v1`） |
| `WECHAT_APP_ID` | 微信公众号 AppID |
| `WECHAT_APP_SECRET` | 微信公众号 AppSecret |
| `WECHAT_COVER_IMAGE_PATH` | 封面图片路径（可选，AI 封面未启用时的备选） |

### 4. 注册微信公众号

1. 打开 https://mp.weixin.qq.com ，注册订阅号
2. 完成认证（个人/企业均可）
3. 在"开发 → 基本配置"中获取 AppID 和 AppSecret
4. 在"开发 → 接口权限"中获取封面图 media_id（可选）

### 5. 手动触发

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
            "url": "https://hnrss.org/frontpage?q=AI+OR+ML+OR+LLM",
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
├── src/
│   ├── main.py                  # 主程序
│   ├── collector.py             # RSS 采集
│   ├── summarizer.py            # LLM 摘要
│   ├── cover.py                 # AI 封面图生成
│   ├── generator.py             # HTML 渲染
│   └── wechat.py                # 微信推送
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
| 调度 | GitHub Actions |
| 托管 | GitHub Pages |
| 推送 | 微信公众号 API |

## License

MIT
