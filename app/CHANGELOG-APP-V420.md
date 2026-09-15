# HashMM App V420

- 远程查看端从实际 WebRTC `RTCStats` 读取选中的本地 candidate、协议、RTT、丢包和收发字节，每分钟向统一 HashMM 远程会话上报一次有界摘要。
- 上报不包含 candidate 地址、SDP、账号 token、剪贴板、文件或输入正文；后端仍按 owner、session、role 和稳定 device ID 校验。
- 这些摘要进入持久化远程审计，供真实公网 relay 验收和 24 小时稳定性判定使用；仅后端健康不再能冒充远程链路稳定。
- App 版本升级为 1.14.0（versionCode 130），对应 Backend V420。
- 验证：30 个测试套件、163 项测试通过；Debug Kotlin 编译通过。

