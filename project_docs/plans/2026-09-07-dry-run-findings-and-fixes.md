# 2026-09-07 服务器干跑问题与修复任务

## 目标与执行边界

根据真实生产审计修复动作识别漏判、观点资格误判、英文来源回退和构建失败诊断。复用当前公共入口，不重构系统，不降低事实、证据、去重或发布门槛，不以最终达到 5 条作为修复正确性的判据。

指定执行模型：GPT-5.6 Terra；推理强度：medium。执行前完整阅读根目录 `AGENTS.md`，并按导航阅读 `project_docs/backend.md`、`pipeline.md`、`sources.md`、`configuration.md`、`workflow.md`。本任务仅实现、测试和文档同步；不自动提交、推送、部署，不调用真实微信草稿 API，不另启生产干跑。

## 已核实基线

- 服务器：`root@tankex.xyz`，仓库 `/opt/ai-news`，运行代码 `5d88c37`；本地 `71cc3c2` 只多一项文档提交，生产实现一致。
- 2026-09-07 10:54:30—11:05:19（Asia/Shanghai）执行 `docker compose exec -e SKIP_WECHAT_DRAFT=1 -T web python -m src.main`，耗时约 10 分 50 秒，退出码 1。
- 原始候选 2465，过期 2390，不相关 3；剩余 72 中分类拒绝 55、无效证据 6，最终进入构建 11 条（7 条 fact_event、4 条 attributed_opinion）。
- 最终接受 3 条观点、0 条事实，`DraftDecision=block`，原因为 `insufficient_items,insufficient_fact_items`。没有去重删除或隔离，没有触及候选池上限。
- 内容构建 19 次，19 次完成成功响应处理；内容 LLM 无超时、不可用或熔断。这里的成功计数不等于条目结构有效或事实核验通过。质量 LLM 成功 3 次。
- 8 条最终拒绝：`title_missing_event_action` 3、`community_claim_overstated` 2、`builder_item_malformed` 2、`title_action_not_source_bound` 1。
- VentureBeat 返回 429；arXiv 两次 429 后降级为空；X 本机快照正常读取。外部限流不是本次代码修改对象。
- 干跑释放运行锁，web 容器健康，未创建真实微信草稿。运行时产物已更新。
- 私有证据快照：服务器 `/root/ai-news-dryrun-20260907-05b0tl/briefing.json`、`latest.json`、`exit-code`。原始审计路径为 `/opt/ai-news/docs/debug/2026-09-07-briefing.json`，可能被后续运行覆盖，应优先使用私有快照。
- 最近本地完整测试：652 passed，1 条 feedparser 弃用警告。服务器宿主机 `python3` 不能解析项目使用的 Python 3.12 f-string 语法；需要项目导入的服务器复现应在 web 容器内执行，不改生产代码迎合旧 Python。
- 保留原有未跟踪文件 `project_docs/plans/2026-08-18-authenticated-x-snapshot.md`；不得读取或提交服务器 `.env` 备份、凭证和完整外部响应。

## 任务一：修复明确起诉动作的漏识别

优先级：高。涉及 `src/briefing/publishability.py` 的共享动作词表和动作/事实框架识别，以及 `src/briefing/classification.py` 的调用链。

真实输入：

```text
Seattle Times and Newsday are the latest publications to sue OpenAI and Microsoft
Two more news organizations are suing OpenAI and Microsoft over the supposed use of their journalism to train AI.

Seattle Times and Newsday sue OpenAI and Microsoft for infringement
The Seattle Times and Newsday are just the latest plaintiffs to take OpenAI to court, alleging copyright infringement.
```

两个 RSS 候选均有 `evidence_quality=ready`。容器内重放结果：`asserted_action_types(title)` 返回空集合，事实来源检查返回 `non_news_content`，统一分类拒绝，未进入内容 LLM。这两篇报道属于同一事件，不能当成两条事实补足数量。

