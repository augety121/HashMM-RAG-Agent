# HashMM V1300 / Cloud Workspace Runtime 1.0 + App 2.0 Spec

状态：实现基线（2026-08-09）  
适用范围：HashMM 后端、桌面端、Web UI、Android App  
上游基线：HashMM V1200 / Secure Remote Workspace 3.0 + Agent Retrieval Fabric 1.0

本文件同时记录目标契约与当前落地状态。当前已完成源码接入、自动化测试和 App Debug 构建；尚未在用户 Cloudflare 账号部署 Worker，也尚未完成双账号公网隔离、Durable Object 恢复和 Android 真机弱网验收。

## 1. 版本目标

V1300 不再增加彼此孤立的入口，而是把“对话、工作、远程执行、移动端接力”接入同一条可恢复事实链。首个云执行后端直接采用官方 `@cloudflare/computer`，不复制其 VFS 或运行时实现。

本版本必须同时交付四件事：

1. Cloudflare Computer Worker：Durable Object + SQLite VFS + 官方发布包的 `WorkerBackend`，提供经过认证、限额、审计友好的 HashMM 网关。
2. HashMM Runtime Adapter：后端作为业务与权限权威，将 owner/workspace/run 映射到云工作空间；客户端不得直接持有 Worker Secret。
3. Desktop Runtime 控制面：展示配置、可达性、运行后端与降级原因；未配置时不得伪装可用。
4. App 2.0：保持“我的”和“对话”的可见界面不变，重构其同步内核；允许重构“今天”和“工作”，使其成为同一工作事实的移动投影。

## 2. 明确边界

### 2.1 本版本要做

- 直接依赖 `@cloudflare/computer`，锁定已审计的版本，不使用自研同名兼容层。
- 支持 Worker Shell 云运行后端；容器后端作为协议兼容的后续升级位，不在本版本伪装完成。
- 每个远程执行请求都经过 Supabase 登录态对应的 HashMM 后端鉴权，再由后端使用专用 Worker Secret 调用 Cloudflare。
- owner、workspace、project 和 run 的查询均做 owner 约束；缺失与越权返回不可枚举的相同结果。
- 命令执行、文件写入、输出读取产生可关联的 run/event/receipt，不把模型文本当作执行证据。
- App 的离线缓存、增量游标、冲突、删除墓碑和重试状态统一建模。

### 2.2 本版本不声称完成

- 没有 Cloudflare 账号、部署令牌和 Worker Secret 时，只能验收源码、类型检查和本地协议测试，不能声称公网 Worker 已部署。
- `@cloudflare/computer` 当前为 `0.1.0-alpha.1` Preview。它可以直接使用，但生产默认状态必须显示“预览后端”，并保留关闭开关和本地/桌面降级路径。
- Worker Shell 不等同于完整 Linux 容器，不支持的二进制、系统调用或长驻进程必须返回结构化 capability 错误。
- 本版本不修改“我的”和“对话”的 Compose 布局、文案结构、导航位置、组件视觉或交互层级。
- 不把 Supabase 当成任务执行权威；Supabase 负责身份和必要的唤醒/同步传输，HashMM 后端数据库保存规范工作事实。

## 3. 统一对象模型

### 3.1 标识与归属

| 对象 | 规范主键 | 必须归属 | 说明 |
|---|---|---|---|
| Workspace | `workspace_id` | `owner_id` | personal/project workspace |
| Work Run | `run_id` | `owner_id + workspace_id` | 唯一任务状态机 |
| Runtime Execution | `execution_id` | `owner_id + run_id` | 一次云命令或文件操作 |
| Conversation | `conv_id` | `owner_id` | 可关联 run，但不是 run 本身 |
| Sync Event | `cursor` | `owner_id` | 单调递增、可重放 |
| Artifact/Receipt | `artifact_id` | `owner_id + run_id` | 结果与执行证据 |

