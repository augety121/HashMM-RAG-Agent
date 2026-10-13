# HashMM V900 管理后台、设置与插件大版本规格

状态：Implemented contract  
版本：Backend `V900 / 0.9.0`，Desktop/WebUI/MCP/Native `2.3.0`  
日期：2026-08-09

## 1. 目标与非目标

V900 把“管理后台、设置、插件中心”从松散页面收敛为三条可验证链路：账号设置由服务端持久化并发出回执；管理总览明确每个数据源是否可验证；插件从发现、导入、审核、信任、加载、撤销到升级均有显式状态。

本版本不把 UI 中出现一个开关视为能力已经生效，不把数据源异常伪装成 0，不把上传 ZIP 视为可信代码，也不保存或展示模型隐藏思维链。ProjectVault 仍位于用户选择的 HashMM 安装目录下 `HashMM Data`，本版本不把用户数据硬编码到 C 盘。

## 2. 强制不变量

1. `profile/settings/plugin/admin` 对象必须经过登录身份或管理员身份检查。
2. 个人资料写入只能修改当前账号，不允许借用 `/api/admin/users/{id}`。
3. 账号设置写入必须携带 revision；冲突返回 409，批量写入必须同事务回滚。
4. 设置成功必须来自服务端回执，本地缓存只能是兼容副本，不能反向冒充权威状态。
5. 密码旋转成功后必须接收新的 access/refresh token；失败不得改写本地成功状态。
6. 管理总览的失败数据源返回 `null + issue`，不得返回有欺骗性的 0 或空列表。
7. 运行时密钥不得以明文落入 `app_settings`，列表不得返回明文或密文。
8. 插件发现和导入不得 import Python；只有管理员对精确 package SHA-256 建立信任后才允许加载。
9. 插件字节变化、升级或撤销必须立即移除活动工具并使旧信任失效。
10. 未知工具和未声明副作用的插件工具不得按只读处理。

## 3. 设置控制面

### 3.1 作用域

| 作用域 | 权威位置 | 示例 |
|---|---|---|
| user | `user_settings_v2`，owner-scoped | 回答风格、自定义指令、默认审批偏好 |
| device | Electron/浏览器设备存储 | 主题、窗口、设备能力可用性 |
| admin/runtime | 管理员 API + `app_settings` | 搜索提供商、渠道、Supabase 公共配置 |
| secret | 加密存储 | Serper/Bing/Tavily、渠道 secret |

账号设置读取返回 `effective_value/source/editable/managed_by/revision/restart_required`。V900 的可写注册表仅包括已经真正串入服务端的字段；未接线的界面控件不得被伪装为账号级持久化。

### 3.2 API

- `GET /api/me/profile`：读取当前身份的公开资料。
- `PATCH /api/me/profile`：只允许 `display_name`；本地账号 owner-check，Supabase 账号使用调用者 access token 和 RLS，不引入 service-role。
- `GET /api/me/settings`：返回 `hashmm.user-settings.v2` 有效设置。
- `PATCH /api/me/settings`：1–32 个变更，字段白名单、类型校验、revision 检查、单事务提交，返回审计 receipt。

### 3.3 设置页验收

- 个人资料不调用管理员路由。
- 密码规则前后端一致，旋转后的令牌被当前设备接收。
- 个性化和审批偏好跨设备读取服务端值。
- 归档/恢复逐项确认，失败项保持或恢复原状态。
- 搜索按控件语义匹配；每页显示账号/设备作用域。

## 4. 管理后台控制面

管理员默认进入“平台总览”。`GET /api/admin/overview` 使用 `hashmm.admin-overview.v2`，为 models、users、knowledge bases、evolution skills、services、audit、jobs 分别记录状态和延迟。

前端展示三类事实：数据值、采样时间、数据源状态。`unavailable` 只表示当前无法验证，不能推断为零。普通用户菜单不暴露管理后台入口；服务端仍是最终权限边界。

V900 保留现有 admin/user 两级角色，不虚构已经完成组织级 RBAC、审批人分离或多租户账单控制面；这些属于后续独立版本。

## 5. 插件供应链

### 5.1 生命周期

`ZIP received → staged → manifest validated → digest inventoried → review_required → trusted exact digest → loaded → active`

任何摘要变化、撤销或升级都会回到 `review_required`。升级必须显式 `replace=true`，旧包移动到 ProjectVault 数据根下的 `plugin-quarantine`，不会静默覆盖。

### 5.2 导入边界

`POST /api/plugins/install` 仅管理员可用，接收原始 ZIP 和可选 `X-Plugin-Sha256`。服务器限制 8 MiB 压缩包、4 MiB 解压总量、100 个文件、单文件 1 MiB，并拒绝：绝对路径、`..`、反斜杠、符号链接、根目录外文件、多 manifest、重复路径、大小写碰撞、Windows ADS/尾随点空格和文件目录冲突。

成功响应必须包含 archive/package 摘要、文件数、`trust_required=true`、`execution_started=false`。导入动作写入管理员审计。

### 5.3 审核与诊断

`GET /api/plugins/{name}/diagnostics` 返回文件清单、package SHA-256、权限声明、工具副作用注解、信任收据、活动状态和执行边界。信任、加载、撤销均单独授权和审计；加载失败返回非 2xx，不能伪造成功。

Python 插件通过审核后仍在 HashMM 后端进程内执行，这是明确限制，不是容器或 isolate。需要强隔离的第三方能力应使用 MCP 或未来的隔离运行时。

## 6. 数据与兼容

- `user_settings_v2` 为加法建表，不覆盖旧用户数据。
- 历史运行时明文 secret 在首次认证读取时加密回写；读取失败时保持 fail-closed 日志，不向列表泄漏。
- 旧插件信任收据仍按精确摘要读取；升级必撤销旧收据。
- 旧 `/system/plugins/*` 兼容路由保留给已有客户端；新导入和诊断只在规范 `/api/plugins/*` 暴露，避免扩大兼容面。
- 本版本不改变 `requirements.txt` 或已安装 pip 集合。

## 7. 验收矩阵

| 风险 | 必须验证 |
|---|---|
| 跨账号设置 | owner A 写入不影响 owner B |
| 并发覆盖 | stale revision 返回冲突；批量前项回滚 |
| 密钥泄漏 | 数据库不是明文；列表只显示掩码 |
| 插件自动执行 | 导入后 executor 为空、active=false |
| ZIP 越界 | traversal、symlink、重复/碰撞、ADS 全部拒绝 |
| 插件升级 | 旧工具卸载、旧信任失效、旧字节隔离 |
| 管理假数据 | 单源异常显示 null/unavailable |
| 前端契约 | 单测、typecheck、production build 全通过 |

## 8. 明确剩余限制

- 尚未实现组织/项目级细粒度 RBAC 与双人审批。
- 尚未实现签名插件市场、发布者证书、恶意代码静态分析或进程级沙箱。
- Supabase 资料更新依赖部署方存在受 RLS 保护的 `profiles` 表；不可用时返回失败，不落本地假状态。
- 部分设备级外观和桌面能力仍由设备存储管理，不承诺跨设备同步。
- SHA-256 证明字节完整性，不证明发布者身份或代码安全性。
