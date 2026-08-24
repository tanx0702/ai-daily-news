# Task 2：X 动态确定性分类报告

## RED

- 新增 `tests/test_ai_update_rules.py` 和 X 分类优先级测试后，运行：
  `python -m pytest -q tests/test_ai_update_rules.py tests/test_x_feed_collector.py`
- 结果：预期失败，`ModuleNotFoundError: No module named 'src.briefing.update'`。
- 失败原因是任务要求的新确定性资格模块尚未存在。

## GREEN

- 新增 `src/briefing/update.py`：提供不可变、可序列化的 `UpdateEligibility`，并使用规则拒绝推广/转发、纯链接、内容过短和缺少具体技术锚点的内容。
- 技术锚点只匹配模型/版本、百分比/排名/速度等机械数值，或 benchmark、leaderboard、evaluation、experiment、framework、training、inference、GGUF、quant 及对应中文对象；没有 LLM 分类。
- `src/collectors/x_feed.py` 的分类顺序为：合格观点优先；任何 `asserted_action_types()` 的明确动作保留 `fact_event`；其后才将合格技术动态标为 `ai_update`；其余仍为 `fact_event`。
- 重新运行：`python -m pytest -q tests/test_ai_update_rules.py tests/test_x_feed_collector.py`
- 结果：`25 passed in 0.80s`。

## 变更

- 新增 `src/briefing/update.py` 与 `tests/test_ai_update_rules.py`。
- 修改 `src/collectors/x_feed.py` 与 `tests/test_x_feed_collector.py`。
- 未修改 `.env`、`docs/` 产物或任务范围外的项目文件。

## 自审与验证

- `python -m pytest -q tests/test_opinion_rules.py tests/test_ai_update_rules.py tests/test_x_feed_collector.py`：`31 passed in 0.85s`。
- `git diff --check`：通过。
- `python -m pytest -q`：`552 passed, 2 failed, 1 warning`。失败均不在本任务修改路径：
  - `tests/test_draft_decision.py::test_decision_requires_three_facts_and_limits_opinions_and_authors`：实际原因码为 `duplicate_event_remaining`，测试期望 `opinion_limit`。
  - `tests/test_main_publish_filter.py::test_invalid_configuration_fails_before_any_external_or_render_call`：实际状态为 `blocked`，测试期望 `failed`。
- 这两项失败涉及草稿决策/主流程预检；本提交只改 X 采集分类与其测试，未改动上述模块。

## Commit

- `5222ad5 feat(x): 分类可追溯 AI 圈动态`

## 审查修复：RED

- 新增标题型推广、裸排名和非观点账户技术评论回归测试后，运行：
  `python -m pytest -q tests/test_ai_update_rules.py tests/test_x_feed_collector.py tests/test_opinion_rules.py`
- 结果：`3 failed, 31 passed in 1.10s`。
  - 标题型推广被错误判为动态；
  - 裸 `#6` 排名被错误判为动态；
  - 非观点白名单账户的 `I think benchmark rankings are overrated in model training today` 被错误标为 `ai_update`。

## 审查修复：GREEN

- `evaluate_ai_update_candidate()` 现在把同一规范化的正文/标题回退文本覆盖到拒绝检查使用的 `summary`，同时复制原候选，保留 `x_is_repost` 等字段。
- 具体动态资格现在要求明确模型/版本、可量化性能或排名、以及明确结果关系同时存在；通用技术词和裸排名不再单独成为资格锚点。
- 重新运行：
  `python -m pytest -q tests/test_ai_update_rules.py tests/test_x_feed_collector.py tests/test_opinion_rules.py`
- 结果：`34 passed in 0.85s`。

## 审查修复：自审与 Commit

- `git diff --check`：通过。
- 暂存内容只包含 `src/briefing/update.py`、`tests/test_ai_update_rules.py`、`tests/test_x_feed_collector.py`。
- `824c424 fix(x): 收紧 AI 动态分类边界`

## 第二轮审查修复：RED

- 新增两个有效结果回归后，运行：
  `python -m pytest -q tests/test_ai_update_rules.py tests/test_x_feed_collector.py tests/test_opinion_rules.py`
- 结果：`2 failed, 34 passed in 1.10s`。
  - `Model 2.0 scores higher on the benchmark`；
  - `模型 2.0 在基准测试中达到第 6 名`；
  两者均被过度收紧的三项强制条件以 `update_missing_concrete_anchor` 拒绝。

## 第二轮审查修复：GREEN

- 动态规则仍要求明确结果/进展关系，并要求：模型/版本或技术对象，且同时有机械量化/排名或可辨识的 benchmark-result 表述。
- 新增中文“第 N 名”排名识别；裸 `#N`、推广和非观点 benchmark 评论仍由既有负向测试覆盖。
- 重新运行：
  `python -m pytest -q tests/test_ai_update_rules.py tests/test_x_feed_collector.py tests/test_opinion_rules.py`
- 结果：`36 passed in 0.84s`。

## 第二轮审查修复：自审与 Commit

- `git diff --check`：通过。
- 暂存内容仅包含 `src/briefing/update.py`、`tests/test_ai_update_rules.py`。
- `f97882c fix(x): 平衡 AI 动态结果识别`

## 第三轮审查修复：RED

- 新增泛化动态负向测试后，运行：
  `python -m pytest -q tests/test_ai_update_rules.py tests/test_x_feed_collector.py tests/test_opinion_rules.py`
- 结果：`1 failed, 36 passed in 1.29s`。
- `I think benchmark scores are higher this year for model training` 被错误接受为 `ai_update`，因为先前技术对象分支把 benchmark-result 当作机械锚点替代。

## 第三轮审查修复：GREEN

- `_has_update_anchor()` 使用精确布尔式：
  `result_relation && ((model_version && (mechanical_progress || benchmark_result)) || (technical_object && mechanical_progress))`。
- 因此模型版本的明确 benchmark 结果仍可进入动态；仅具技术对象的动态必须具有机械量化/排名；泛化评论、裸排名、推广和非观点评论保持拒绝。
- 重新运行：
  `python -m pytest -q tests/test_ai_update_rules.py tests/test_x_feed_collector.py tests/test_opinion_rules.py`
- 结果：`37 passed in 0.82s`。

## 第三轮审查修复：自审与 Commit

- `git diff --check`：通过。
- 暂存内容仅包含 `src/briefing/update.py`、`tests/test_ai_update_rules.py`。
- `532f89c fix(x): 拒绝无机械锚点的泛化动态`

## 文档缺口修复

- `project_docs/sources.md`：补充 X 候选确定性分类优先级 `attributed_opinion → fact_event → ai_update`，并明确硬新闻动作、机械可核验动态锚点及推广/转发/纯链接/泛评论的边界。
- `project_docs/architecture.md`：同步同一分类契约及其在采集器与简报层之间的职责边界。
- 检查：`git diff --check` 通过。
- Commit：`docs(x): 同步 AI 动态分类契约`。
