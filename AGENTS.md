# AI Daily News Agent 项目导航与执行约束

本仓库是一个单体 Python AI 新闻采编与发布流水线，附带 Flask 微信/新闻服务和 Docker/nginx 部署。面向维护者的说明使用中文；代码标识、文件名、环境变量、API 字段和命令保持原样。

## 文档导航

开始任务前先读本文件，再按变更范围阅读：

- 项目结构和依赖边界：[project_docs/architecture.md](project_docs/architecture.md)
- Python/服务端约束：[project_docs/backend.md](project_docs/backend.md)
- 日报生产阶段与降级：[project_docs/pipeline.md](project_docs/pipeline.md)
- 环境变量和密钥规则：[project_docs/configuration.md](project_docs/configuration.md)
- RSS、HN、GitHub、HF、arXiv、X 来源：[project_docs/sources.md](project_docs/sources.md)
- 本地、Docker、cron、Flask 和诊断：[project_docs/operations.md](project_docs/operations.md)
- 测试、提交和文档同步：[project_docs/workflow.md](project_docs/workflow.md)

## 工程边界

```text
cron / 手动命令 -> python -m src.main -> 采集/编辑/摘要/质量/媒体/封面/产物/微信草稿
                                      -> docs/index.html、latest.json、cover.jpg、wechat.html、archive/

nginx -> 静态发布 docs/，并反代 /wechat、/health、/api/ 和 /editorial-review
Flask app.py -> 读取 latest.json，处理微信回调和受保护的 shadow 编辑反馈
```

- `src/main.py` 是生产日报主入口；`app.py` 是 Flask 服务入口。
- 采集层包含 RSS、Hacker News、GitHub、Hugging Face、arXiv 和 X 快照。X 通过 GitHub Runner 生成的 `x-feed.json` 接入，不是生产任务直接调用 X API。
- `src/agents`、`src/domain`、`src/services`、`src/workflows` 支持 v2/editorial、shadow 和反馈闭环；它们不能未经说明替换默认 v1 生产路径。
- `src/tencent_scf/` 是历史兼容代码，当前 Docker 主流程不依赖它。
- `docs/` 是 nginx 公开/运行时产物目录，不是维护文档目录；长期说明放在 `project_docs/`。

## 必须遵守

- 外部 RSS、X、GitHub、HF、arXiv、LLM、图片和微信 API 失败必须记录并降级，不能让单条故障无日志地中断整期日报。
- LLM 只能翻译和摘要原始证据，不能新增事实；GitHub 活跃度不能写成正式发布证据。
- high risk 条目必须经过 quality gate 移除/回填；`publication.ready`、条目 `quality_state`、质量复核状态和来源集中度不能被绕过。
- Flask 路由只做协议转换、签名/认证、读取产物和调用服务；禁止在路由中直接采集、调用 LLM、渲染或创建草稿。
- 新采集器、配置、质量门禁、发布出口、Docker/nginx/cron 或 Flask 边界变更，必须同步对应 `project_docs/`，并更新本文件的导航/约束（见 `project_docs/workflow.md`）。
- Python 3.12+，模块使用 `logging`；时间使用带时区值和项目统一日期工具；跨模块数据必须可序列化。

## 配置与安全

- 首次部署只复制 `.env.example`；高级覆盖从 `.env.advanced.example` 逐项复制。
- `QUALITY_LLM_*` 未设置时继承 `LLM_*`，用于独立质量门禁和编辑复核；新增配置仍以 `.env.advanced.example` 为参考。
- 修改 `.env` 后执行 `docker compose up -d --force-recreate`。
- 不提交 `.env`、API key、微信密钥/token、Basic Auth 密码、日志、媒体缓存、`docs/` 生成物或完整外部 API 响应。
- `COVER_RENDER_MODE` 默认 `legacy`；`editorial` 是本地确定性封面模式。图片/质量配置缺失时允许按文档描述降级。
- 生产环境必须配置 `WECHAT_TOKEN` 并进行签名验证；`ALLOW_INSECURE_WECHAT_TOKEN=1` 只允许本地排查。

## 最小验证

完成任何变更前后，按风险运行受影响测试或完整测试：

```powershell
python -m pytest -q
git diff --check
git status --short
```

报告必须列出实际运行的命令和结果；基线已有失败时，必须单独说明失败测试及其与本次变更的关系，不得声称未通过的检查为通过。

提交前检查 `git diff --staged`。普通提交使用中文 Conventional Commit；合并提交统一使用英文 `Merge` 标识，例如 `Merge: 合并 X 来源采集`，通过 GitHub Pull Request 合并时保留 GitHub 默认的 `Merge pull request #...` 格式。不要使用 `git commit --no-verify` 绕过检查。
