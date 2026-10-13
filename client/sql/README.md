# HashMM Supabase SQL（在 Supabase → SQL Editor 运行，全部幂等）

按需运行：

1. **hashmm-supabase-sync.sql** —— 登录 + 用户数据同步表 + RLS（基础，先跑）。
2. **hashmm-realtime-activity.sql** —— 实时任务进度表 client_activity + 开启其 Realtime。
3. **hashmm-delta-sync.sql** —— 同步表加 updated_at + 触发器 + 索引，支撑增量同步。
4. **hashmm-realtime-sync.sql** —— chat_conversations / chat_messages 加入 Realtime 发布（App 实时联动）。
5. **hashmm-set-admin.sql** —— 把 admin@example.invalid 设为管理员（写 app_metadata.role）。改后该账号需重新登录。
6. **hashmm-grounding-ledger.sql** —— 已有项目补加逐主张证据账本列；全新项目已包含在基础 SQL 中。

后端需配环境变量 `HASHMM_SUPABASE_SERVICE_KEY`（service_role key，仅服务端）。
管理员也可用环境变量 `HASHMM_SUPABASE_ADMIN_EMAILS=邮箱1,邮箱2` 设置（与 SQL 二选一即可）。
