# V374 持久化工作控制协议

## 目标

V373 已经让 Chat、Loop、Team、Browser Use、Computer Use 和跨设备任务进入同一运行账本。V374 解决下一层问题：用户看到状态后，能否在不重复副作用、不越权、不伪造“已停止”的前提下真正控制任务。

系统坚持四条不变量：

1. 控制能力由服务端运行种类与状态派生，客户端不能自行发明按钮。
2. 每个命令绑定账号、运行 ID、命令 ID 和期望 revision；越权对象不可枚举，陈旧状态不能写入。
3. 命令在副作用之前持久化；同一命令 ID 最多本地分派一次。
4. 文件、Shell、网页、外部数据库和模型调用不是可事务回滚资源。没有运行证据时，系统不能声称已撤销或已恢复。

## 协议

运行响应携带：

```json
{
  "control": {
    "schema": "hashmm.work-control.v1",
    "expected_revision": 7,
    "available_actions": ["pause", "cancel"],
    "side_effect_boundary": "checkpointed_cooperative"
  }
}
```

客户端向 `POST /api/work-runs/{run_id}/commands` 提交 `command_id`、`action` 和 `expected_revision`。命令记录先进入 `executing`，完成后只允许首次收敛为 `applied` 或 `failed`。若进程在外部动作后异常退出，超过确认窗口的 `executing` 在读取时投影为 `uncertain`；系统不自动重放。

这提供的是“恰好一次接纳、至多一次本地分派”，不是对外部 API 的“恰好一次执行”承诺。外部系统是否接收请求，仍需其自身幂等键或后续事实核对。

## 执行器语义

| 执行器 | 可用动作 | 真实边界 |
|---|---|---|
| Loop | 运行时暂停/停止；阻塞时恢复 | 保存现有循环状态，协作式暂停；恢复沿用原范围与检查点 |
| Team | 运行时停止；终态重试 | 当前模型调用无法硬杀，返回后丢弃输出；重试创建新运行并链接旧运行 |
| Browser/Computer/取文件 | 排队时取消 | `file_requests.status='pending'` 条件更新；桌面端领取后拒绝取消 |
| Chat/Artifact/普通 Workflow | 暂无控制 | 只读，不展示虚假控制按钮 |

团队进入 `stop_requested` 后，运行快照立即关闭再次停止按钮。Browser/Computer 取消与桌面领取竞争由同一条 SQL 状态条件裁决，不用客户端时间推断。

## 与参考产品的关系

- Codex 的任务/目标机制把暂停、恢复、继续追加要求和权限边界分开；app-server 协议把 resume、interrupt 和状态变化做成显式事件。HashMM 借鉴“状态与控制是协议，不是聊天文案”的原则。
- Claude Code 的会话恢复与 checkpoint 明确区分文件编辑检查点和 Bash、外部数据库、外部 API 等副作用；后台 agent 遇到需要新授权的操作不会自行放行。HashMM 因此没有实现一个名不副实的“全局回滚”按钮。
- HashMM 的实现基于自身 Python、SQLite、Electron 与 Android 架构，不声称复制或兼容 Codex/Claude Code 的未公开内部实现。

官方参考：

- <https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md>
- <https://code.claude.com/docs/en/checkpointing>
- <https://code.claude.com/docs/en/sessions>
- <https://code.claude.com/docs/en/sub-agents>
