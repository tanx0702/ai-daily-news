# 微信公众号头像候选图生成计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `kie-imagegen` for all Kie image-generation requests. Execute each paid request once and preserve the returned task ID or local artifact paths.

**Goal:** 生成 3 张可供选择的无文字微信公众号 AI 新闻头像候选图。

**Architecture:** 使用全局 `kie-imagegen` CLI 的默认 GPT Image 2 模型，按 A、B、C 三个独立方向分别提交 1 次 `1:1` 任务。每次任务写入独立的本地临时目录，CLI 轮询完成并安全下载图片；项目公开产物目录 `docs/` 与 Git 均不接收生成图片。

**Tech Stack:** Python 3.12+、Kie.ai `gpt-image-2-text-to-image`、全局 `kie-imagegen` Skill CLI。

## Global Constraints

- 只从用户环境变量 `KIE_API_KEY` 读取凭证；不显示、持久化或传入命令行。
- 每张图片是一次付费请求，默认模型为 GPT Image 2；失败时不重试且不切换模型。
- 使用 `1:1`、纯图形、无文字、字母、数字、水印或品牌标记；核心符号处于中央约 60% 安全区。
- 输出仅写入 `tmp/wechat-avatar-candidates/2026-08-13/`，不写入 `docs/`，不加入 Git。
- 成功只接受 CLI JSON 的 `local_paths`；超时或失败时保留 CLI 返回的 `task_id`/错误。

---

### Task 1: 生成 A - 冷静专业科技媒体感

**Files:**
- Create at runtime: `tmp/wechat-avatar-candidates/2026-08-13/a-professional/`

**Interfaces:**
- Consumes: `KIE_API_KEY`、`scripts/kie_image.py` 和设计记录 `project_docs/designs/2026-08-13-wechat-ai-news-avatar-design.md`。
- Produces: CLI JSON 的 `local_paths`，其中每项为可展示的绝对图片路径。

- [ ] **Step 1: 检查本机调用前提，不显示凭证值**

```powershell
$skill = 'C:\Users\开坦克的肖哥\.codex\skills\kie-imagegen\scripts\kie_image.py'
[pscustomobject]@{
  Python = (Get-Command python -ErrorAction SilentlyContinue).Source
  SkillExists = Test-Path -LiteralPath $skill
  ApiKeyConfigured = -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable('KIE_API_KEY'))
} | ConvertTo-Json -Compress
```

Expected: `Python` and `SkillExists` are set; `ApiKeyConfigured` is `true`.

- [ ] **Step 2: 提交一次 A 方向生成任务**

```powershell
python 'C:\Users\开坦克的肖哥\.codex\skills\kie-imagegen\scripts\kie_image.py' generate `
  --prompt 'Square avatar icon for a trusted daily AI news publication, no text, no letters, no numbers, no watermark, no logo. Deep ink navy background. One centered abstract news pulse symbol built from clean concentric arcs, one precise diagonal line, and a few luminous cyan-blue nodes. Restrained professional technology editorial identity, minimal geometric vector-like 3D relief, high contrast, generous negative space. Keep the full core symbol inside the central 60 percent circular safe area for a WeChat round profile crop. No people, no devices, no screenshots, no dense circuitry.' `
  --aspect-ratio '1:1' `
  --output-dir 'D:\personal_workspace\Ai_Deaily_News_Agent\tmp\wechat-avatar-candidates\2026-08-13\a-professional'
```

Expected: exit `0` and JSON with non-empty `local_paths`; otherwise retain the returned error or `task_id` and stop this direction.

### Task 2: 生成 B - 未来 AI 信号感

**Files:**
- Create at runtime: `tmp/wechat-avatar-candidates/2026-08-13/b-signal/`

**Interfaces:**
- Consumes: same Kie CLI and credential contract as Task 1.
- Produces: CLI JSON `local_paths` for B.

- [ ] **Step 1: 提交一次 B 方向生成任务**

```powershell
python 'C:\Users\开坦克的肖哥\.codex\skills\kie-imagegen\scripts\kie_image.py' generate `
  --prompt 'Square avatar icon for a cutting-edge daily AI news publication, no text, no letters, no numbers, no watermark, no logo. Near-black background. One centered floating signal ring with a small number of deliberate connected nodes, electric cyan-green and cool blue edge light. Abstract computation, network intelligence, and live information transmission, sleek icon-level geometry with a bold outer silhouette. Keep the full core symbol inside the central 60 percent circular safe area for a WeChat round profile crop. No robot face, no chip photograph, no human, no dense neural network, no devices.' `
  --aspect-ratio '1:1' `
  --output-dir 'D:\personal_workspace\Ai_Deaily_News_Agent\tmp\wechat-avatar-candidates\2026-08-13\b-signal'
```

Expected: exit `0` and JSON with non-empty `local_paths`; otherwise retain the returned error or `task_id` and stop this direction.

### Task 3: 生成 C - 温暖编辑日报品牌感

**Files:**
- Create at runtime: `tmp/wechat-avatar-candidates/2026-08-13/c-editorial/`

**Interfaces:**
- Consumes: same Kie CLI and credential contract as Task 1.
- Produces: CLI JSON `local_paths` for C.

- [ ] **Step 1: 提交一次 C 方向生成任务**

```powershell
python 'C:\Users\开坦克的肖哥\.codex\skills\kie-imagegen\scripts\kie_image.py' generate `
  --prompt 'Square avatar icon for a human-curated daily AI news publication, no text, no letters, no numbers, no watermark, no logo. Charcoal black background with restrained deep crimson, coral red, and warm gold highlights. One centered abstract symbol merging a folded editorial page with a flowing information stream, sharp geometric shapes and a very subtle soft glow. Warm, discerning, modern editorial identity rather than a generic tech tool. Keep the full core symbol inside the central 60 percent circular safe area for a WeChat round profile crop. No newspaper layout, no readable writing, no people, no devices, no festive imagery.' `
  --aspect-ratio '1:1' `
  --output-dir 'D:\personal_workspace\Ai_Deaily_News_Agent\tmp\wechat-avatar-candidates\2026-08-13\c-editorial'
```

Expected: exit `0` and JSON with non-empty `local_paths`; otherwise retain the returned error or `task_id` and stop this direction.

### Task 4: 验收并交付候选图

**Files:**
- Inspect: `tmp/wechat-avatar-candidates/2026-08-13/`

**Interfaces:**
- Consumes: Tasks 1-3 成功 JSON 的 `local_paths`。
- Produces: 向用户展示 3 张本地候选图及其方向标签，不修改公众号设置。

- [ ] **Step 1: 验证每项本地路径为非空图片文件**

```powershell
Get-ChildItem -Path 'D:\personal_workspace\Ai_Deaily_News_Agent\tmp\wechat-avatar-candidates\2026-08-13' -Recurse -File |
  Select-Object FullName, Length, LastWriteTime
```

Expected: 每个成功方向至少有一张非零字节图片；不显示或复制 API 凭证。

- [ ] **Step 2: 人工检查三图的圆形裁切可读性**

检查中心安全区、是否存在可读文字或水印、主符号是否在小尺寸仍清晰；只展示候选图，不自动上传或更改微信头像。
