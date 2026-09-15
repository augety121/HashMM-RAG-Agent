# HashMM Public Agent API v1

HashMM 的公开 API 以 `WorkRuntime` 为唯一任务事实源。公开协议保存可审计的任务计划、事件、审批、检查点、产物与证据；不返回供应商隐藏推理，也不把模型文字当作执行证据。

## 启用与鉴权

- 默认关闭：`HASHMM_PUBLIC_API=1` 后才挂载可用能力。
- 推荐在“设置 → API 访问”创建 owner-scoped managed key；密钥原文只显示一次，服务端仅保存摘要。旧 `HASHMM_API_KEY` 环境变量继续兼容，但没有 Key 级策略与用量归属。
- 使用 `Authorization: Bearer <key>`、`X-API-Key` 或 `X-Goog-API-Key`。API key 不接受 URL 查询参数，避免进入访问日志。
- managed key 在执行前依次校验状态、过期、来源 IP、Scope、模型与项目 allowlist，再原子执行 RPM、并发租约与费用配额预占。撤销提交后新请求立即失败。
- `HASHMM_PUBLIC_API_OPEN=1` 仅适合可信的本机或隔离内网。
- 所有写操作必须发送 `Idempotency-Key`。同一 owner、路由和 key 的相同请求返回首次结果；不同请求体返回 `409 idempotency_conflict`。

### 自助管理接口

管理接口只接受已登录用户会话；API Key 本身不能创建或管理其他 Key。

| 端点 | 作用 |
|---|---|
| `GET/POST /api/platform/keys` | 列表/创建；完整 `secret` 仅在创建响应出现一次 |
| `GET/PATCH/DELETE /api/platform/keys/{id}` | owner-check 读取、带 `revision` 更新、不可逆撤销 |
| `GET /api/platform/keys/{id}/usage` | Key 级 1–366 天用量与估算费用 |

标准 Scope 为 `models:read`、`responses:read/write`、`threads:read/write`、`runs:read/write` 和 `usage:read`。限流响应返回 `Retry-After` 与 `X-RateLimit-*`；费用估算值不会冒充供应商正式账单。

## 对象与端点

对象关系为 `Project -> Thread -> Turn -> Item`，执行关系为 `Run -> Event -> Approval -> Checkpoint -> Artifact/Evidence`。

| 端点 | 作用 |
|---|---|
| `GET /v1/models` | 可用模型 |
| `GET /v1/capabilities` | 真实能力与限制 |
| `POST /v1/responses` | 规范响应入口；支持 `background`、`stream`、RAG 和显式文档范围 |
| `GET/DELETE /v1/responses/{id}` | owner 范围内读取/删除响应 |
| `GET /v1/responses/{id}/events` | JSON 或 SSE 续读持久化事件 |
| `POST /v1/chat/completions` | Chat Completions 兼容入口 |
| `POST/GET/PATCH /v1/threads` | 创建、读取、修订 Thread |
| `POST /v1/threads/{id}/fork` | 复制公开 Turn/Item 历史，不复制隐私推理 |
| `POST /v1/runs` | 创建 durable WorkRun |
| `GET /v1/runs/{id}` | 读取任务与公开任务链 |
| `GET /v1/runs/{id}/events` | JSON/SSE 运行事件 |
| `GET /v1/runs/{id}/checkpoints` | 可恢复检查点 |
| `POST /v1/runs/{id}/approvals/{approval_id}` | owner 绑定、一次性审批 |
| `POST /v1/runs/{id}/commands` | pause/resume/cancel/retry |
| `GET /v1/usage` | owner 范围内强用量账本 |

## SSE

每个事件包含 `event_id`、`sequence`、`response_id/run_id`、`turn_id`、`item_id`、`timestamp` 和 `schema_version`。客户端可用 `Last-Event-ID` 或 `after` 续读。主要事件：

- `response.created`
- `run.status.changed`
- `response.output_text.delta`
- `response.completed`
- `error`

当前模型适配器是整段生成接口，因此 delta 会在模型返回后从已持久化结果投影；这不冒充供应商原生 token 流。已经产生不可重放输出后不进行跨供应商自动切换。

## 状态与完成语义

规范状态包括 `created`、`queued`、`running`、`waiting_approval`、`waiting_input`、`paused`、`retrying`、`verifying`、`delivered`、`completed`、`failed`、`cancelled`、`interrupted`。旧客户端需要的 `blocked/observed` 仅保留为兼容状态。

`delivered` 表示生成了待验收结果；只有接受门通过后才能进入 `completed`。服务重启后无法安全重放的 Python 闭包任务会标成 `interrupted`，不会自动重复副作用。

## 检索与成本边界

- 显式 `document_scope` 在 RRF 后再次执行严格白名单过滤，Sparse/BM25 结果不能越界。
- Expected Gain Selector 记录相关性、引用完整性、信任、token 成本、延迟、来源风险和冗余分量；所有候选低于阈值时保留最高项并标记 fallback。
- 供应商调用前先预留预算，调用后 settle。估算值、供应商回报值和对账值分字段保存，估算成本不会冒充账单。
