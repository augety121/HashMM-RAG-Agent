# HashMM App V284

日期：2026-07-19

- 工作台改为面向用户的“帮你完成”，按“让电脑帮忙、复杂工作、任务与进度、更多能力”组织，不再把底层模块名放在第一层。
- “管理后台”重构为“团队管理”：读取与桌面端一致的 Supabase 成员目录，展示成员、角色、邮箱、最近登录和云端同步状态。
- 修复团队管理只显示本地管理员的问题；完整目录通过 backend V351 安全代理当前管理员的 `list_all_profiles` RPC，App 不接触 service-role。
- “用量”重构为“使用概览”：管理员查看团队汇总，普通用户只看自己；展示完成次数、处理量、输入/生成、估算花费、按成员和按模型下钻。
- 网络、鉴权、旧服务、空响应和契约不兼容不会再被显示成 0，而是给出明确错误和重试入口。
- 安全记录把底层工具名转换成“使用浏览器完成任务、操作已连接的电脑、读取或整理文件”等用户语言。
- 版本升级为 versionCode 100、versionName 1.10.59；联动 backend V351。

## 验证与交付

- Android 单元测试：128 passed，0 failed。
- `assembleDebug` 通过；APK manifest 核验为 versionCode 100 / versionName 1.10.59。
- APK：`HashMM-App-1.10.59-debug.apk`，70,284,875 bytes。
- SHA-256：`de13ecffdb8efdb17e9328a7446f8f0e826b37bf8dcf0cd4019cd5622c0feb29`。
