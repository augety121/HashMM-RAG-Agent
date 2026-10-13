# HashMM V1600 / Cross-Device Connection & Sync Kernel 6.0

## 正式数据边界

- HashMM 后端 SQLite/ProjectVault 是对话、消息、任务和文件的唯一权威源。
- Supabase Auth 是统一身份源；Supabase 对话表是受 RLS 保护的镜像与 Realtime 唤醒信号，不参与客户端读回退。
- App 和桌面端持有按账号隔离的本地副本；离线时展示副本，恢复后只与 HashMM API 对账。
- 对象访问继续执行 owner check；缺失与越权对象在需要防枚举时返回相同结果。

## 连接状态机

`online → suspect → offline-valid → online`。单次业务超时只进入 suspect；连续两次传输/网关失败才进入 offline-valid。401 属于身份状态，403 属于授权状态，二者都不能被翻译成服务器离线。`/api/livez` 只证明进程存活，`/api/readyz` 只读取快速服务状态，兼容入口 `/api/health` 保留。

## 同步规则

1. 客户端先显示本地副本。
2. App 通过 owner-scoped HashMM API 稳定分页获取规范快照和 tombstone。
3. Realtime 事件只触发 750ms 合并后的单飞同步，绝不直接改本地真值。
4. 普通 GET 不产生任何云端写入；显式 `cloud_sync=1` 仅用于一次性旧数据恢复。
5. 手机直连消息以 `client_message_id` 幂等写回；相同 id 与不同内容冲突返回 409，不覆盖其他消息。
6. App 退出登录采用本设备 LOCAL scope，不撤销桌面端或其他设备会话。

## 部署门禁

部署 V1600 前必须在目标 Supabase 项目运行 `sql/20260810_v1600_cross_device_kernel.sql`。服务器、桌面端和 App 的正式组合为 `V1600 / 7.0.0 / 4.0.0`；混用旧客户端只能获得兼容能力，不能获得本轮 single-flight、LOCAL sign-out 和幂等直连回写。

生产配置继续要求 `https://hashmm.hashlens.org`、`HASHMM_REQUIRE_AUTH=1`、`HASHMM_REQUIRE_SECURE_REMOTE=1`、loopback Tunnel origin 与明确 CORS 来源。service-role 密钥只允许存在服务器环境中。
