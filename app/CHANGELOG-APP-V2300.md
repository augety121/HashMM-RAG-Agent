# HashMM App V2300 / 9.1.0

- 远程连接升级为 `hashmm.remote.v3`：先从登录后的 HashMM API 读取 `hashmm.remote-bootstrap.v3`，严格校验 HTTPS/WSS、主机、端口和路径，再申请一次性 viewer ticket。
- 每次 WSS 携带 `attempt_id + trace_id`，认证失败显示稳定错误码；不再把设置页 URL 自行替换成旧 `/api/remote/ws`。
- 设备目录只把服务端 `role=host` 且 `remote_ready=true` 的记录当作可控电脑；Android viewer 在线不再被误认为另一台电脑。
- 媒体仍为 WebRTC direct-first，TURN 和应急 HTTPS 语义保持分离。
- 版本号：`9.1.0 (230)`。
- `testDebugUnitTest`、debug/release Kotlin 编译、R8、resource shrinking 与 `lintVitalRelease` 通过；交付 ZIP SHA-256 为 `3d08327e7d4a73f2072c3081ff624d547dca03f758b3f534eec1ff2c66011b7f`。
