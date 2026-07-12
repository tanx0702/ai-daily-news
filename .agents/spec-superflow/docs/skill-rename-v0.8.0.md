# Skill 重命名迁移指南（v0.8.0）

v0.8.0 把 9 个 skill 的名字改为“动作 + 对象”风格，让入口和每个阶段的作用一目了然。旧名字不再保留 alias，因此升级后需要按下面的对照表使用新名字。

## 名字对照表

| 旧名称 | 新名称 | 阶段 | 作用 |
|--------|--------|------|------|
| `workflow-orchestrator` | `workflow-start` | 入口 | 工作流启动入口，检测状态并路由到下一个 skill |
| `spec-explorer` | `need-explorer` | 探索 | 澄清需求、比较方案、确认范围 |
| `spec-forger` | `spec-writer` | 规划 | 撰写 proposal、specs、design、tasks |
| `bridge-contract` | `contract-builder` | 桥接 | 把规划工件压缩成 `execution-contract.md` |
| `execution-governor` | `build-executor` | 执行 | 按契约实现代码，支持 Inline / Batch Inline / SDD |
| `systematic-debugger` | `bug-investigator` | 调试 | 4 阶段根因调试 |
| `code-reviewer` | `code-reviewer` | 审查 | 代码审查（未改名） |
| `closure-archivist` | `release-archivist` | 收尾 | 验证、总结、归档 |
| `spec-syncer` | `spec-merger` | 同步 | 把 delta spec 合并进主规格 |

## 用户需要改什么

### Claude Code

- 触发入口从 `/workflow-orchestrator` 改为 `/workflow-start`。
- 其他 skill 名称在路由时会自动被 `workflow-start` 引用，通常不需要手动输入。

### Cursor

- 重新运行 `node scripts/install-cursor.mjs`，把 skill 复制到 `.cursor/skills/`。
- `.cursor/rules/phase-guard.mdc` 会同步更新。
- `.cursor/` 是本地生成目录，不需要提交到仓库。

### GitHub Copilot CLI

- 通过 root `plugin.json` 重新加载插件。
- `plugin.json` 中的 `skills` 字段指向 `skills/` 目录，目录重命名后无需手动修改 manifest，只需确保插件刷新。

### Gemini CLI

- 重新激活插件，读取更新后的 `GEMINI.md`。

## 为什么重命名

- **降低认知成本**：`workflow-start` 比 `workflow-orchestrator` 更直接地表达“这是入口”。
- **动作 + 对象**：`spec-writer`、`contract-builder`、`bug-investigator` 等名字看到就知道职责。
- **去掉生僻词**：`orchestrator`、`forger`、`archivist` 等词需要额外解释。

## 兼容性

v0.8.0 起旧 skill 名称不再可用。如果你在任何脚本、别名或个人笔记中引用了旧名字，请按上表替换。
