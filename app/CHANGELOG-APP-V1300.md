# HashMM App 2.0.0 / V1300

日期：2026-08-09  
版本：`2.0.0 (140)`

## 本轮变化

- “我的”和“对话”的可见 Compose 界面、入口和布局保持不变；改造仅发生在身份投影、同步网关、缓存对账与 ViewModel 内部。
- 会话和消息规范读取优先走 HashMM owner-checked API，并把稳定分页拉取到耗尽后才更新本地；Supabase Realtime 只作为刷新信号，直接表读取降为兼容回退。
- 删除记录使用 `(deleted_at, conversation_id)` 复合游标分页；切换账号和登出时清除旧账号个人资料投影，缓存继续按 owner 加密隔离。
- “工作”能区分同账号桌面在线、Cloudflare Computer 云环境可用、等待桌面和离线；App 不保存 Cloudflare Gateway Secret。
- “今天”继续作为统一 WorkRuntime 行动收件箱，复用 expected revision、服务端事实和 owner-scoped 加密 outbox，不创建影子任务。

## 验证边界

- JVM 全量单测和 Debug 构建是源码/构建证据，不替代 Android 真机弱网、杀进程、切账号、推送和跨端并发验收。
- Cloudflare provider 只有在 HashMM 后端已配置且真实健康检查通过时才显示可用；未部署时不会伪装在线。
