# HashMM V1800 / Remote Fabric 4.0

## 1. 目标与结论

V1800 解决的不是“列表少一个设备”这一处页面问题，而是跨端远程控制缺少稳定设备真值、连接租约和可诊断恢复链的问题。桌面端与 App 登录同一 Supabase 账号后，只有桌面端完成远程宿主注册且租约仍有效，App 才能看到并连接该电脑；健康接口成功、邮箱相同或旧 presence 行都不能替代这一证据。

本版本的生产事实链为：

`Supabase access token -> owner uid -> 30 秒单次 socket ticket -> WSS 注册 -> 稳定 device_id -> generation lease -> 10 秒心跳 -> owner-scoped device snapshot`

## 2. 不变量

1. **账号边界唯一**：所有设备目录、票据、会话和诊断均按已验签 `uid` 隔离，不以邮箱匹配，不允许客户端指定 owner。
2. **设备标识稳定**：桌面端使用安装用户数据根派生的稳定标识；Android 使用 App 自有持久安装标识，不读取原始硬件标识。
3. **在线必须可路由**：HTTP 诊断注册不得宣称 `remote_ready`；只有当前 SignalHub 中存在且租约有效的 host 才出现在可控设备列表。
4. **旧连接不能覆盖新连接**：同 owner、role、device 的新连接增加 generation；旧 generation 不能续租、下线或转发控制消息。
5. **凭据最小暴露**：账号 access token 只用于 HTTPS 换取短期单次票据；V2 隐藏宿主页和 WebSocket 首帧不再接收账号 token。数据库仅保存票据 SHA-256。
6. **失败不伪装为空列表**：鉴权失败、入口不可达、注册超时、租约被替换和权威空设备列表具有不同状态。
7. **配置不是验收证据**：配置 TURN、Cloudflare Tunnel 或 shared broker 不等于完成真实公网、对称 NAT、弱网或长稳测试。

## 3. 组件与职责

| 组件 | 权威职责 |
| --- | --- |
| Supabase Auth | 用户身份签发与刷新；不承担活跃远程连接路由 |
| HashMM HTTPS API | 验签、签发单次票据、owner-scoped 设备快照与自诊断 |
| SignalHub | 当前进程内 WebSocket 路由、稳定 ID 解析、旧连接 fencing |
| RemoteDeviceRegistry | SQLite 持久设备目录、35 秒租约、generation、票据哈希 |
| Desktop main process | 获取新票据、启动/监督隐藏 host、失败后 1 秒重建 |
| Desktop host | 注册、10 秒心跳、权限确认、WebRTC 信令与控制面 |
| Android App | 获取 viewer 票据、稳定安装身份、列表同步、有限重连和诊断 UI |

SQLite 是单一服务源的安全默认值。`HASHMM_REMOTE_SHARED_BROKER` 未配置时，诊断必须返回 `multi_instance_ready=false`。V1800 不把尚未实现并验收的 Redis/NATS 跨节点信令伪装成已完成能力。

## 4. API 与协议

### 4.1 HTTPS 控制面

- `POST /api/remote/v2/socket-ticket`
  - Bearer：Supabase/HashMM 有效身份。
  - body：`role`, `device_id`。
  - 返回：单次 `ticket`、`expires_in`、`hashmm.remote.v2`。
  - 每账号最多 64 个未过期票据；已用和过期票据及时清理。
- `GET /api/remote/v2/devices`
  - 返回稳定设备 ID、在线、可远控、忙碌、最后心跳、版本和安全指纹。
- `GET /api/remote/v2/diagnostics/self`
  - 只返回当前 owner 的非敏感诊断；不得返回 token、IP、SDP、ICE candidate 或 TURN 密码。
- `POST /api/remote/v2/devices/register` 与 `/heartbeat`
  - 用于非 WebSocket 代理和诊断；HTTP 注册本身不产生可路由 host。
