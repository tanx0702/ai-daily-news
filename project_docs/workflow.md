# 开发、验证与协作规范

## 任务开始

1. 先读 `AGENTS.md`，再读变更涉及的 `project_docs/` 主题文档。
2. 用 `git status --short` 和 `git diff --stat` 确认工作区现状，保留用户已有改动。
3. 从代码、配置模板和测试寻找事实来源；不要只凭旧文档推断当前行为。
4. 写明本次变更的输入、输出、依赖边界、风险和验收命令，再开始编辑。

## 变更范围

- 一个任务只解决一个可独立验证的问题；测试、对应文档和实现一起变更。
- 不做无关重命名、格式化、依赖升级或生成产物清理。
- 不把 `docs/` 日报、`docs/debug` 诊断、`docs/media` 缓存、`logs/` 日志、`.env` 或真实凭证加入提交。
- 修改采集器、事实简报核验、草稿决策/执行、配置、发布出口、Docker/nginx/cron 或 Flask 边界时，按 `AGENTS.md` 的同步矩阵更新 `project_docs/`。

## Python 约定

- Python 3.12+；模块使用 `logging`，不要用散落的 `print` 作为生产诊断。
- 采集和外部 API 失败必须有日志、明确降级和可测试结果；禁止空 catch 或悄悄吞掉异常。
- 时间值带时区；报告日期统一经 `src.time_utils` 计算。
- 候选、事实简报、草稿决策/执行和诊断字段保持可序列化；新增字段要检查所有 JSON/HTML/微信消费者。
- `latest.json` 生产写入 schema v2 的 `brief_items`、`draft_decision`、`draft_execution` 和诊断；v1 只用于冷启动读取兼容。
- v2/shadow/editorial review 和 Tencent SCF 的改动不能未经说明改变已接受的事实简报或 `DraftDecision`。

## 测试与验证

小范围改动先运行精确测试，例如：

```powershell
python -m pytest -q tests\test_x_feed_collector.py
python -m pytest -q tests\test_app.py tests\test_deployment_config.py
```

涉及共享流水线、事实简报核验、草稿决策/执行、发布或配置契约时运行完整测试：

```powershell
python -m pytest -q
```

所有文档/代码变更都运行：

```powershell
git diff --check
git status --short
```

验证报告必须列出实际执行的命令和结果。若基线已有失败，说明失败测试、原因和是否与本次改动相关；不能把未运行或失败的命令描述为通过。

## 提交规范

普通提交信息使用中文 Conventional Commit：

```text
<type>(<scope>): <简洁、命令式的结果描述>
```

常用类型：`feat`、`fix`、`test`、`docs`、`refactor`、`chore`。例如：

```text
docs: 重构项目导航与开发规范
fix(collector): 隔离 X 快照过期候选
test(quality): 覆盖发布门槛回填路径
```

合并提交不使用中文 `合并` 前缀，统一使用英文 `Merge` 标识：

```text
Merge: 合并 X 来源采集
Merge pull request #12 from tanx0702/codex/quality-gate-batch-retry
```

本地合并可以使用 `Merge: <中文摘要>`；通过 GitHub Pull Request 合并时保留 GitHub 自动生成的 `Merge pull request #...` 标题。历史提交不做改写。

提交前检查 `git diff --staged`，确认只包含当前任务文件，没有密钥、日志、产物和无关修改。不要使用 `git commit --no-verify` 绕过检查。

## 文档同步

| 变更 | 同步文档 |
| --- | --- |
| 来源/采集器 | `sources.md`、`architecture.md`，必要时 `configuration.md` |
| 主流程/事实简报/草稿决策 | `pipeline.md`、`AGENTS.md` |
| 环境变量 | `configuration.md`、`.env*.example` |
| Docker/nginx/cron/Flask/微信 | `operations.md`、`AGENTS.md` |
| Python 分层、领域、服务或工作流 | `architecture.md`、`backend.md` |
| 测试和提交规则 | `workflow.md`、`AGENTS.md` |
