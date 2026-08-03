# GitHub Actions X 网页采集探针设计

## 上下文

- 当前日报的 RSS、HN、GitHub、Hugging Face 和 arXiv 采集均在 VPS 的 Docker 容器内运行。
- VPS 宿主机和容器直连 `api.x.com`、`x.com` 均不可达；本机浏览器通过本地系统代理可以访问 X。
- 本阶段不使用 X API，也不接入付费数据服务。
- 参考的网页采集方案使用 Playwright 加载公开 X 页面，并从浏览器网络响应中提取结构化推文数据。

## 目标

- 验证 GitHub 托管运行器是否能加载一个公开 X 页面。
- 验证 Chromium 是否能捕获并解析至少一条 Tweet 相关 XHR/GraphQL 响应。
- 生成可审阅的探针报告、脱敏响应摘要和失败时的页面截图。
- 探针通过前，保持 VPS 的生产日报、微信草稿、影子报告和 Docker 镜像不变。

## 非目标

- 不调用 X API，不使用 `X_BEARER_TOKEN`。
- 不采集完整账号列表、关键词搜索或评论。
- 不上传候选 JSON 到 VPS，不修改 `src/main.py` 或现有采集流程。
- 不保存、导出或上传浏览器 Cookie、授权头、请求体和会话令牌。
- 不创建定时生产任务；只提供 `workflow_dispatch` 手动探针。

## 决策

### 决策 1：使用 GitHub Actions 承载浏览器探针

- **选择**：在 GitHub 托管 Ubuntu Runner 中运行 Playwright Chromium。
- **理由**：VPS 对 X 无出站路径，GitHub Runner 提供独立网络出口；探针先验证真实可达性，再决定是否建设正式采集链路。
- **考虑的替代方案**：
  - VPS 内运行 Playwright：仍依赖不可达的 `x.com` 网络路径。
  - 本机定时运行：依赖个人电脑持续开机。
  - 公共 RSS 镜像：稳定性不足，无法作为每日候选核心来源。

### 决策 2：只捕获允许列表中的结构化响应

- **选择**：监听页面响应，只接受 URL 中包含 `TweetResultByRestId`、`UserTweets`、`UserByScreenName` 或 `SearchTimeline` 的 JSON 响应。
- **理由**：页面 DOM 易变化，XHR JSON 是文章中采用的稳定数据边界；允许列表避免把无关资源、认证数据或页面脚本写入报告。
- **考虑的替代方案**：直接解析页面 DOM。该方式对选择器和页面布局高度敏感，且不利于获取发布时间、作者和互动指标。

### 决策 3：探针以公开 URL 为输入，不引入登录态

- **选择**：工作流由 `workflow_dispatch` 接收 `target_url`，默认使用公开账号主页；探针报告记录实际命中的操作名和脱敏字段。
- **理由**：探针验证的是匿名公开访问能力，避免在 GitHub Secrets 中保存浏览器 Cookie。
- **考虑的替代方案**：使用持久化登录态。只有匿名访问已验证失败且用户明确接受账户会话管理时才单独设计。

## 工作流

```text
workflow_dispatch(target_url)
  -> 安装 Python 与 Playwright Chromium
  -> 打开公开 X 页面
  -> 捕获允许列表 XHR/GraphQL JSON
  -> 提取 tweet_id、文本、作者、发布时间、互动计数
  -> 写入脱敏 probe-report.json 与失败截图
  -> 上传 GitHub Actions Artifact
```

成功条件：工作流退出码为 0，报告中至少有一条结构化推文记录，且不包含 Cookie、`Authorization`、请求头或 Token。

失败条件：页面无法加载、未捕获允许操作、响应无法解析或所有记录缺少推文 ID/文本。工作流退出非零，并上传截图和简短诊断。

## 预期文件

| 文件 | 目的 |
|---|---|
| `.github/workflows/x-web-probe.yml` | 手动触发的 GitHub Actions 探针工作流 |
| `scripts/x_web_probe.py` | Playwright 页面加载、XHR 捕获、脱敏报告和退出码控制 |
| `tests/test_x_web_probe.py` | 响应 URL 过滤、推文字段提取、脱敏和失败判定测试 |
| `requirements-dev.txt` | 仅供探针/CI 使用的 Playwright 依赖，不进入 VPS 生产镜像 |

## 风险与权衡

- GitHub Runner 可能被 X 识别为自动化环境：探针必须以真实捕获结果判定，不将“工作流完成”视为数据可用。
- X 的前端操作名或 JSON 结构可能变化：操作 URL 使用小范围允许列表；解析器返回缺失字段而非猜测补全。
- 运行器日志可能泄露网络数据：日志只输出操作名、状态码、计数和字段名；原始响应不写入 Artifact。
- Playwright 安装时间较长：仅在探针工作流安装，不修改 VPS Docker 镜像。

## 迁移与回滚

- 上线步骤：先手动触发探针，审阅 Artifact；探针稳定通过后，另行设计定时采集与 VPS 候选导入。
- 回滚步骤：删除工作流和探针脚本即可；现有生产流程从未引用这些文件。

## 待明确问题

- GitHub Runner 的匿名访问是否足以获取公开账号时间线和搜索结果，由本探针实测决定。
- 若匿名访问失败，是否接受单独引入受保护的浏览器会话管理，由用户在探针报告后决定。
