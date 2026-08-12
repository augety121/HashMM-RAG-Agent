# HashMM App V498 联动说明

- App 版本升级为 `1.19.0`（`versionCode 136`），对应 Backend V498。
- Chat 请求携带受限的工作方式；手机端继续定位为查看、确认和接力入口，不声明桌面本机执行权限。
- Work Canvas 消费服务端 `hashmm.operating-contract.v1` 投影，展示实际工作方式、路由原因、依据要求和操作前确认状态。
- 工作方式与运行状态仍由服务端能力事实决定；离线缓存只用于恢复显示，不能替代新的执行回执。
- 本轮不改变数据库 schema，也不声明真实公网对称 NAT、自有 TURN、24 小时稳定性或商店正式签名已经验收。
- 验证：`testDebugUnitTest`、`assembleDebug`、`assembleRelease` 均通过。
- 交付：debug APK 使用 Android 调试证书，可用于安装验收；release APK 已通过 R8、资源收缩和 lintVital，但因未提供正式 keystore 而保持未签名，不得冒充商店发行包。
