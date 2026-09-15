# HashMM App V279

日期：2026-07-18

- Chat 接入服务端 `waiting_input` / `resolved` 权威消息状态；澄清不再只依赖页面内存标记。
- 需要补充信息时显示“任务已安全暂停”卡片和最多三个真实回复选项；点击后通过原会话 Chat 发送，不另建演示任务。
- 修复 `waiting_input` 被误判为 streaming、导致永久显示生成动画和发送入口不可用的问题。
- 动态页读取 Supabase `client_activity.status`，区分“正在生成”和“等待你的输入”；待回复任务可直接回到同一会话接管。
- 服务器、桌面端与 App 复用同一 assistant message ID、conversation ID 和 suggestions，支持换端继续。
- 版本：versionCode 95，versionName 1.10.54。
