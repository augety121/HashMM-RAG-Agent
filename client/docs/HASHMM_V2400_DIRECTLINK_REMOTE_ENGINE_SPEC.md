# HashMM V2400 / DirectLink Remote Engine 5.0

## 目标与成功定义

本版本只把真实事实称为成功：同一账号的 viewer 已通过审批，媒体路径已建立，并且 viewer 实际渲染出第一帧。设备在线、WSS 注册、ICE connected、DTLS connected 或收到 VideoTrack 都只是中间状态。

默认路径为：

1. HashMM HTTPS/WSS：身份、设备目录、审批、一次性票据和信令。
2. WebRTC ICE direct：局域网或 NAT 穿透成功时，媒体不经过 HashMM API。
3. 自有 coturn：直连失败时由 ICE 选择 UDP、TCP 或 TLS relay candidate；仍不经过 HashMM API 进程。
4. compatibility preview：前两条路径未能在预算内产生首帧时，使用短时会话票据拉取低帧率 JPEG，只用于可用性与诊断。

## V4 协议

- `GET /api/remote/v4/bootstrap`
- `GET /api/remote/v4/preflight`
- `POST /api/remote/v4/socket-ticket`
- `GET /api/remote/v4/devices`
- `GET /api/remote/v4/diagnostics/self`
- `WS /api/remote/v4/ws`

V4 客户端必须声明 `hashmm.remote.v4`。V3 路由保留一轮用于兼容，但 V4 服务端只向 host/viewer 都为 V4 的会话下发新策略和时间预算。

时间预算：offer/answer 2 秒、路径选择 5 秒、直接首帧 8 秒、迁移 6 秒、兼容预览首帧 8 秒。超过预算必须进入下一路径或稳定终态，不允许只有 spinner。

## 里程碑与安全

服务端只接受 allowlist 里程碑与错误码。审计可以记录角色、阶段、候选类型、传输协议、候选数量、RTT、分辨率和耗时；禁止记录 token、ticket、SDP、候选 IP、屏幕内容与剪贴板内容。每个 session ID 必须同时校验 owner、role 与 device ID。

关键里程碑包括：permission approved、offer/answer created/forwarded/received、candidates gathered、candidate pair selected、DTLS connected、track received、first frame rendered、fallback started/first frame 和 terminal error。

## 客户端状态机

`CONNECTING → PICK_DEVICE → NEGOTIATING → WAITING_FIRST_FRAME → STREAMING`。

- `onAddTrack` 只能进入 `WAITING_FIRST_FRAME`。
- Android 由 `VideoSink.onFrame`、桌面 Chromium 由 `requestVideoFrameCallback`/`loadeddata` 确认首帧。
- 兼容预览首张 JPEG 解码成功后也可进入 `STREAMING`，但 UI 与诊断必须标明 `compat-preview`。
- 401/403、票据缺失、8 秒无帧、ICE 穷尽等必须展示可执行错误。

## 控制与性能

- `hashmm-control-v1`：有序可靠，承载点击、键盘与需要确认的控制。
- `hashmm-pointer-v1`：无序、零重传，承载 mouse move 与 scroll，避免旧事件阻塞新位置。
- `hashmm-file-v1`：有序可靠，继续受 session scope 与序列校验保护。
- ICE 并行收集 host/srflx/relay candidate，选择路径后持续读取 RTCStats；不把“配置了 TURN”当成“TURN 可用”，必须看到 relay candidate 与选中候选对才算证据。

## 部署与验收

Cloudflare Tunnel 只承载 HTTPS/WSS 控制面，回源保持 `127.0.0.1:6006`。coturn 必须是独立公网入口，开放 3478 UDP/TCP、5349 TCP/TLS 与配置的 UDP relay 端口段；`HASHMM_TURN_SHARED_SECRET` 只存在服务端与 coturn 配置中。

发布前至少验证：

- 同 LAN 直连首帧与输入；
- 手机网络↔宽带直连；
- 强制 TURN UDP；
- 屏蔽 UDP 后 TURN TCP/TLS；
- 直连失败后的兼容预览首帧与 8 秒终态；
- 网络切换、休眠恢复、票据过期与审批撤销；
- 两个账号互不可见、同账号多设备并发不互相挤占；
- 24 小时稳定性、TURN 带宽与费用。

未完成这些真实网络测试前，只能声明“实现与自动验证完成”，不能声明商业级公网远程 SLA。
