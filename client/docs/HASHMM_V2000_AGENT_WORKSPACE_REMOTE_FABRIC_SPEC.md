# HashMM V2000 · Agent Workspace 6.0 / Remote Fabric 4.0

## 目标

V2000 只把可验证的能力显示为可用：一个账号身份、一个对象归属、一个远程设备真值、一个可恢复任务状态。模型文字、健康探针、HTTP 票据和配置名称都不能代替执行证据。

## 核心契约

- Principal：`uid → sub → legacy id` 只用于兼容解析，业务代码只消费 canonical principal id。
- Remote：`identity_ok → host_ticket_issued → host_socket_registered → viewer_registered → approved → rtc_connected`。前一阶段成功不推导后一阶段成功。
- Device：`online` 与 `remote_ready` 分离；缺字段 fail closed；generation 较旧的连接不得更新租约或路由输入。
- Agent role：catalog `agent_id` 选择角色提示，但 tools/network/filesystem/approval 仍由任务作用域独立授权。
- Capability：不可用、未配置或未选择的能力不应在 Composer 中伪装成可执行按钮。
- Evidence：远程诊断只记录阶段、时间、匿名指纹和 attempt id；不记录 access token、IP、SDP、ICE candidate 或屏幕内容。

## 发布门禁

- 身份形状回归、票据单次消费、host/viewer 分层、stale generation、角色 owner boundary 必须通过。
- Frontend test/typecheck/build、Desktop Node tests/source gate、Android unit/lint/build、Backend full pytest 必须形成真实终态。
- 公网远控、TURN、24 小时稳定性与多实例只有取得相应环境证据后才可标记完成。
