# HashMM V2500 / Evidence Workbench 5.0

## 目标

V2500 将模型连接、Chat、插件、多 Agent 与 Canvas 收进同一条可恢复、可审计的工作链。页面存在、模型描述和 HTTP 200 都不是完成证据；真实状态来自持久运行、能力清单、操作日志、执行回执与用户验收。

## Provider Fabric

- 北向 `/v1` 继续使用 HashMM API Key；南向 Provider Fabric 保存用户自己的官方 API、获授权 Sub2API、兼容网关或显式本地运行时。两类凭据不复用、不转发。
- 每个连接属于唯一账号，密钥使用 `HASHMM_SECRET` 派生的 Fernet 密钥加密，列表永不回显密钥。
- 公网连接强制 HTTPS，并拒绝回环、私网、链路本地、保留地址和 URL 内嵌凭据；只有 `kind=local` 可使用本机 HTTP。
- 连接包含模型通道、优先级、权重、并发上限、能力声明、健康样本、持久熔断状态、租约、请求尝试和用量事件。
- “用于 Chat”是显式动作：只有用户主动激活后才进入现有模型偏好链。流式首 Token 出现后禁止切换上游。
- Sub2API 是外部、获授权的 OpenAI-compatible 网关连接，不复制其服务端源码，不提供消费者订阅共享、账号池绕过或授权规避。

## Chat Kernel

- 运行状态以 `WorkRun + event_seq + after_seq` 为权威；草稿、运行、等待用户、等待审批、暂停、完成、失败和取消均不得从按钮文案推断。
- Composer 使用服务端 `runtime-capabilities` 的 `production_ready` 投影，只展示本客户端真实可执行的浏览器、电脑、Canvas 和多 Agent 入口。
- 专注模式折叠工具活动与过程卡片，正文和证据优先；完整轨迹仍保留在统一检查器，不删除执行事实。
- 跨 Chat 接力仅传目标、验收、已完成/待完成、运行/检查点、项目/工作区、附件、证据、风险和产物，不传隐藏思维链，也不继承审批。

## 插件契约

- `hashmm.plugin.v1` 在原工具清单上统一投影 `skills/connectors/hooks/ui/scheduled_tasks/compatibility`。
- 稳定生命周期为 `discovered → inspected → awaiting_approval → trusted → active`，异常进入 `quarantined`，字节或信任变化进入 `revoked`。
- Python 插件继续使用精确摘要绑定的短生命周期子进程；这提供崩溃隔离，不宣称操作系统沙箱。MCP 仍是第三方集成的首选边界。

## Team Run

- 启动契约固定包含 objective、scope、inputs、workspace_id、allowed_tools、budget、acceptance、deadline、parent_run_id、result_schema。
- 包含写入工具时必须绑定受管 `workspace_id`；实际工具集合只能是运行时工具集与契约白名单的交集。
- 只持久化计划、决定摘要、动作、证据和验收，不保存或展示隐藏思维链。

## Evidence Canvas

- Canvas 增加账号/会话所有者校验下的 `hashmm.canvas-ops.v1` 操作日志。
- 每次写入携带 `op_id/client_mutation_id + expected_revision`；重复请求幂等返回，陈旧版本返回 409，不覆盖较新状态。
- 操作体最大 64 KiB，日志有界；快照使用 SHA-256 内容摘要和操作版本生成回执。附件正文与凭据不进入操作日志。
- 当前实现适用于单账号多设备的顺序操作合并。多人同段富文本并发仍需成熟 CRDT/OT 引擎，V2500 不把自研操作日志冒充 CRDT。

## 发布门禁

- 后端：所有者隔离、凭据不回显、SSRF、幂等、版本冲突、插件清单与 Team 写入边界。
- 前端：Provider Fabric 真 API 接线、能力感知 Composer、专注模式、typecheck/test/build。
- 桌面：MCP/版本一致性、source-only release gate；最终安装器必须由 `installer-native/build-all.bat` 生成并核对 EXE、`.sha256` 与 `.release.json`。

## 明确边界

- 未配置真实上游账号时，自动测试只证明协议和安全边界，不证明第三方额度、费率、模型权限或公网 SLA。
- 当前 Provider Fabric 已持久化完整路由控制面，并以显式激活接入 Chat；跨不同 Base URL 的请求级加权执行器和多实例共享租约仍需 Redis/Postgres 生产部署验收。
- 插件 Python 子进程不是容器沙箱；Canvas 操作日志不是多人 CRDT；这些不会被标记为已完成能力。

