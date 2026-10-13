# HashMM App V2200 / 9.0.0

- 远程设备仍通过 HashMM 账号控制面发现、申请权限和交换 WebRTC 信令。
- 默认媒体路线为端到端 WebRTC，ICE 自动选择直连或 TURN。
- “安全中转”只表示 TURN-only，不再同时开启旧服务器 JPEG 轮询。
- WebRTC/TURN 在服务端给出的 12 秒窗口内均未建立时，才使用批准会话的短时 ticket 拉取应急 HTTPS 帧；WebRTC 恢复后立即停止。
- 新增 Direct-First 远程合约测试；版本号为 `9.0.0 (220)`。
