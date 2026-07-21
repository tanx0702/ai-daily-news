# 修复批次设计文档（fix/issue-verification-batch）

> 日期：2026-07-07 | 基线：v0.8.14 | 范围：真实缺陷 + 轻量增强（用户选定）
> 来源：对 19 条 GitHub issues 的独立代码核查（见 `spec-superflow-issues-analysis.md`）

## 范围与分类

| 项 | 类型 | 修复归属 |
|----|------|----------|
| BUG-A 收口 `closing` 卡死 | 真实 BUG（P0） | spec-superflow 自身 |
| BUG-B `readState` 静默默认致状态漂移 | 真实 BUG（P0） | spec-superflow 自身 |
| #26 / #27.2 `ssf` 未进 PATH | 真实 BUG / 增强（P1） | spec-superflow 自身 |
| #28 门禁可跳步（spec-merger 被跳过） | 真实 BUG（P1） | spec-superflow 自身 |
| #15 git 分支隔离仅建议、无强制 | 真实 BUG（P2） | spec-superflow 自身 |
| #29 ZCODE 平台支持 | 轻量增强 | spec-superflow 自身 |

## BUG-A：收口跳转 `executing → closing` 被永久卡死

**根因**：`release-archivist` 从不写 `test_result: pass`，而 `tests-passing` 守卫强制要求它；`ssf state transition` 又强制执行守卫并 `exit(1)`，导致收口永远失败。

**修复**：
1. `scripts/guard/checks/tests-passing.mjs`：在 `state.test_result === 'pass'` 之外，额外接受 `state.dp_6_result` 以 `"pass"` 开头（release-archivist 验证结果即等价信号）。代码级兜底，不依赖 AI 记忆。
2. `skills/release-archivist/SKILL.md`：在 DP-6 通过后、执行 `state transition closing` 之前，补一步 `ssf state set <change-dir> test_result pass`（随 #26 一并改为 node 调用），保持状态文件语义一致。
3. 回归测试：`tests/lib/guard-tests-passing.test.mjs` —— 无 test_result 且无 dp_6 pass 时 `executing→closing` 守卫失败；dp_6_result 以 pass 开头时通过。

## BUG-B：`readState` 对缺失文件静默返回默认 → 幽灵状态文件/漂移

**根因**：`state-loader.mjs:45-47` 文件不存在时返回默认 `state: exploring`；`cmd-state.mjs` 的 `get/set/transition` 据此静默操作，尤其 `set` 会在错误目录新建 `.spec-superflow.yaml`。

**修复**：
1. `scripts/lib/cmd-state.mjs`：对所有非 `init` 子命令，先校验 `.spec-superflow.yaml` 是否存在；不存在则明确报错 `No state file at <path>. Run 'ssf state init <change-dir>' first.` 并 `exit(1)`。这同时杜绝 `set` 创建幽灵文件。
2. 回归测试：`tests/lib/cmd-state-missing.test.mjs` —— 对不存在目录执行 transition/get/set 均非零退出且提示 "No state file"。

## #26 / #27.2：skill 依赖裸 `ssf`（PATH 依赖）

**根因**：skills 大量调用裸 `ssf ...`，但安装脚本从不把 `ssf` 链入 PATH；cursor/marketplace 用户无 `ssf` 二进制 → 步骤失败。

**修复（彻底去 PATH 依赖）**：把 `skills/**/SKILL.md` 中所有 `ssf <sub>` 调用统一改写为 `node "${CLAUDE_PLUGIN_ROOT}/scripts/spec-superflow.mjs" <sub>`（`CLAUDE_PLUGIN_ROOT` 安装时已替换为绝对路径，必然可用）。该机械改写由脚本完成并逐文件 review diff。这同时满足 #27.2「没有 ssf 命令也能用」的诉求。

## #28：门禁可跳步（delta specs 存在时 spec-merger 被跳过）

**修复（硬门）**：
1. 新增 `scripts/guard/checks/specs-merged.mjs`：若 `spec_merged === true` → 通过；否则若 `specs/` 含 `## ADDED/MODIFIED/REMOVED/RENAMED` delta 需求且 `spec_merged !== true` → 失败（提示先跑 spec-merger）。
2. `scripts/guard/guard.mjs`：`executing:closing` 增加维度 `specs-merged`，并在 CHECK_RUNNERS 注册。
3. `state-loader.mjs`：增加字段 `spec_merged`（默认 false），同步到 writeState 与 SETTABLE_FIELDS。
4. `skills/spec-merger/SKILL.md`：合并完成后写 `ssf state set <change-dir> spec_merged true`（node 调用）。
5. 回归测试：`tests/lib/guard-specs-merged.test.mjs`。

## #15：git 分支隔离仅建议、无强制（曾改坏 master）

**修复（可强制）**：
1. 新增 `scripts/ensure-branch.mjs`：检查当前分支；若在 `main`/`master`，优先 `git worktree add`，失败则 `git switch -c`；均失败且未获用户显式批准则非零退出并提示。
2. `scripts/spec-superflow.mjs`：注册 `isolate` 命令 → `cmd-isolate.mjs`（封装 ensure-branch.mjs）。
3. `skills/build-executor/SKILL.md`：将"Branch/worktree preflight"由建议文本改为**必须运行** `node "${CLAUDE_PLUGIN_ROOT}/scripts/ensure-branch.mjs" <change-dir>`（或 `ssf isolate`），其失败即阻断编辑。

## #29：ZCODE 平台支持

**修复（机械镜像 cursor 安装）**：
1. 新增 `scripts/install-zcode.mjs`，镜像 `install-cursor.mjs`（复制 skills、替换 `CLAUDE_PLUGIN_ROOT`、生成 rules）。
2. `scripts/spec-superflow.mjs` 注册 `install-zcode`。
3. `INSTALL.md` 平台表增加 ZCODE 行。
> 假设：ZCODE 以 skills 目录方式发现技能（与 Cursor 本地 skills 同形）。具体 manifest 格式待用户确认后补；当前实现保证 `ssf install-zcode` 可用。

## 验证

- 每个修复配独立回归测试（`node --test`）。
- 全量运行 `npm test`，确保无回归。
- 手动 `ssf doctor` 检查安装健康度。
