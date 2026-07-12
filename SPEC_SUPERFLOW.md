# spec-superflow 工作流接入

本项目已接入 `spec-superflow`，用于较大的功能变更、跨模块重构和发布链路调整。

## 目录

- `.agents/skills/`：Codex 可发现的 skill 入口，入口 skill 为 `workflow-start`
- `.agents/spec-superflow/`：官方运行时文件，包括 CLI、模板、校验脚本和参考文档
- `spec-superflow.config.json`：本项目的 workflow 配置

## 使用方式

在需要走规格化流程时，对 Agent 说：

```text
用 workflow-start 开始
```

本地 CLI 路径：

```bash
node .agents/spec-superflow/scripts/spec-superflow.mjs --version
node .agents/spec-superflow/scripts/spec-superflow.mjs validate changes/<change-name>
```

## 适用范围

推荐使用：

- 新闻采集、排序、质量门禁等跨模块功能
- 微信发布链路、部署链路、运行可靠性调整
- 需要 proposal/design/tasks/execution-contract 的长期维护变更

不强制使用：

- 小文案修改
- 单文件 hotfix
- 纯配置或说明文档 tweak

## 工件约定

新变更目录放在 `changes/<change-name>/`：

```text
changes/<change-name>/
├── proposal.md
├── specs/
│   └── <capability>/
│       └── spec.md
├── design.md
├── tasks.md
└── execution-contract.md
```

没有经过用户确认的 `execution-contract.md`，不要进入实现阶段。