- [ ] 在 `tests/test_publishability.py`、`tests/test_content_classification.py` 为上述明确起诉输入增加失败回归测试。
- [ ] 检查共享动作入口如何被分类、绑定、跨语言 rules_only 和去重消费，最小补全明确的起诉动作及必要中文映射；不要另建一套词表。
- [ ] 验证“起诉/提起诉讼”不能被翻译升级为“胜诉/裁决”；保留主体、对象、原文 quote 和规范 URL 绑定。
- [ ] 覆盖否定、假设或计划性表述，确保 `may sue`、`considering suing`、`did not sue` 不被当作已经起诉；教程和纯评论仍不能进入事实。
- [ ] 用当前去重公共入口检查同一事件的两个来源不占两个名额，不为回归测试放宽实体或去重标准。

验收：明确动作不再仅因词表缺项被拒绝；最终是否入选仍由完整 Validator 与去重决定。若发现需要额外主体修复，先复现并限于这组输入直接相关的根因。

## 任务二：修复教程误获署名观点资格

优先级：高。涉及 `src/briefing/opinion.py::evaluate_opinion_candidate` 与统一分类。

真实审计：Sebastian Raschka 的视频教程正文含 `Logits and next-token predictions`，被赋予 `stance_type=prediction`。今早定时任务接受过该条；本次干跑仍误分类为观点，但最终因 `community_claim_overstated` 拒绝。

最小复现：

```python
from src.briefing.opinion import evaluate_opinion_candidate

result = evaluate_opinion_candidate(
    {"text": "In this video I explain how LLMs generate text, including logits and next-token predictions."},
    {"opinion_eligible": True},
)
# 当前错误：eligible=True、stance_type='prediction'
```

根因已确认：`any(marker in lower for marker in markers)` 将 `predict` 子串匹配到技术名词 `predictions`，并把技术过程当成作者预测立场。

- [ ] 在 `tests/test_opinion_rules.py` 补充上述失败回归和真实视频语境的精简回归。
- [ ] 检查其它 stance marker 的同类子串或技术语境问题，修复与本次根因直接相关的误判；英文词边界只是必要措施，不能把技术描述中的 `predict` 动词自动等同作者立场。
- [ ] 保留具有实质 AI 立场的预测、批评、比较和个人观点；白名单、原帖、AI 主题、上下文要求继续生效。
- [ ] 在分类层验证教程不会因该资格错误变成观点，也不应绕道成为事实或动态；不得用增加 LLM 调用补救确定性资格错误。

验收：纯教程/技术过程为不合格观点；明确、有实质内容的作者立场仍可通过资格检查。最终观点保持 title_only 和作者归因。

## 任务三：修复英文来源回退的主体污染

优先级：高。涉及 `src/briefing/publishability.py::source_anchored_title`、`_literal_subject_surface`、`src/briefing/builder.py`。

真实输入：

```text
Nando de Freitas: Today we're releasing data on models accelerating research at OpenAI. Recursive self-improvement could be the most important contributor to AI capabilities over the next few years,
```

容器内直接重放 `source_anchored_title(source)` 输出：

```text
Nando de Freitas: Today we're 发布 OpenAI
```

根因：缺少已知主体锚点时，动作前整段文本进入 `_literal_subject_surface`，残留普通英文及来源归因前缀；同时“在 OpenAI 的研究”不能被改写成“发布 OpenAI”。本次最终门禁正确拒绝，错误回退未公开。

- [ ] 在 `tests/test_publishability.py`、`tests/test_brief_builder.py` 重现污染输入，明确其不能生成上述回退标题。
- [ ] 最小修复回退主体和对象选择：无法机械绑定主体、动作、对象时返回无回退，不从机构位置、比较对象或来源前缀拼接新闻。
- [ ] 保留已登记机构/模型/产品、受控数字与版本、项目路径，以及已验证部署时长和开发者辞职等现有边界。
- [ ] 验证一轮重建上限、`source_fallback_used` 独立标志和原始失败原因均保留；回退不能绕过 Validator。
- [ ] 检查重建流程是否使用本轮有效结构结果，以及回退覆盖顺序；仅在有失败测试和直接证据时修复相关问题，不顺带重构。

