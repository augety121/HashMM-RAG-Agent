# HashMM V2600 / Verified Work Kernel

## 1. 版本目标

V2600 不继续增加互不相干的按钮，而是把 Chat、Work、Provider、桌面端、Android 和发布物收敛到可验证契约。产品版本为 `26.0.0`，后端为 `V2600 / 2.6.0`，桌面端与原生安装器为 `15.0.0`，Android 为 `10.0.0 (260)`。

唯一版本源是 `hashmm/release-manifest.json`。运行时健康接口、桌面发布门禁、Android 打包脚本与产物清单都必须读取它，禁止分别手写“当前版本”。

## 2. 统一事件内核

Work 运行与账号同步变更都携带 `hashmm.event.v1` 信封：

- `event_id / stream_id / event_seq`：稳定标识、流归属和单调序号；
- `actor`：仅记录受控 kind 与账号/系统标识；
- `type / idempotency_key / trace_id / occurred_at`：重试收敛、链路诊断和审计；
- `payload_ref`：只指向脱敏对象，不复制附件、密钥或正文。

服务端是状态权威。客户端以 cursor 恢复，按事件 ID 去重，以 revision 做乐观并发；不得用本地最后写入覆盖服务端终态。正式 JSON Schema 位于 `contracts/hashmm-event-v1.schema.json`，`/api/work-runs/stream` 与 `/api/remote/v4/ws` 的 SSE/WSS 契约位于 `contracts/asyncapi.hashmm.v2600.json`。

## 3. Provider Fabric v2

Provider Connection 表示一个上游账号或授权网关，Channel 表示可独立限流的上游入口。连接和通道严格按 owner 查询；密钥加密保存且不会返回前端。

真实请求路由遵循：

1. 过滤未启用、熔断或租约已满的通道；
2. 优先健康通道，再按 priority 分组；
3. 在同优先级内进行确定性加权选择；
4. conversation/project sticky key 保持上下文稳定；
5. 同一个 request ID 收敛到同一尝试，首 token 后不进行危险重放；
6. 每个通道有并发租约，过期租约会在下一次选择时回收。

管理页可以添加通道、配置模型别名策略和预览脱敏路由证明。预览接口仍执行 owner check，绝不返回 API key。

## 4. Chat、任务与证据

专注模式只减少非关键装饰，不隐藏任务计划、待办、审批或证据状态。Work 的事件信封为桌面端和 App 提供相同恢复语义。任务完成仍必须同时满足终态、产物存在与验证证据，模型文字不作为执行成功证明。

现有多 Agent、插件、Canvas 与 RAG 能力继续复用统一账号和审计边界。Canvas op/revision/snapshot 状态已迁入主数据库，条件 revision 更新可以在 PostgreSQL 多副本中检测并发冲突；SQLite 部署仍是单节点能力。Python 插件增加只读、无网络 audit policy，更宽权限必须走 MCP/隔离工作区；该策略仍不冒充容器、VM 或完整 OS 沙箱。

## 5. Android 与桌面发布

- Android 增加 Compose instrumentation 冒烟测试，CI 编译并在模拟器执行；
- APK 打包前必须与统一清单核对 versionName/versionCode；
- debug 包只用于内部验收；release 文件名包含 `unsigned` 时拒绝作为生产产物；
- 正式签名仅从 `HASHMM_ANDROID_*` 环境变量读取，不把口令写入仓库；
- Windows 发布仍由 `installer-native/build-all.bat` 唯一生成，产物必须与 `.sha256` 和 `.release.json` 一致；
- `scripts/verify-contracts.py` 是无网络、可重复的契约门禁。

## 6. 兼容与滚动升级

最低兼容版本记录在统一清单中。服务端 bootstrap 返回公开兼容信息，客户端发现自身低于最低版本时应进入“只读/要求升级”，不能静默尝试未知协议。数据库与协议升级必须先兼容读、再双写/迁移、最后切换读取，禁止一次部署同时破坏旧端。

## 7. 已实现与诚实边界

本轮已实现：统一版本源、事件信封和契约、Provider 请求时路由及管理操作、Chat 专注模式任务可见性、数据库 Canvas revision 日志、Python 插件 fail-closed audit policy、Android 仪器测试入口、Android/Windows 发布版本门禁和回归测试。

尚需部署环境验收：

- Windows Authenticode 与 Android 正式签名证书的真实签名链；
- 两台真实设备跨 NAT 的 WebRTC 首帧、TURN 降级、断网恢复与 24 小时稳定性；
- 多副本服务下的共享 lease/broker 与数据库并发压测；
- 上游 Provider 的真实配额、首 token 边界和故障注入压测；
- Android 仪器测试需在有 SDK/模拟器或真机的环境运行。

自动化测试通过只能证明代码契约与本地行为，不证明公网、证书、第三方 SLA 或真机网络已经验收。

## 8. 发布验收矩阵

| 门禁 | 命令 | 通过条件 |
|---|---|---|
| 契约 | `python scripts/verify-contracts.py` | 协议与统一清单一致 |
| 后端 | `python -m pytest -q tests` | 无失败，环境能力缺失只能显式 skip |
| 前端 | `npm test && npm run typecheck && npm run build` | 三项全通过 |
| 桌面源 | `python desktop/scripts/verify-release.py --source-only` | 版本、运行时和 WebUI 一致 |
| Android | `gradlew testDebugUnitTest lintDebug assembleDebugAndroidTest` | 单测、lint、仪器包均成功 |
| Windows 产物 | `installer-native/build-all.bat` | EXE、SHA-256、release JSON 三方一致 |
| Android 产物 | `scripts/package-app.ps1 -Variant release` | 已签名且版本、SHA、release JSON 一致 |
