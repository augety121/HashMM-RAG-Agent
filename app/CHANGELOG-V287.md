# HashMM App V287

日期：2026-07-19

- 服务端 Chat 历史默认读取最新 500 条并保持时间正序，长会话不再因为旧的 100 条窗口而看不到最新回复。
- App 直连模型模式新增确定性长上下文压缩：保留初始目标、阶段要求、已报告进度和最近 10 条原文；完整 Room/Supabase 历史不删除。
- 直连压缩不额外调用模型，不产生隐形费用；压缩内容只作为上下文，不冒充已验证完成证据。
- 版本升级为 versionCode 103、versionName 1.10.62；联动 backend V358。

## 验证

- `testDebugUnitTest`、`compileDebugKotlin`、`assembleDebug` 通过。
- APK：`HashMM-App-1.10.62-debug.apk`，70,301,259 bytes。
- SHA-256：`e7cf91ec2ef7eaf60ca6e45872098876efc0ee6e67d76fa2e99384a02f9f18c5`。
- Debug APK 未使用正式发布签名，仅用于本轮安装验证。