Cloudflare Durable Object 名称不得包含原始邮箱、Supabase UID、project_id 或本地路径。后端使用服务端密钥计算：

`workspace_handle = base32(HMAC-SHA256(secret, owner_id + NUL + workspace_id))[:40]`

### 3.2 Run 状态机

规范状态：

`queued -> admitted -> running -> waiting_approval|waiting_input|paused -> running -> verifying -> completed`

终态：`completed | failed | cancelled | partial`。服务重启后只从持久化 checkpoint 和已承认 event 恢复；禁止根据 UI 文案猜测状态。

每个变更命令必须携带 `idempotency_key` 和 `expected_revision`。重复键返回原结果；修订不匹配返回 409 和当前 revision，不进行隐式覆盖。

## 4. Cloudflare Computer 直接接入

### 4.1 采用的官方能力

- 包：`@cloudflare/computer@0.1.0-alpha.1`。
- 工作区：`withWorkspace(...)` + Durable Object SQLite storage。
- 运行：npm 已发布的 `0.1.0-alpha.1` 使用 `WorkerBackend` + Worker Loader；上传的较新源码快照已改名为 `WorkerShellBackend`。构建以 lockfile 实际解析的发布包 API 为准。
- 文件：`workspace.fs.readFile/writeFile`，仅允许 `/workspace` 下的规范路径。
- 命令：已发布包使用 `workspace.shell.exec(...).result()`；较新源码快照的对应接口为 `workspace.runtime.exec(...)`。
- 首批网关命令白名单仅包含无网络的 core 文本/文件命令。`curl` 由网关拒绝，实时检索统一经过 Retrieval Fabric。新增命令必须显式评审权限并更新 capability manifest。

### 4.2 Worker HTTP 契约

- `GET /v1/health`：不泄露租户与 Secret，只返回协议、版本和 readiness。
- `POST /v1/workspaces/{handle}/exec`：仅接收 `argv`，默认不接受任意 shell 字符串；`cwd` 必须位于 `/workspace`。
- `PUT /v1/workspaces/{handle}/files/{path}`：大小、路径深度和总配额限制。
- `GET /v1/workspaces/{handle}/files/{path}`：流式返回，禁止目录穿越。

所有 `/v1/workspaces/**` 请求必须携带 `Authorization: Bearer <gateway-secret>`；认证失败统一 404 或 401（按端点威胁模型确定），响应不得回显 token、内部 DO id 或堆栈。

默认限制：

- JSON 请求 256 KiB；单文件 8 MiB；单次输出 2 MiB。
- argv 最多 64 项，单项 4096 字符；cwd 最长 512 字符。
- 后端调用超时 60 秒；超时后记录 `timed_out`，不能把未知结果标记成失败后安全重试。
- 只允许后端到 Worker 的服务调用；App、浏览器和 Electron Renderer 不保存网关密钥。

### 4.3 Provider 协议

HashMM 定义稳定的 `WorkspaceRuntimeProvider`：

- `capabilities()`
- `health()`
- `exec(owner_id, workspace_id, argv, cwd, limits)`
- `write_file(owner_id, workspace_id, path, bytes)`
- `read_file(owner_id, workspace_id, path)`

首个 provider 为 `cloudflare_computer_worker_shell`。未来容器、本机和其他云后端必须实现同一协议，UI 不依赖具体厂商字段。

## 5. HashMM 后端控制面

新增认证 API：

- `GET /api/v3/runtime/providers`
- `GET /api/v3/runtime/providers/cloudflare-computer/health`
- `POST /api/v3/runtime/workspaces/{workspace_id}/executions`
- `GET /api/v3/runtime/workspaces/{workspace_id}/executions/{execution_id}`
- `GET /api/v3/runtime/workspaces/{workspace_id}/executions/{execution_id}/events`

执行创建要求：

