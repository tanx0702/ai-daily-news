# GLM-5.3-Flash 低推理强度设计

## 背景

智谱官方控制台已经提供 `GLM-5.3-Flash`。此前开发文档的模型枚举未及时包含该型号，但服务器 `open.bigmodel.cn` 已接受 `glm-5.3-flash` 请求，因此不能据旧枚举判断模型不存在。

2026-08-28 服务器安全干跑显示，当前模型在 7 次内容请求中出现 5 次 90 秒超时、1 次 HTTP 200 空正文和 1 次有效 JSON。最小探针进一步确认：该模型强制思考，默认请求可能将输出预算消耗在 `reasoning_content`；显式 `thinking.disabled` 返回 400，而 `reasoning_effort=low` 在 2.91 秒内返回合法 JSON。

## 目标

- 继续使用服务器现有 `glm-5.3-flash` 和智谱官方 API。
- 让生产事实简报中的结构化翻译、质量核验和语义去重使用低推理强度。
- 不影响 DeepSeek、Agnes 或其它 OpenAI 兼容模型。
- 不新增环境变量，不修改现有超时、重建、熔断或事实门禁。

## 方案比较

### 方案 A：所有模型强制发送 `reasoning_effort=low`

代码最少，但其它兼容供应商可能拒绝未知参数，未来切换模型会产生回归。

### 方案 B：为推理强度新增环境变量

最灵活，但需要扩展 `LLMConfig`、环境模板和部署文档；当前只有一个已确认需要特殊参数的模型，属于过度配置。

### 方案 C：按模型名附加兼容参数（采用）

在 `src/llm_config.py` 提供一个无副作用请求选项函数：模型名大小写归一化后等于 `glm-5.3-flash` 时返回 `extra_body={"reasoning_effort":"low"}`，其它模型返回空字典。内容、质量和语义去重三个生产客户端复用该入口。

该方案改动小、可测试，并保持其它供应商请求完全不变。

## 数据流

```text
LLMConfig
  -> structured_llm_request_options(config)
       -> glm-5.3-flash: {extra_body: {reasoning_effort: low}}
       -> other models: {}
  -> chat.completions.create(..., **options)
```

适用调用点：

- `src/briefing/builder.py`：单条中文标题与摘要生成。
- `src/briefing/validator.py`：可选只读质量核验。
- `src/briefing/semantic_reviewer.py`：少量疑似重复关系判断。

历史 `src/summarizer.py`、`src/generator.py` 不属于当前 `DraftDecision` 主链路，本次不修改。

## 错误与兼容边界

- 不发送 `thinking.disabled`，因为服务器已确认该模型拒绝关闭思考。
- 不读取 `reasoning_content` 作为事实输出；生产仍只接受 `message.content` 中的严格 JSON。
- 空正文、无效 JSON、超时和熔断继续使用现有精确原因码与降级行为。
- 模型名不是 `glm-5.3-flash` 时，请求参数与当前版本逐字等价。

## 测试与验收

1. 请求选项函数对 `glm-5.3-flash` 和大小写变体返回低推理强度参数。
2. 其它模型返回空字典。
3. builder、validator、semantic reviewer 使用 GLM 5.3 Flash 时实际请求都包含该参数。
4. 现有单条调用、禁用 SDK 重试、JSON schema、超时熔断和门禁测试保持通过。
5. 完整运行 `python -m pytest -q`、`git diff --check`。
6. 推送唯一发布基线 `master`，服务器快进拉取、重建，并用 `SKIP_WECHAT_DRAFT=1` 安全运行；不得重复创建微信草稿。
