# V377 外部参考审计

日期：2026-07-22

本审计只记录能由本地文件复核的机制。外部项目的宣传语、模型输出和未执行示例不作为 HashMM 功能证据。

## 参考材料与完整性

- Codex 本地源码文档：`D:/sheji/codex-main/codex-rs/app-server/README.md`
  - 大小：152,892 bytes
  - SHA-256：`d141beed3d43959de6ad65b36e172224da27466dd690b1da4f0a415953cf0903`
- Hermes Agent 压缩包：`D:/edge downloads/hermes-agent-main (4).zip`
  - SHA-256：`2bbfd002635138aa50cdf6fbcb8acb9fda4335614c8dda9387696a26258174f6`
  - 压缩包内 `LICENSE` 为 MIT；本轮未复制其源码，只移植经重新实现的产品机制。
- 面试实战资料：`C:/Users/Administrator/Desktop/大模型应用!算法学习路线+八股+面试实战4.md`
  - 大小：1,427,064 bytes
  - SHA-256：`ee6d42a4e8a14af33da24d13ab85cf4cefc12c26635b7efa978ffda82f80742f`

## 采用的机制

1. **会话是控制面实体，不只是一次模型调用。** Codex 文档区分 thread、turn、resume、fork、interrupt、steer 和 compaction；Hermes 的上下文引擎也具有真实 session start/end 生命周期。HashMM 因此把子 Agent 投影为 `hashmm.agent-session.v1`，在开始时登记父会话和派生执行范围，结束时保存有界结果摘要。
2. **停止必须指向实际执行会话。** 团队停止不再只设置团队布尔值；服务端会同时向当前所有子 AgentSession 发出中断请求。无法硬杀的模型调用返回后丢弃结果，避免把停止后的输出冒充交付。
3. **上下文压缩与检索证据分层。** Codex 支持显式 thread compaction，Hermes 把上下文引擎做成可替换生命周期组件。HashMM 保留既有 CAS 压缩和结构化 run manifest，不把工具结果、文件、来源与核验账本压成不可审计的自然语言。
4. **每个子 Agent 有独立预算和最小工具集。** Hermes 的每 Agent 迭代预算和 Codex 的线程级权限启发了 HashMM 的派生 execution scope。V377 在桌面端/App 展示实际 session、工具调用数和授权能力，而不是显示一个笼统的“多 Agent 已开启”。
5. **Graph Engineering 必须回到原始证据。** 面试资料强调混合召回、重排、Graph RAG、ACL、全链路耗时和可评测性。HashMM 的 KG 扩展只补入索引中的原始 chunk，重新执行文档 ACL，并记录新增、缺失和越权节点数量；图谱摘要本身不冒充原文证据。
6. **程序判定优先于模型自评。** 面试资料明确强调 pytest/typecheck 等外部检查。HashMM 的工具结果状态改为结构化解析，不能再凭结果文本是否带某个符号推断成功；完成门继续基于真实工具、来源、产物和检查记录。

## 明确不采用

- 不复制 Hermes 的 CLI/TUI、云终端供应商、第三方网关或凭据体系；HashMM 的产品入口仍是 Chat、桌面右栏和 App 伴随控制。
- 不把 Codex 的未稳定实验接口逐项仿制成页面；只有 HashMM 后端已经具备真实状态和安全边界的能力才进入 UI。
- 不允许 Agent 自己扩权、把未知工具当只读、把 KG 关系文本当事实，或在缺少测试证据时声称“已完成”。
- 不把 App 伪装成本地执行器；本地文件、浏览器和 Computer Use 的特权副作用仍由同账号在线桌面执行器承担。
