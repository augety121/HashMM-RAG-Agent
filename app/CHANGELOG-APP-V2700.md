# HashMM App V2700

- 版本升级为 `11.0.0`（versionCode `270`），与 Backend V2700、remote.v4 兼容矩阵一致。
- App 继续使用 `https://hashmm.hashlens.org` 作为生产后端，登录身份、设备发现和审批均由同一 Supabase 项目签发的会话进入 HashMM 控制面。
- 远程成功仍以审批后的真实首帧为准；服务端缺少 v4 路由时必须提示升级服务器，不能把 404 显示为“电脑离线”或无限转圈。
- 本轮 APK 的单测、lint、Debug 与 unsigned Release/R8 由实际构建验证；unsigned Release 不能冒充公开签名包。
