# HashMM App V285

日期：2026-07-19

- 修复“团队管理无法读取成员”：兼容本地数字注册时间与 Supabase ISO 时间，单条异常成员不再导致整页失败。
- 团队目录改为 HashMM 后端与 Supabase 管理员 RPC 并行读取；任一路成功都会显示真实成员，两路结果按账号 ID 合并。
- 使用当前登录会话调用 `list_all_profiles`，管理员权限继续由 Supabase RPC 校验；App 只使用 publishable key，不包含 service-role。
- 失败状态区分会话失效、管理员角色未同步、RPC 未部署、网络失败、空响应和格式不兼容。
- 只读取到部分来源时显示同步提示与重试入口，不再把部分结果伪装成完整目录。
- 版本升级为 versionCode 101、versionName 1.10.60；联动 backend V352。

## 验证

- Android 单元测试：130 passed，0 failed。
- `assembleDebug` 通过；APK metadata 为 versionCode 101 / versionName 1.10.60。
- APK：`HashMM-App-1.10.60-debug.apk`，70,284,875 bytes。
- SHA-256：`b18b7899398e7fa3743e87efde80d4ae664cb9070ca74a2d5d494be9046d43b0`。