验收：该来源不能产生主体/对象错误的混合英文回退；合法已知回退测试继续通过。

## 任务四：细化畸形构建响应的私有诊断

优先级：中。涉及 `src/briefing/builder.py::_strict_item`、`BuildResult` 与 `src/briefing/pipeline.py` 的私有审计。

本次 Ethan Mollick 的历史模型回顾和 CNBC 的 model fatigue 候选最终均记录 `builder_item_malformed`。当前私有审计未说明是额外字段、类型错误、index/event_key 不匹配、title 引用 ID 错误，还是展示目标结构错误，因此不能断言其一定是解析器 bug，也不能认定两条都应入选。

- [ ] 追踪 `_strict_item` 各拒绝分支及审计传播入口，用合成响应复现有区分价值的结构失败。
- [ ] 添加有界、可序列化的私有结构诊断（例如稳定的字段/失败类别），保留现有顶层原因码，避免破坏现有公开契约和统计。
- [ ] 至少区分顶层字段/类型错误、索引或事件身份不匹配、展示目标错误和标题 quote ID 无法解析；不得把原始模型响应或源文本当作诊断字段写出。
- [ ] 核实并保护现有 title 有效、brief quote 无法解析时的 title_only 降级；title quote 无效仍必须重建/拒绝，不能模糊匹配或使用整篇 evidence_text 兜底。
- [ ] 在 `tests/test_brief_builder.py`、`tests/test_fact_brief_pipeline.py`、必要的公开产物边界测试中验证诊断传递、预算不变及公开产物不含细节。

验收：未来同类失败可定位到结构环节，原有严格拒绝与安全降级保持有效。本任务不应通过放宽结构验证制造“修复成功”。

## 不纳入本次修复

- VentureBeat/arXiv 429：已正确有界降级，不增加重试预算，不更换代理或凭证。
- HN 外链正文获取、AMD 等域名信任登记：部分来源只有标题，这是供给/证据补全议题，本次不自动增加抓取或扩大可信白名单。
- 所有 `non_news_content` 都直接放行：本次只处理可复现的明确动作漏识别，不能将未核实样例当作可发布事实。
- 候选池扩容、放宽时间窗口、凑够 5 条或 2 条事实、提高 X 配额、关闭去重。
- 修改 Flask、真实微信草稿、生产配置和服务器部署。

## 文档同步与最终验收

- [ ] 按实际实现同步 `AGENTS.md`、`project_docs/pipeline.md`、`project_docs/backend.md`；若变更来源分类契约，同步 `project_docs/sources.md`。
- [ ] 每个确认问题先运行新增回归验证失败，再最小修复并运行相关测试。不为纯文档新增测试。
- [ ] 精准验证：`python -m pytest -q tests/test_publishability.py tests/test_content_classification.py tests/test_opinion_rules.py tests/test_brief_builder.py tests/test_brief_validator.py tests/test_fact_brief_pipeline.py tests/test_draft_decision.py`。
- [ ] 完整验证：`python -m pytest -q`；测试进程可设置 `PYTHON_DOTENV_DISABLED=1` 避免加载真实 `.env`。
- [ ] 运行 `git diff --check`、`git status --short`，审查最终差异，只保留任务相关改动，删除本次无用临时文件。
- [ ] 简洁报告每项根因、修改文件、失败与通过的实际验证、遗留不确定性。报告不保证下次真实干跑一定达到发布条数。

交付判据：上述四项的回归、实现和私有诊断边界完成，受影响及完整测试通过（已知基线警告单列），代码与文档一致。若某项不能在现有事实约束内修复，提供具体证据，不放宽规则掩盖问题。
