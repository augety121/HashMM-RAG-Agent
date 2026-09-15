# HashMM V1500 / Multi-User Runtime & Secure Local Replica 4.0

## 1. 发布目标

V1500 将桌面端、Android App 与 HashMM 服务端收敛到同一套生产身份、并发调度、跨端同步和本地副本契约。核心目标是：同一 Supabase 项目中的账号可以独立并发使用；同一账号的桌面端与 App 能看到一致的服务端事实；网络抖动时客户端可恢复而不会重复制造副作用；本地副本按账号隔离并加密保存正文。

本版本不把模型输出当作执行证据，不把“请求已发出”当作任务完成，也不通过扩大权限解决并发或同步问题。

## 2. 生产身份契约

- 生产环境的身份配置以进程环境为唯一权威，历史数据库覆盖项不得改变 Supabase 项目。
- `GET /api/auth/identity-contract` 只返回公开身份元数据、配置修订和后端版本，不返回 publishable key、service-role key、JWT 或其他凭证。
- 桌面端登录前校验后端身份契约和 JWT issuer；Supabase 项目不一致时明确报告 `project_mismatch`，不再泛化为“服务器未正确接入”。
- 启动诊断验证 public URL、issuer/project ref、service-role key 所属项目与安全远程设置，但日志永不输出密钥值。
- 所有 owner-scoped 对象继续使用服务端鉴权结果做归属校验；客户端账号名或本地缓存不能替代授权。

## 3. 多用户资源调度

运行资源分为两类：

1. 普通 Agent/RAG/API 任务走全局并发池和每账号公平配额，默认全局 12、单账号 3。
2. Browser Use、Computer Use 和顺序浏览器动作仅在实际占用物理桌面时持有设备互斥锁。

普通文件、自动任务和服务端 AgentLoop 不再被一个进程级浏览器锁串行化。任务准入同时检查全局与 owner 配额；超额请求返回有界错误，由调用方退避或稍后重试。桌面 dispatch 的健康检查、轮询、心跳和完成回执均有独立 deadline，避免单个失联请求永久占用 runner。

## 4. 跨端同步与失败语义

- 服务端会话、WorkRuntime、待处理动作和项目归属仍是权威事实；本地副本只用于首屏、弱网和恢复。
- 桌面端前台同步基线为 30 秒并加入随机抖动；后台基线为 5 分钟；重新联网和回到前台立即对账。
- Android 远程配置采用登录边沿强制刷新、6 小时 TTL 和随机抖动；失败时保留 last-known-good，不每分钟持续请求 Supabase。
- GET 请求只对网络错误及 429/502/503/504 做最多一次重试；写请求默认不自动重放，副作用请求依赖既有幂等键和回执对账。
- 超时表示结果未知，不等价于失败；客户端必须通过同一 request/idempotency id 查询终态。

V1500 提供自适应轮询和恢复触发的可靠基线，不宣称已经完成全链路 Realtime Broadcast。后续可在不改变服务端事实模型的前提下以 Realtime 作为低延迟唤醒通道。

## 5. 安全本地副本

### Android

- 使用 Android Keystore 中的 AES-256-GCM 密钥。
- 数据写入 `noBackupFilesDir/hashmm-cache/<account>`，按账号隔离并从系统云备份排除。
- 每个文件使用账号与逻辑文件名作为 AAD；临时文件写入、`fsync` 后原子替换。
- 按文件加锁，避免不同账号或不同数据集互相阻塞。
- 旧 EncryptedFile 缓存只做一次兼容读取，成功迁移后删除旧副本。

### Desktop

- refresh token 和完整会话正文通过 Electron `safeStorage` 落盘。
- 正文副本存放在按账号 SHA-256 命名的独立缓存文件中；切换账号不会清空其他账号，也不会跨账号读取。
- Renderer 的 localStorage 只保留侧栏所需元数据，缓存正文被剥离；缓存格式、条目数和单项大小均有上限。

本版本不宣称所有 Renderer 元数据都已加密；短期 access token 和非敏感侧栏元数据仍属于后续最小化范围。

## 6. 安装与版本契约

- Backend/SDK：`V1500 / 1.5.0`。
- Desktop、WebUI、MCP、原生安装器：`6.0.0`。
- Android：`3.0.0 (150)`。
- 原生安装器的 `FileVersion`、`ProductVersion`、UI 显示版本和发布清单必须一致。
- 安装器最小化/关闭按钮的可点击区域为 44×40，并具备可访问名称；首次安装必须显式接受协议，不能预勾选。
- 发布 EXE 只有在实算 SHA-256 同时匹配 `.sha256` 与 `.release.json` 后才可交付。

## 7. 验收门

- 后端全量 pytest、前端测试/typecheck/production build、桌面 Node 契约测试、release source gate、Android 单测/Debug/Release/lintVital 必须通过。
- 生产启动 doctor 必须通过，且不得打印秘密值。
- 服务器包不得包含 `.env`、数据库、用户数据、模型、索引、日志、密钥或旧归档。
- Android release APK 在没有发布者 keystore 时必须明确标记 unsigned；Windows 在没有证书时必须明确标记未做 Authenticode。
- 真实公网双账号、校园网/5G、断网恢复与 24 小时稳定性属于部署环境验收，不得用本地测试替代。

