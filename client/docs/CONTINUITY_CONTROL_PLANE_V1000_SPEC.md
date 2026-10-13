# HashMM V1000：会话连续性与控制平面实施规格

状态：已按本仓库实现核验更新；本文只描述可由代码和测试证明的能力。  
版本：Backend/SDK `V1000 / 1.0.0`，Desktop/WebUI/MCP/Native `3.0.0`  
日期：2026-08-09

## 1. 版本目标

V1000 不再按页面逐个补按钮，而是统一四类核心对象的归属和生命周期：

1. Chat：一个活动 Chat 只能归属于一个项目或“未归属”，不能同时出现在项目和“最近”。
2. Project：项目是 Chat、工作区引用和长期任务上下文的唯一容器；安装目录不是项目工作区。
3. WorkRun：任务状态由服务端事件推进，UI 只投影，不通过文案推断完成。
4. Handoff：跨 Chat 接力传递公开任务状态和可验证引用，不传递隐藏推理或权限。

本版本的产品 highlight 是“可验证连续性”：历史、项目、任务、证据、接力和设置共用 owner、revision、receipt 和 stale 检查，而不是依赖模型记忆或前端缓存碰运气。

## 2. 会话唯一归属

### 2.1 权威字段

`conversations` 增加或统一使用：

- `project_id`：为空表示未归属；非空必须属于同一 owner。
- `archived`、`deleted_at`：归档与删除不是 UI 临时状态。
- `revision`：每次归属、活动或属性更新单调增加。
- `sync_state`：描述服务器已知同步状态，不作为内容真实性证明。
- `has_durable_content`、`last_activity_at`、`visibility_source`：由服务端列表投影返回。

### 2.2 列表不变量

- 项目视图：`owner_id = current_user AND project_id = selected_project`。
- 最近视图：`owner_id = current_user AND project_id IS NULL AND archived = 0 AND has_durable_content = true`。
- 空白本地草稿可暂存，但没有服务端持久内容前不伪装成历史记录。
- 项目 Chat 不重复出现在“最近”；归档 Chat 不进入活动列表。
- 所有对象查询先校验 owner；缺失与无权对象使用不可枚举的统一失败语义。

### 2.3 游标和搜索

会话列表使用绑定 `scope/project/query` 的不透明 keyset 游标，排序键为 pinned、更新时间和 ID。插入或翻页时不能因 offset 漂移产生重复或遗漏。搜索结果显式返回位置：项目或未归属。

## 3. 同步、离线和删除

- Supabase 拉取按页执行，桌面缓存最大 10,000 条，不再只取首屏。
- 新 schema 同步 `project_id/revision/sync_state`；旧 schema 通过字段回退保持兼容。
- 合并优先比较 revision 和更新时间，旧云端行不能把较新的项目 Chat 移回“最近”。
- 删除先写入 owner-scoped 单调墓碑，再执行本地硬删除；云端旧副本 revision 不超过墓碑时禁止复活。
- 归档、项目移动和删除均必须以服务端回执为准，本地失败时不能保留假成功状态。

## 4. 可验证 Chat Handoff

### 4.1 任务胶囊

`hashmm.chat-handoff.v1` 只包含公开且接力必要的字段：

- 当前目标、最近用户要求、计划、决策、阻塞项和下一动作；
- 证据引用与产物的 SHA-256；
- Project、Workspace、WorkRun 和 checkpoint 引用；
- 待审批项的引用，但明确 `permission_carried=false`；
- source/target conversation、owner、project、revision 和状态。

必须剔除隐藏思维链、模型私有 reasoning、原始工具参数、密钥、令牌和审批能力。历史 Chat 中的“我已经执行”只是待验证声明，不是工具证据。

### 4.2 状态与幂等

创建 handoff 必须提供 `Idempotency-Key`。目标 Chat 在同项目内确定性创建，重复请求不能创建多个目标。状态至少覆盖 sealed、claimed、acknowledged、rejected；动作只能由目标 Chat owner 执行。

### 4.3 新鲜度

接收端验证：

- source revision 是否变化；
- 产物是否缺失或 SHA-256 改变；
- WorkRun 状态和 checkpoint 是否仍为当前；
- 工作区与 Git 状态由桌面端在真实路径复核，服务端不能假装已验证。

任一检查失败都保留胶囊但标记 stale，UI 展示原因，不静默把旧上下文注入为事实。

