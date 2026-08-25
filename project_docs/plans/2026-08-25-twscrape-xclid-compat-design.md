# twscrape XClId 兼容层设计

## 状态

- 日期：2026-08-25
- 方案：A（项目内可移除兼容补丁）
- 范围：只修复认证 X 快照客户端，不修改日报筛选、LLM、微信草稿或凭证存储。

## 问题与证据

服务器使用 `twscrape==0.20.0` 调用 `user_by_login("OpenAI")` 时返回空结果，底层错误为
`XClIdParseError`。脱敏探针确认认证页面满足以下条件：

- 页面包含验证 meta 和加载动画，且不是 logged-out 页面。
- 页面直接引用 `https://abs.twimg.com/responsive-web/client-web/*.js`。
- 当前脚本 hash 为 16 位，而上游 legacy 解析器只从旧的 7 位 hash map 重建 URL。
- `main` 脚本包含上游 `INDICES_REGEX` 可识别的动画索引，但不再引用外部
  `ondemand.s` 或 `sign.o` 脚本。

在服务器进程内临时加入“发现直接脚本 + 扫描内联索引”后，`OpenAI` 用户查询成功返回用户 ID。
这说明当前 cookie 至少能完成该认证查询，故障边界位于 XClId 脚本发现阶段。

## 决策

新增一个独立兼容模块，只包装 `twscrape.xclid.parse_anim_idx`：

1. 从认证页面提取受信任的 `abs.twimg.com/responsive-web/client-web/*.js` 绝对 URL。
2. 优先读取 `main`，再读取其余受信任脚本。
3. 复用上游 `INDICES_REGEX` 提取内联动画索引，不复制交易 ID 算法。
4. 找到索引即返回给上游 `XClIdGen`；找不到则调用原始 `parse_anim_idx`，保留上游行为和错误。
5. runner 创建 `twscrape.API` 前安装一次补丁；重复安装不叠加包装。

兼容层不解析任意第三方 URL，不向日志写 cookie、请求头、HTML 或脚本正文。单个脚本请求失败只记录异常类型并继续；全部失败后仍由上游错误触发现有按来源降级。

## 测试与验收

- 单元测试覆盖当前 16 位 hash HTML、受信任域名限制、`main` 优先、内联索引成功和上游回退。
- 本地运行 X 相关精确测试、完整测试、`git diff --check`。
- 服务器先查询单个 `OpenAI` 账号，再生成完整 `x-feed.json`。
- 最后使用 `SKIP_WECHAT_DRAFT=1` 安全干跑日报，禁止创建真实微信草稿。

当未来 `twscrape` 原生支持当前认证页面且相同服务器探针不再触发兼容分支时，可删除该模块及其安装调用。
