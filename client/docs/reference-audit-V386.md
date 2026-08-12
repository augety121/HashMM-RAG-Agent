# V386 外部参考与机制迁移审计

日期：2026-07-22

本审计只采用可由本地文件、运行代码和回归测试复核的机制。外部项目 README 的宣传、模型自述和未执行示例不作为 HashMM 已具备能力的证据。

## 参考材料与完整性

- Codex 源码压缩包：`D:/edge downloads/codex-main (1).zip`
  - 大小：12,643,011 bytes
  - SHA-256：`5cc0be46bcb226ba884615aab9542e611907cc1176d1e9921bc20cb6065ad9d1`
- Codex 本地源码：`D:/sheji/codex-main`
  - 本轮核对工具注册表 `codex-rs/core/src/tools/registry.rs`
  - 大小：26,535 bytes
  - SHA-256：`1165a8527aef6e07b16fefeab7daadda8e1699abf825df8d1c13758774b7625d`
- OpenClaw 压缩包：`D:/edge downloads/openclaw-main.zip`
  - 大小：99,793,713 bytes
  - SHA-256：`1ac8d4b6f8ea039e3e2ecb73de1f1c4fe8307f6eb2055a368569a8781a848de4`
- Hermes Agent 压缩包：`D:/edge downloads/hermes-agent-main (4).zip`
  - 大小：77,437,712 bytes
  - SHA-256：`2bbfd002635138aa50cdf6fbcb8acb9fda4335614c8dda9387696a26258174f6`
- 面试实战资料：`C:/Users/Administrator/Desktop/大模型应用!算法学习路线+八股+面试实战4.md`
  - 大小：1,427,064 bytes
  - SHA-256：`ee6d42a4e8a14af33da24d13ab85cf4cefc12c26635b7efa978ffda82f80742f`

## 本轮采用并重新实现的机制

1. **冻结的一轮上下文。** 参考 Codex 的 turn 级状态边界，HashMM 在模型看到工具前冻结属主指纹、会话、目标指纹、作用域、审批、联网模式、预算和真实可执行能力；运行中不接受模型扩权。
2. **schema 与 executor 必须相交。** 参考 Codex 工具注册与执行分层，能力只有同时具有合法 schema、服务端作用域授权和可调用执行器才会暴露给模型。空或畸形作用域按 deny-all 处理，页面存在不再等于能力可用。
3. **有界生命周期轨迹。** 参考 Hermes 的 JSONL trajectory 与稳定参数签名，HashMM 只保存事件类型、状态、耗时、工具名和不可逆参数哈希；不保存原始参数、凭据、工具正文或模型思维链。
4. **粘性终止结果。** 参考 OpenClaw 的终止归一化，取消、硬超时、等待审批和等待输入不会被迟到回调覆盖成成功。桌面端和 App 读取同一规范化终态。
5. **子 Agent 准入而非无限派生。** 参考 OpenClaw 的深度、活跃数和总数上限，普通与流式 worker 共用同一准入；子任务结束会触发真实 `SubagentStop` 生命周期，失败、取消与阻塞不会冒充完成。
6. **压缩前检查点。** 结合 Codex compaction、Hermes session lifecycle 与面试资料的 memory-flush 要求，AgentLoop 在丢弃旧消息前先运行 `PreCompact`，再写属主绑定的确定性摘要和数据库检查点；结束后再次保存本轮代际、工具调用数与恢复标识。
7. **检索证据保持不可信。** RAG 结果进入统一上下文生命周期用于预算、恢复和可观察性，但仍标为 `untrusted_data`，不会因被缓存或压缩而升级为系统指令。
8. **跨端只投影运行事实。** run manifest 和 work runtime 只向桌面端/App 投影有界 harness 与 context lifecycle；滚动摘要、用户资料、完整回答和原始工具参数不会复制到工作账本。
9. **评测验证框架而非只验答案。** 按面试资料的组件、集成、轨迹、多轮和多 Agent 评测思路，新增 capability wiring、轨迹连续性、PreCompact、SubagentStop、恢复检查点、敏感字段剔除和跨端投影回归。

## 明确未采用

- 不复制 Codex、OpenClaw 或 Hermes 的 CLI/TUI、品牌、云端凭据、供应商网关和未稳定实验接口。
- 不把外部仓库中的演示 Agent、示例 skill 或页面直接登记为 HashMM 能力；没有执行器、权限边界和测试的功能不会暴露给 Chat。
- 不允许未知工具默认只读，不允许子 Agent 自己提升深度、worker 数或联网权限。
- 不把上下文摘要当作事实来源，不把参数哈希当作执行成功证明，也不把模型自评当作完成门。
- 不把 App 伪装为桌面特权执行器。文件、Browser Use、Computer Use 和系统操作仍由同账号在线桌面执行器领取，App 负责发起、审批、查看与续接。

## 许可证与实现边界

本轮为机制级重新实现，没有把上述仓库源码复制进 HashMM 发布包。各外部项目的许可证仍约束其原始代码；本审计的哈希用于确定本轮实际查看的输入版本，不表示 HashMM 与外部项目存在官方兼容或背书关系。