- 验证 Supabase/HashMM 登录态和 workspace owner。
- 验证关联 run 的 owner、workspace 与可执行状态。
- 由服务端权限策略决定是否需要审批；管理员账号也不自动绕过用户级审批。
- 记录 provider、capability、输入摘要、输出摘要、耗时、退出码和证据 hash；不记录 token 与完整敏感环境变量。
- 未配置返回 `configured=false` / 503，不退化为未审计的本机 shell。

配置项：

- `HASHMM_CLOUDFLARE_COMPUTER_URL`
- `HASHMM_CLOUDFLARE_COMPUTER_TOKEN`
- `HASHMM_CLOUDFLARE_COMPUTER_TIMEOUT_SECONDS`
- `HASHMM_CLOUDFLARE_COMPUTER_ENABLED`
- `HASHMM_WORKSPACE_HANDLE_SECRET`（至少 32 字节，独立于登录令牌）

Secret 只从服务端环境或凭据库读取，绝不进入前端配置 JSON、日志、健康响应或安装包示例值。

## 6. Desktop / Web UI

工作区控制面展示：

- 当前 provider：本地桌面 / Cloudflare Computer Preview / 不可用。
- 配置状态、最后健康检查、协议版本、capability 与明确降级原因。
- execution 与 work run 的关联、当前阶段、审批、退出码、产物和 receipt。
- Cloudflare 未部署时显示“未配置”，不得显示“在线”或可执行按钮。

Renderer 只调用 HashMM API。若新增 Electron IPC，必须经过 `desktop/modules/ipc-guard.js` 分类并通过窄 preload 暴露；本版本的 Cloudflare Secret 不通过 IPC。

## 7. App 2.0

### 7.1 UI 冻结区：我的、对话

以下文件对应的可见 Compose 树原则上不改：Profile、ChatHome、ChatList、ChatDetail 及底部导航中“我的/对话”的可见结构。

允许修改：

- Repository、data source、DTO、cursor、outbox、冲突合并、ViewModel 内部状态转换。
- 网络错误分类、离线恢复、分页和性能，但不能增加/删除可见入口或改变视觉布局。
- 通过既有状态位展示真实同步结果；若现有 UI 无展示位，仅记录可诊断状态，不擅自改 UI。

必须解决：

- 冷启动先显示 owner 隔离的本地快照，再从 HashMM 增量 API 对账。
- 规范读取优先走后端 owner-checked API：会话使用稳定 keyset cursor 拉到耗尽，消息使用稳定时间边界拉到耗尽；不再把 `updated_at > since` 当作规范路径。
- 删除通过 tombstone 同步；分页耗尽前不能推进高水位。
- Realtime 只作为“有变化”的提示，规范内容仍由增量同步拉取。
- 同一 conv 的消息按 server revision 合并；本地尚未确认消息使用 client mutation id 去重。
- 切换账号时立即切换数据命名空间，禁止展示上一账号缓存。

### 7.2 可调整区：今天

“今天”是行动收件箱，不是多个 API 卡片的拼盘，只回答：

1. 现在需要我处理什么？
2. 哪些工作仍在推进？
3. 今天交付了什么？
4. 哪些任务离线或降级？

数据只来自统一 workspace snapshot/run events。审批、补充信息、失败重试必须回到同一 run，不能新建影子任务。

### 7.3 可调整区：工作

“工作”是跨端 Run 台账：按 `需要处理 / 进行中 / 已完成 / 失败与部分完成` 投影。每项显示可证明状态、当前步骤、更新时间、执行位置和产物数量。打开后进入同一 work canvas；暂停、恢复、取消、审批均携带 expected revision。

Cloudflare execution 是 run 的一个执行后端，不新增第五个顶级 Tab，也不与“设备接力”混成同一概念：

- 接力：同一工作由哪个客户端继续交互。
- 执行位置：具体动作在哪个 runtime 运行。

### 7.4 App 同步协议

本轮以 `BackendChatSyncGateway` 落地第一阶段同步抽象：

