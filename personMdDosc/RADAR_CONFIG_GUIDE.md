# AI 热点雷达 — 采集配置说明

本文档说明新增的多源采集器和相关环境变量配置。

## 采集器总览

| 采集器 | 环境变量 | 需要 Token | 说明 |
|--------|----------|-----------|------|
| RSS | 无需 | 否 | 原始 RSS 源采集（Hacker News RSS, TechCrunch, The Verge, 量子位 等） |
| Hacker News | `ENABLE_HN_COLLECTOR=1` | 否 | 通过 HN Firebase API 拉取 top/new stories，过滤 AI 相关 |
| GitHub | `ENABLE_GITHUB_COLLECTOR=1` | 推荐 | 通过 GitHub Search API 发现热门 AI 开源项目 |
| Hugging Face | `ENABLE_HF_COLLECTOR=1` | 可选 | 通过 HF Hub API 获取近期热门模型 |
| arXiv | `ENABLE_ARXIV_COLLECTOR=1` | 否 | 通过 arXiv API 获取 24-48h 内的 AI 论文 |

## 核心环境变量

### Freshness 控制

```bash
# 新闻时间窗口（小时）。默认 36：只保留最近 36 小时内的新闻
DAILY_NEWS_HOURS=36

# 是否允许无发布时间的新闻进入日报。强烈建议保持 0
DAILY_ALLOW_UNDATED=0
```

### 采集器开关

```bash
# 是否启用 Hacker News 采集器（默认开启，无需 Token）
ENABLE_HN_COLLECTOR=1

# 是否启用 GitHub 采集器（默认开启）
ENABLE_GITHUB_COLLECTOR=1

# 是否启用 Hugging Face 采集器（默认开启）
ENABLE_HF_COLLECTOR=1

# 是否启用 arXiv 采集器（默认开启，最终日报最多 2 条论文）
ENABLE_ARXIV_COLLECTOR=1
```

### 可选 API Token

```bash
# GitHub Personal Access Token（推荐配置）
# 未配置时也能运行，但 API 限流较低（10 req/min）
# 配置后限流提升到 30 req/min
GITHUB_TOKEN=

# Hugging Face Token（可选）
# 公开模型无需 Token 也能访问
HF_TOKEN=
```

## 容错设计

- **任一 collector 失败不影响其他 collector**：RSS 挂了不影响 HN，GitHub 限流不影响 arXiv
- **无 Token 时自动降级**：GitHub 无 token 时只做 1 次轻量查询，Hugging Face 无 token 也能正常访问公开 API
- **限流日志清晰**：GitHub 限流时会输出 `rate_limit=X, remaining=Y`，并建议配置 GITHUB_TOKEN
- **主流程永不中断**：即使所有 collector 都失败（极端情况），日报生成和微信发布流程不受影响

## 榜单质量控制

- **HN 质量门槛**：纯 HN（无跨源 + 低社区热度）条目自动降权，防止低互动内容靠新鲜度霸榜
- **选题平衡**：HN 最多 50%，RSS/官方源至少 40%，arXiv 最多 2 条，HF 最多 2 条
- **同公司上限**：同一公司/产品最多 2 条
- **Topic 去重**：同主题多篇报道自动合并

## Debug 报告

每天生成调试文件：
- `docs/debug/YYYY-MM-DD-candidates.json`：每条入选新闻的完整数据和选中原因
- `docs/debug/YYYY-MM-DD-ranking.md`：人类可读的排名解释报告
