# X 内容分类与发布性分派修复设计

## 背景与根因

2026-08-27 服务器逐条内容 LLM 安全干跑共尝试 73 次，全部成功且没有超时、不可用或熔断，但最终只接受 2 条。候选池包含 21 条 X，其中 15 条以 `non_news_content` 被拒绝。逐条审计和源码调用链确认，这 15 条同时包含应继续拒绝的体育、政治、直播和推广内容，以及被误判的官方发布和 AI 观点。

根因位于 LLM 之前的确定性链路：

1. X URL 经通用来源规范化后把真实发布者统一写成 `X`，注册来源身份只残留在标题前缀。
2. X 分类器未识别 `is live`、`goes live`、`releasing` 等明确发布表达。
3. 白名单人物观点只接受很窄的立场词，无法识别当前样本中的 `not every ...` 和中文“需要……”表达，也没有独立的 AI 主题前提。
4. 候选池发布性预检固定调用硬新闻规则，没有按 `fact_event`、`ai_update` 和 `attributed_opinion` 分派；最终 Validator 已部分分派，两个阶段可能漂移。
5. 未命中观点、硬新闻或量化动态的 X 内容仍回退为 `fact_event`，最终会保守拒绝，但会消耗候选和内容 LLM 预算。

## 目标

- 注册 X 来源在 `SourceEvidence.publisher_name` 中保留真实名称。
- 将明确的官方上线/发布表达稳定分类为 `fact_event`。
- 将白名单自然人关于 AI 的实质观点分类为 `attributed_opinion`。
- 候选预检和最终 Validator 共用同一个按内容类型分派的来源发布性入口。
- 当前真实样本的精简测试中，Qwen API 上线、OpenAI 技术报告、Ethan Mollick AI 观点和马东锡 AI 监督观点进入正确类型。
- Nathan Benaich 体育内容、Rodney Brooks 政治内容、Gary Marcus 直播预告和 Qwen 推广帖继续不能进入可发布结果。

## 非目标

- 不使用 LLM 判断候选内容类型。
- 不放宽 `ai_update` 的量化细节、指标方向和逐条证据绑定要求。
- 不修改日报数量、X 数量、观点/动态配额、候选池大小或来源排序参数。
- 不修改内容 LLM 逐条请求、熔断、语义去重、微信草稿或 X 快照认证采集。
- 不把完整 X 快照或服务器审计响应加入仓库测试 fixture。

## 方案比较

1. **根因修复（采用）**：恢复可信发布者身份，补充高置信分类表达，增加观点 AI 主题前提，并统一来源发布性分派。改动覆盖真正漂移点，最终事实门禁保持不变。
2. **只补动作关键词（不采用）**：能恢复少量官方帖子，但发布者、观点和预检漂移仍未解决。
3. **LLM 分类（不采用）**：覆盖面较广，但增加外部依赖和不确定性，不能用于候选池确定性预检。

## 组件设计

### 注册 X 发布者身份

`XFeedCollector` 只在 `config/x_sources.json` 命中注册来源时写入内部候选字段 `x_source_name`。`source_evidence_from_candidate()` 只在 `trusted_x_collector=True` 且 `x_source_name` 非空时使用该名称覆盖通用的域名发布者 `X`。未注册来源不能借快照中的自由文本升级可信身份；handle、URL 和官方身份校验保持不变。

### X 内容分类

分类顺序保持：前置拒绝推广/转发，随后依次判断观点、硬新闻动作和量化动态。硬新闻动作词表只补充高置信发布表达 `is live`、`goes live`、`went live` 和 `releasing`，继续要求完整主体和事件细节。

白名单观点在现有“原帖、上下文完整、非推广、内容足够长、存在立场”条件之前增加确定性 AI 主题检查。AI 主题只接受模型、智能体、机器学习、LLM、已知模型家族或明确 `AI` 等锚点，不使用作者身份本身推断主题。立场词只补充当前误杀所需的 `not every` 与中文“需要”；没有 AI 主题的体育、政治和生活观点即使作者在白名单中也返回 `opinion_no_ai_topic`。

未命中三种发布类型的内容仍保持保守回退并在现有事实门禁中拒绝；本次不新增删除式采集行为，保证审计仍能看到候选及拒绝原因。

### 统一来源发布性分派

在 `src/briefing/publishability.py` 增加一个内容类型分派入口：

- `fact_event` 调用现有 `validate_source_publishability()`；
- `ai_update` 调用现有 `validate_update_source_publishability()`；
- `attributed_opinion` 要求 `opinion_eligible`、`original_post`、`context_complete` 和非空 `opinion_author`，否则返回现有观点原因码。

`collect_candidates()` 的预检和 `BriefValidator.validate()` 均调用该入口。最终 Validator 继续执行标题作者归因、显示声明、逐字 quote、规范 URL、保护锚点和质量 LLM 规则；统一分派不替代任何显示门禁。

## 数据流与失败行为

```text
X 快照记录
  -> 注册来源身份恢复
  -> 推广/转发拒绝
  -> 带 AI 主题的白名单观点 | 明确官方动作 | 严格量化动态 | 保守 fact 回退
  -> SourceEvidence
  -> 按 content_type 的共享来源发布性预检
  -> 内容 LLM 逐条翻译/摘要
  -> 同一共享来源发布性检查 + 显示/证据/去重门禁
  -> DraftDecision
```

分类仍是确定性的。未知表达、主题不明确、来源身份未注册或观点上下文不完整时保守拒绝，不转人工复核，也不请求 LLM 猜测类型。

## 测试与验收

- 先用精简公开文本写失败测试，确认当前代码错误分类或错误规范化。
- 覆盖注册 X 发布者 round-trip、官方 `is live/releasing`、AI 观点主题与立场，以及非 AI 白名单观点拒绝。
- 覆盖候选预检与最终 Validator 对三类内容调用同一来源发布性分派。
- 运行受影响测试、完整 `python -m pytest -q`、`git diff --check` 和 `git status --short`。
- 提交并推送唯一发布基线 `master`，服务器 `pull --ff-only` 并重建服务。
- 使用 `SKIP_WECHAT_DRAFT=1` 安全干跑，报告 X 候选分类、最终拒绝原因、内容 LLM 计数、最终条数和决策；不以降低门禁或调用真实微信草稿换取成功。