## 5. Chat Mailbox

`hashmm.chat-mailbox.v1` 提供同账号、同项目或均未归属 Chat 之间的持久消息通道：

- owner 与作用域双重检查；
- 幂等发送；
- queued、delivered、read、acknowledged、rejected、expired 状态；
- 服务端脱敏 secret、private reasoning 和 raw args；
- 明确 `carries_authority=false`，不能借消息复用 shell、文件、网络或支付审批。

本轮完成后端 API 与前端 SDK。完整的收件人发现、统一收件箱和系统通知 UI 不在本次“已完成”范围内。

## 6. 统一任务状态机

规范状态包括 draft、queued、running、waiting_approval、blocked、paused、verifying、completed、completed_with_limits、failed、cancelled、interrupted。兼容状态映射在服务端完成。

每个 WorkRuntime 事件携带服务端派生的 `task_transition`：

`owner → project → conversation → turn → run → checkpoint → actor/reason/request → from/to → time`

`completed_with_limits` 用于产物已交付但存在明确限制的情况；它不能被 UI 渲染为无条件完成。没有确定性验证或有效回执时，模型自述不能推进完成门。

## 7. 设置与管理后台

### 7.1 设置契约

账号设置返回 `desired/effective/source/editable/managed/revision/restart_required/availability`。批量更新使用 revision 乐观并发，冲突返回 409 和当前事实。设备能力与账号偏好分开：Computer Use 的设备切换仍走窄 Electron bridge，不能仅改账号 JSON 冒充生效。

已接入通用、外观、通知、个性化、审批、浏览器确认、Computer Use、Worktree、API 访问和存储视图。Worktree 只能来源于活动 ProjectVault 项目的真实 Git 根；不得把安装目录或猜测路径显示为工作区。

`general.followup_mode=queue` 目前只定义了账号契约，尚未形成可跨重启的下一轮持久队列，因此 UI/发布说明不得宣称其为耐久队列。

### 7.2 管理治理域

现有功能页归并为 10 个治理域：控制总览、身份与组织、模型与供应商、Agent 运行时、工具与插件、知识与 RAG、工作与自动化、质量与进化、用量与成本、安全与系统。域用于信息架构，不能替代后端授权；每个叶子页仍调用真实 API。

总览数据源独立报告 `ok/unavailable` 和延迟。读取失败返回 `null`/不可用，不得把失败伪造成 0。普通用户不能进入管理员外壳。

## 8. 插件与 OCR 的版本边界

- 延续 V900 插件供应链：ZIP 路径与碰撞防护、隔离导入、摘要信任、显式升级、撤销和诊断；信任不等于进程级沙箱。
- 本轮未安装、删除或升级 pip 包。
- OCR 继续使用仓库既有持久 OCR 队列和运行环境已有 provider；V1000 不把“可选 provider 存在”误写成已安装 PaddleOCR/Unlimited-OCR。

## 9. 验收标准

1. 2,000 级会话 keyset 翻页无重复、无遗漏，项目与最近互斥。
2. 跨 owner 读取、移动、删除、handoff 和 mailbox 全部拒绝且不可枚举。
3. 删除后用较旧云端 revision 合并，Chat 不得复活。
4. handoff 重放不重复建目标 Chat；私有推理、raw args、secret 和权限不进入胶囊。
5. 产物或 source revision 变化时 handoff 明确 stale。
6. WorkRuntime 的每次状态变化保留完整 lineage；受限完成不冒充完整完成。
7. 前端测试、类型检查和生产构建通过；后端测试按隔离进程覆盖全部测试文件。
8. 服务器 ZIP 必须包含本文、逐文件 SHA-256 清单和升级验证；Windows EXE 只在原生流水线产出的 SHA-256 与 sidecar/release JSON 一致时接受。

## 10. 明确未完成项

- Chat mailbox 的完整收件箱/收件人发现 UI；
- `followup_mode=queue` 的跨进程、跨重启耐久执行队列；
- 服务器对桌面 Workspace/Git 状态的远程真实性证明；
- Authenticode 发布者签名（SHA-256 只证明完整性，不证明发布者身份）；
- Cloudflare Wallet/x402、真实支付和 Redis 多实例全局限流。

这些限制不降低本轮已实现链路的正确性，但不得在产品文案中被描述为已完成。
