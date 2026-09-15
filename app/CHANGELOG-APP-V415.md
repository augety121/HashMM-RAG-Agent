# HashMM App V415

- App 远程控制统一连接 HashMM 后端信令，不再把直接 Supabase 轮询作为产品运行路径。
- 同一账号只负责识别 owner；每次连接仍需桌面端批准具体权限。
- 增加稳定 Android 设备 ID、会话 ID、短时中继票据、票据刷新、控制序号和时间戳。
- 鼠标键盘优先走获批会话的 WebRTC DataChannel；电源与剪贴板继续走受服务端权限和审计约束的信令通道。
- MJPEG 回退只使用短时 Remote ticket，不使用账号 access token。
- ICE 支持服务端返回单 URL 或 URL 数组；未配置 TURN 时只使用 STUN，不内置公共 TURN 凭据。
- App 版本：1.13.0（129）。
