# 确定性证据引用设计

## 背景

内容 LLM 当前同时生成中文标题、摘要、`source_quote` 和 `source_url`。真实服务器测试中，
`glm-4.5-air` 的中文翻译质量基本可用，但 10 条逐条请求中有 6 条因为模型返回的
`source_quote` 不是规范证据中的逐字片段而触发 `quote_not_found`。这类失败来自复制证据的
不稳定性，不应通过放宽逐字引用门禁解决。

## 目标

- 内容 LLM 继续逐条或分批生成中文标题和零至两句摘要。
- LLM 只选择程序提供的证据引用编号，不再复制原文或来源 URL。
- builder 根据引用编号确定性写入逐字 `source_quote` 和规范 `source_url`。
- validator、`BriefItem`、`DraftDecision` 和公开产物契约保持不变。
- 标题引用有效但任一摘要引用无效时，删除全部摘要并安全降级为 `title_only`。

## 非目标

- 不做模糊文本匹配、向量匹配或 LLM 二次修复引用。
- 不把完整 `evidence_text` 作为通用引用兜底。
- 不放宽主体、动作、数字、跨语言锚点或事实发布性门禁。
- 不改变内容 LLM 的两次生成预算、熔断规则或质量 LLM 降级规则。

## 方案比较

### 方案 A：引用编号映射（采用）

程序从规范证据生成稳定的 `quote_id -> 原文片段` 映射，LLM 只返回 `source_quote_id`。
优点是引用一定逐字存在、URL 一定规范，同时仍由模型选择与声明对应的证据。代价是需要调整
builder 的私有提示词和响应解析契约。

### 方案 B：模糊回查模型返回的引用（不采用）

将模型改写后的 quote 与原文做相似度匹配。代码量较少，但容易把语义相近的另一句话错误绑定，
无法满足事实审计要求。

### 方案 C：整篇证据兜底（不采用）

任何失败都绑定完整 `evidence_text`。虽然能消除 `quote_not_found`，但会弱化同一条 quote 内的
主体、动作和细节核验，并可能掩盖跨句拼接。

## 数据流

1. builder 对每个 `SourceEvidence.evidence_text` 做确定性分段。
2. 分段只保留原文中的非空连续片段，去除片段两端空白，按出现顺序去重并编号为 `q1`、`q2`。
3. LLM 输入事件增加 `source_quotes`：

   ```json
   [
     {"quote_id": "q1", "text": "OpenAI releases Model 5."},
     {"quote_id": "q2", "text": "The model adds a text API."}
   ]
   ```

4. LLM 的每个 `evidence_targets` 元素只返回：

   ```json
   {"target": "title", "source_quote_id": "q1"}
   ```

   同一 target 仍可返回多个引用编号。
5. `_strict_item` 校验 target 和 quote ID 后，从本期输入映射恢复 `EvidenceBinding`：
   `claim` 来自完整展示目标，`source_quote` 来自映射，`source_url` 固定取规范来源 URL。
6. validator 继续执行现有逐字引用、声明与引用匹配、保护锚点和发布性检查。

## 分段规则

- 仅从 `evidence_text` 生成引用，不能使用模型生成内容。
- 以换行、中英文句号、问号、感叹号和分号作为主要边界；小数点不得切断数字。
- 片段保留原始标点和内部空白，只裁剪两端空白，保证它仍是 `evidence_text` 的连续子串。
- 相同片段只保留第一次出现，编号在同一事件内稳定。
- 不额外添加整篇 `evidence_text` 候选。

## 失败与降级

- title 缺少引用、引用未知或引用结构错误：该条 builder 输出按现有畸形响应处理并进入一次重建预算。
- 任一 brief target 缺少或引用未知，但 title 完整有效：删除全部 brief 和 brief bindings，输出
  `brief_mode=title_only`、`brief_reason=brief_quote_unresolved`。
- target 未知、响应缺项、重复 index 或其它 schema 错误：继续使用现有精确原因码，不进行宽松解析。
- 即使 quote ID 有效，validator 仍可因 `claim_quote_mismatch`、动作不受支持或翻译失败要求重建或拒绝。

## 兼容边界

- `EvidenceBinding`、`BuiltBrief`、`BriefItem` 和 `latest.json` 不变。
- `source_quote_id` 只存在于内容 LLM 的私有请求/响应，不进入公开产物。
- 不兼容旧的 LLM `source_quote/source_url` 响应；旧结构按畸形响应处理，确保不会静默绕过新契约。

## 测试

- 引用分段保持原文连续片段、稳定编号、去重且不切断小数。
- builder 请求包含 `source_quotes`，提示词要求 `source_quote_id` 且不再要求模型复制 URL/quote。
- 有效编号生成逐字 quote 和规范 URL，并支持同一 target 多条引用。
- 未知 title 引用拒绝该条；未知 brief 引用降级为 `title_only`。
- 旧 `source_quote/source_url` 结构被拒绝。
- 现有 validator 测试继续证明最终逐字引用和事实绑定门禁没有放宽。

## 文档与部署

实现时同步 `project_docs/pipeline.md` 和根 `AGENTS.md` 中的内容 LLM 证据契约。完成本地测试、
合并到 `master` 并推送后，服务器拉取代码、重建容器，再用同一批真实样本逐条对比：结构合格率、
`quote_not_found` 数量和最终 display contract 通过率。最终只使用 `SKIP_WECHAT_DRAFT=1` 做完整干跑。