- primary：HashMM `/api/conversations`、分页消息和复合游标 tombstone；完整快照拉取成功后才提交本地状态。
- signal：Supabase Realtime，只触发 refresh。
- cache：LocalStore，按 owner + protocol version 分区。
- work outbox：既有 `WorkRuntimeRepository` 继续使用 owner-scoped 加密 outbox、稳定 idempotency id 与 expected revision；Chat mutation outbox 是后续协议升级位，本轮不伪装已经具备。

旧 Supabase 表直读保留为受控兼容模式，只有后端明确宣告旧协议或不可升级时启用，并在诊断中显示 `legacy_direct_supabase`。

删除游标采用 `(deleted_at, conversation_id)`，而不是仅用浮点时间戳；同一删除时间超过一页也不得漏项。当前 conversation/message 同步是“稳定分页完整对账”，尚不是统一 workspace change-log，后续可在不改 UI 的前提下替换 gateway 实现。

## 8. 安全与故障语义

- Worker、HashMM、App 三层都拒绝 `..`、绝对路径逃逸、NUL 和超限 payload。
- URL 只允许 HTTPS（本地测试地址例外且必须显式开发模式）。
- SSRF：用户不能通过配置任意 Worker URL；普通账号只能使用管理员预配置 provider。
- 插件与 Skills 不能直接获得 Gateway Secret；调用云 runtime 必须经过 tool permission 和 run 审计。
- 网络中断时 execution 状态为 `unknown` 或 `timed_out`，不能未经查询就重复具有副作用的命令。
- 健康检查不执行用户命令，不创建业务文件，不消耗真实任务配额。

## 9. 验收门槛

### 9.1 必须自动通过

- Worker：typecheck；鉴权、路径逃逸、argv 限额、文件限额和错误脱敏测试。
- Backend：provider 未配置、健康检查、owner 隔离、workspace/run 归属、稳定 handle、超时和输出限额测试。
- Frontend：typecheck、现有测试、Workspace provider 状态测试。
- App：“我的/对话”UI 源文件无结构性修改；后端分页/tombstone/账号隔离单测；既有 Work outbox 回归；“今天/工作”投影单测。
- Release：Python 全测、前端 test/typecheck/build、Desktop source gate、App unit tests 与 assembleDebug。

### 9.2 外部环境验收

- Cloudflare 真实账号部署成功、Secret 设置、健康检查通过。
- 两个账号相互不可枚举 workspace/execution/file。
- Worker 重启或 DO 迁移后 `/workspace` 文件可恢复。
- 重复 idempotency key 不重复执行；超时后可查询最终状态。
- App 弱网、杀进程、切账号、跨端同时编辑和服务重启测试。

外部环境验收未执行时，发布说明必须列为“待部署验证”，不能写“生产可用”。

## 10. 发布与回滚

- Worker 依赖精确锁版本并提交 lockfile；升级 `@cloudflare/computer` 必须重新跑契约测试。
- Cloudflare provider 使用独立 feature flag，可在不迁移用户数据的情况下关闭并退回桌面 runtime。
- 数据库只做向前兼容迁移；旧 App 可继续读取 conversation，V1300 App 才使用 workspace cursor。
- 发布文件同步更新 backend `RELEASE`、Python/desktop/frontend/App 版本、协议版本、当前轮说明与 changelog。
- 任何真实 Secret、账号 ID、Cloudflare API Token 都不得进入仓库和构建产物。

## 11. 本轮完成定义

“V1300 源码完成”要求第 9.1 全部通过。  
“Cloudflare Computer 已部署”要求第 9.2 的 Cloudflare 部署、鉴权与双账号隔离通过。  
“App 2.0 源码完成”要求不改变“我的/对话”界面的前提下，同步内核测试、全量单测与 Debug 构建通过；“App 2.0 真机验收完成”还要求“今天/工作”的真实设备、弱网、杀进程和切账号关键路径通过。

三种状态必须分别报告，不得互相替代。
