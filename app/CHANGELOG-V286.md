# HashMM App V286

日期：2026-07-19

- App 与桌面端统一任务公开类型：浏览器使用 `browser_use`，电脑操作使用 `computer_use`；服务端 V357 兼容旧 runner 前缀。
- Chat 输入区新增真实多智能体入口。入口携带当前 `conv_id` 和尚未发送的目标文本，智能体工坊在原会话中预览分工、启动团队并回传结果。
- 智能体工坊只有从工作台独立进入时才新建结果会话；从 Chat 进入时不再制造割裂会话。
- Browser Use、Computer Use、画布与多智能体继续复用同一后端、Supabase 登录身份和会话持久层，不新增本地 Demo 状态。
- 版本升级为 versionCode 102、versionName 1.10.61；联动 backend V357。

## 验证

- Android 单元测试：130 passed，0 failed。
- `compileDebugKotlin`、`assembleDebug` 通过。
- APK：`HashMM-App-1.10.61-debug.apk`，70,301,259 bytes。
- SHA-256：`9a1cb95b89f067c9a61413c93137fb5356523c374c6a5b0eb42908715424c6a6`。
- Debug APK 未使用正式发布签名，仅用于本轮安装验证。
