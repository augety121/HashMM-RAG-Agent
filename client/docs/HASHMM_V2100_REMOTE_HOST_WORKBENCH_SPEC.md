# HashMM V2100 · Remote Host Supervisor 与统一工作台实施规格

状态：已实现；真实公网远控仍需设备环境验收。

## 1. 远程主机所有权

- Electron 主进程是账号 host 信令的唯一所有者：申请 single-use ticket、建立 WSS、发送首帧鉴权、心跳、处理租约拒绝和有界重连。
- 屏幕采集渲染进程只持有媒体与 WebRTC 状态；它通过 allowlist IPC 收发信令，不读取账号 token，不申请 ticket。
- `device_id` 稳定，服务端查询必须 owner-scoped；presence 使用 lease/generation fencing，旧连接不能覆盖新连接。
- UI 必须分别呈现 `issuing_ticket`、`opening_socket`、`authenticating`、`registered`、`reconnecting`、`auth_expired`、`rejected`、`degraded`，不得把“拿到票据”显示为“可远控”。

## 2. 远程可观测性

- 服务端记录短 `socket_id`、失败阶段、耗时和不可逆 attempt 指纹。
- 日志禁止写入 access token、完整用户/设备标识、原始 IP、SDP、ICE candidate 或 TURN 密码。
- pre-auth disconnect 与 auth timeout 必须可区分；前端诊断只显示用户可行动的信息。

## 3. 插件执行边界

- 发现阶段只读取有界 `plugin.json`，不导入 Python。
- 管理员信任绑定完整包 SHA-256；每次工具调用前后重新计算摘要。
- Python 插件在短生命周期子进程中导入并执行；超时、异常退出、不可序列化结果均 fail closed，不能击穿后端进程。
- 该实现是 crash/lifetime isolation，不是 OS sandbox；文件系统与网络强隔离仍应使用 AppContainer、namespace/seccomp 或 MCP 外部服务。

## 4. 工作台信息架构

- 自动任务、智能体协作、能力与插件、设备接力使用统一 PanelKit 与 `workbench-vnext` 视觉规则。
- 交互优先顺序：对象列表 → 当前对象详情 → 主操作 → 诊断/管理细节。
- 禁止 hover 位移、彩色装饰底座和低于 11px 的关键元数据；键盘焦点必须可见。
- 全部 285 位角色可搜索和选择；单次执行团队仍保持受控规模，角色内容不自动授予工具权限。

## 5. 品牌与安装器

- SVG 是品牌单一源，生成应用磁贴、透明标记、字标、单色托盘和 16–1024 PNG。
- 原生安装器三态共用窗口约束；标题栏控件必须参与布局，不使用右上角绝对坐标。
- 高级安装位置默认收起；安装进度显示真实百分比；升级、修复和首次安装继续走事务复制与回滚。

## 6. 验收门禁

- Backend 全量 pytest；Frontend test/typecheck/build；Desktop 全量 Node；原生 Qt 编译和 source-only release gate。
- 最终 Windows 交付只接受 `HashMM-Setup.exe`、`.sha256`、`.release.json` 的 SHA-256 一致结果。
- 未完成 Authenticode、真实 TURN/NAT 与 24 小时 soak 时，发布说明必须明确限制。
