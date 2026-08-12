# V372 证据门控完成协议

## 目标

HashMM 的 highlight 不是再增加一个“完成度”页面，而是让 Chat、长任务、RAG、多 Agent、Artifact、Browser/Computer Use、MCP、Hook、测试和 Graph Engineering 共享同一条可验证完成语义：系统只有在可观察条件闭环时才能说“已完成”。

这对应三层连续协议：

1. `hashmm.task-evidence-graph.v1` 说明真实运行中发生了什么、哪些对象仍阻塞。
2. `hashmm.execution-frontier.v1` 说明当前持久权限内下一步最小可行动作是什么。
3. `hashmm.completion-gate.v1` 说明哪些证据允许系统宣称完成，以及仍缺什么权威确认。

## 确定性输入

- 用户目标与成功条件：来自启动时持久化的任务契约。
- 运行检查：交付、计划闭合、工具状态、Artifact、角色交付、来源/引用和用户确认。
- 任务证据图：只使用运行记录产生的节点与边，模型推断边固定为 0。
- 执行前沿：只读取已持久化执行范围、真实工具注册表、联网策略和图阻塞。
- 终止原因：非终止状态永远不能通过。
- 工具与 Agent 轨迹：失败、拒绝、未结束与重复调用都保留为失败模式。

门禁不会执行工具、扩大范围、批准权限或证明现实世界结论必然正确。

## 用户验收的权威边界

任务契约中来源为 `user` 的标准，只有 `authority=actual_user_confirmation` 才能通过。LLM judge、规则代理、管理员看板和客户端本地按钮都不能绕过服务端所有者检查。接受与退回只在已交付目标任务上记录；记录会原子持久化并触发检查、图、前沿和门禁重建，但不会恢复执行或产生副作用。

## 与 Codex / Claude Code 的对齐

- Codex 的核心启发是把上下文、执行范围、批准和验证拆开：子 Agent 隔离噪声，主任务保留需求、决策和最终交付；沙箱与批准是两层边界，验证结果不能由模型文本替代。HashMM 将这些原则落实到跨端持久协议，而不是复制界面。
- Claude Code 的核心启发是把子 Agent、权限与 Hook 放进明确生命周期；Hook 可观察和阻断工作流，但不是完整安全边界。HashMM 因此仍以服务端对象所有权、参数绑定权限和执行范围为最终守卫。
- 面试实战资料强调 EDD、轨迹评估、提前完成、规划失败、多 Agent handoff/ablation 和发布门禁。V372 把这些失败模式变成在线可统计状态，不再只看最终回答。

官方参考：

- https://developers.openai.com/codex/subagents
- https://developers.openai.com/codex/security
- https://developers.openai.com/codex/hooks
- https://code.claude.com/docs/en/sub-agents
- https://code.claude.com/docs/en/permissions
- https://code.claude.com/docs/en/hooks
- https://code.claude.com/docs/en/agent-teams

## 当前限制

- 门禁证明的是“当前协议可观察条件闭环”，不是对现实世界事实的绝对证明。
- 引用正确性仍需要保存完整检索结果契约或外部验证；只有摘要时保持 `not_evaluable`。
- Browser/Computer Use 的真实外站、登录态和操作结果仍需在相应生产环境验收。
- Hook 不替代桌面 IPC guard、后端 owner check、参数权限和网络白名单。
- 用户退回后不会自行扩大预算或权限；需要用户明确恢复、重新创建或授权下一轮。

