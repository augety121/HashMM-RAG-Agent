# HashMM App V2800

- 版本升级为 `12.0.0`（versionCode `280`），与 Backend V2800、remote.v4 兼容矩阵一致。
- 远程设备选择与媒体协商分离；只有服务端签发当前 viewer ticket 且包含 `view` scope 后才启动 WebRTC/兼容预览计时。
- 控制面 401 单次刷新 Supabase 会话并重建连接，避免使用过期令牌循环重试。
- “我的 API 与模型”增加授权 Sub2API/自有反代入口；仍需用户测试并显式设为当前模型。
- 全量 `testDebugUnitTest`、`assembleDebug`、`assembleRelease` 已通过；Release 构建包含 R8、资源收缩和 lintVital 验证。
- V2800 App 验收包 SHA-256 为 `45da7d39be550904a8f71a6914ab460ecb2a1a85b9d22e5644a46048305b30e0`。
- 本地合同测试不代替真实异地双设备、TURN 与商店签名验收；当前 Release APK 未配置正式发布证书。