- `GET /api/remote/readiness`
  - V3 返回 fabric 诊断和既有 HTTPS、身份、TURN、真实公网及 soak 证据。

### 4.2 WebSocket

- 路径：`WSS /api/remote/ws`。
- 首帧在 10 秒内到达且不超过 16 KiB；后续单消息不超过 1 MiB。
- V2 首帧携带单次 ticket；仅当服务端明确返回 404（滚动升级旧端点不存在）时，客户端才允许 V1 token 兼容，401/403/5xx/超时不得静默降级。
- `authOk` 返回 `stableDeviceId`、`ownerFingerprint`、`deviceFingerprint`、`generation` 和租约 TTL。
- host/viewer 每 10 秒发送 `heartbeat`；服务端返回 `heartbeatAck`。`leaseRejected` 或 `deviceReplaced` 必须终止旧连接。
- `listDevices` 只返回同 owner、未过期且 `remote_ready` 的 host；`connect.target` 使用稳定 device ID。

## 5. 状态机与恢复

设备状态：`registered_offline -> online_not_remote -> remote_ready -> busy -> offline/revoked`。

桌面宿主：

1. Renderer 恢复登录后把当前 token 交给 main process。
2. main process 通过 HTTPS 换取单次票据并创建隐藏 host。
3. host 收到 `authOk` 才报告 registered。
4. 信令错误、连接关闭、租约拒绝或设备替换时，main process 1 秒后获取新票据并重建；15 秒 Renderer watchdog 是第二层兜底，4 分钟 token 刷新不再兼任宿主健康检查。

Android viewer：

1. 使用 App 安装标识申请 viewer ticket。
2. 12 秒内未完成注册显示明确超时，不无限转圈。
3. 非身份类断线按 1/2/4/8/15 秒最多 5 次恢复；401/403 直接显示重新登录或无权限，不制造重试风暴。
4. `authOk` 后显示账号安全指纹、协议版本和“设备列表已同步”；权威空列表才显示无在线设备。

## 6. 并发与安全

- 多账号可在同一服务进程并行注册和发现，SignalHub 房间按 uid 分区。
- 同一电脑同一时刻只允许一个交互控制授权，避免输入竞争；轻量 Agent/检索任务仍遵循独立有界并发池。
- 所有 object ID 和远程会话继续执行 owner 检查；missing/unauthorized 不提供可枚举差异。
- 日志只写 owner/device 哈希、角色、connection ID、协议与错误类型；不写 JWT、ticket、SDP、候选地址或输入正文。
- WebSocket 必须经过 HTTPS/WSS；正式公网仍应部署私有 TURN 临时凭据、速率限制、审计保留和告警。

## 7. SLO 与验收门

代码级门槛：

- 单次票据不可重放；owner 隔离、稳定 ID、租约过期、generation fencing 均有回归测试。
- Desktop source gate、Node 远程安全测试、Frontend test/typecheck/build、Android unit/lint/build、Backend targeted/full tests通过。
- Windows 安装包的 EXE、`.sha256`、`.release.json` 三处 SHA-256 一致；Android APK 元数据与 V1800 一致。

环境级门槛（必须在真实部署后取得证据，不能由单元测试替代）：

- 同账号桌面 + App 在不同运营商网络发现并连接。
- 至少一个对称 NAT 场景确实选中 TURN relay，且两端审计属于同一 session。
- 24 小时持续在线、网络切换、App 前后台、桌面睡眠/唤醒和 token 刷新恢复。
- 若启用多实例：必须部署真实 Redis/NATS 路由并执行跨实例 host/viewer 验收；仅设置环境变量不算完成。

## 8. 发布组合

- Backend/SDK：`V1800 / 1.8.0`，服务协议版本 `18.0.0`。
- Desktop/WebUI/MCP/Native Installer：`9.0.0`。
- Android：`6.0.0 (versionCode 180)`。
- 远程设备协议：`hashmm.remote.v2`；readiness：`hashmm.remote.readiness.v3`。

