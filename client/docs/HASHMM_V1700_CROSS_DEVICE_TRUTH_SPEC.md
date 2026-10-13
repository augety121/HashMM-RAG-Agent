# HashMM V1700 / Cross-Device Truth & Navigation Kernel

## 规范性身份顺序

客户端必须依次完成：本地账号缓存恢复、Supabase 会话刷新、后端地址发现、`/api/livez` 传输探测、鉴权 `/api/client/bootstrap` 身份核验、对话游标同步。匿名健康接口、电子邮箱和远程 host 数量都不能替代用户 UUID 与项目归属证明。

`/api/client/bootstrap` 不返回密钥或完整 UUID，只返回 `user_sub_fingerprint`、`supabase_project_ref`、`canonical_origin`、同步协议和特性表。客户端必须拒绝项目或指纹不一致，不能回退到直接读取 Supabase 业务表。

## 失败语义

- Remote Config 失败不得更新成功 TTL。
- 401 只允许单飞刷新会话并重试一次。
- GET 可按协议重试；写操作在执行结果未知时不得自动重复。
- 网络、服务器或身份失败不得覆盖最后成功缓存为空数组。
- 只有成功的权威响应为空时才显示“暂无历史对话”。

## 导航归属

- 项目对话只在项目内出现。
- 最近只显示未归属项目的普通对话。
- 悬停前后行高、标题宽度、缩进与边界盒保持不变。
- App 历史按置顶与时间段分组，支持搜索并保留离线快照。

## 发行门禁

后端定向与全量测试、前端测试/类型检查/生产构建、Android JVM 测试与 Debug/Release 编译、Desktop Node 测试、release source gate 和 Native installer 三方摘要核验均为发布前置条件。
