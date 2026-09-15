# HashMM App 6.0.0 / Backend V1800

- 远程 viewer 改用 `hashmm.remote.v2` 单次 socket ticket，账号 token 不再放入 V2 WebSocket 首帧。
- 设备身份改为 App 自有的持久安装 ID，不读取 `ANDROID_ID`。
- 新增 10 秒租约心跳、12 秒注册超时、最多 5 次有界恢复、设备替换/租约拒绝处理。
- 设备页显示账号设备通道、owner 安全指纹和列表同步事实；空列表不再与鉴权/网络失败混为一谈。
- 版本：6.0.0（versionCode 180），对应 Backend V1800 / Desktop 9.0.0。
