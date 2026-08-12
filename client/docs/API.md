# HashMM 对外 API（/v1）

HashMM 提供一组对外 RESTful API，风格参考 R2R。默认**关闭**，需显式启用。

## 启用与鉴权

```bash
export HASHMM_PUBLIC_API=1      # 开启 /v1
export HASHMM_API_KEY=sk-...    # 默认必需：Bearer / X-API-Key
# 仅可信内网可显式免鉴权：HASHMM_PUBLIC_API_OPEN=1
```

鉴权方式：请求头 `Authorization: Bearer <HASHMM_API_KEY>` 或 `X-API-Key: <HASHMM_API_KEY>`。
未设置 key 并不会自动开放；默认还可使用管理员会话。只有显式设置
`HASHMM_PUBLIC_API_OPEN=1` 才免鉴权，且只应在可信内网使用。

所有错误响应遵循统一错误体（见文末错误码）。

## 端点

### GET /v1/info

返回服务信息与能力探测。无需请求体。

响应示例：

```json
{
  "service": "hashmm",
  "version": "0.5.0",
  "release": "V337",
  "capabilities": ["hybrid_search", "citation_rag"]
}
```

### POST /v1/search

纯检索：返回知识库命中片段，不生成答案。

请求体：

```json
{
  "query": "小米2024年营收",
  "top_k": 5
}
```

字段：`query`（必填，字符串）、`top_k`（可选，默认 5）。
`top_k` 必须是 1–100 的整数。

响应：

```json
{
  "query": "小米2024年营收",
  "results": [
    {"content": "...", "source": "...", "filename": "...", "page": 12, "score": 0.87}
  ],
  "num_results": 5,
  "elapsed_ms": 134
}
```

### POST /v1/rag

检索 + 生成：先检索再让模型基于命中片段作答，返回答案与来源。

请求体：

```json
{
  "query": "对比小米和华为的研发投入",
  "top_k": 5,
  "history": []
}
```

字段：`query`（必填）、`top_k`（可选，默认 5）、`history`（可选，多轮对话历史）。

## Python SDK

从项目根目录安装开发版：

```bash
pip install -e .
```

调用：

```python
from hashmm.client import HashMMClient

client = HashMMClient("http://127.0.0.1:6006", api_key="sk-...")
hits = client.search("小米 2024 年营收", top_k=5)
answer = client.rag("小米 2024 年营收是多少？")
print(answer["answer"], answer["sources"])
```

HTTP 非 2xx 会抛出 `HashMMHTTPError`（含 `status_code`、`error_code`、`details`）；
连接失败会抛出 `HashMMTransportError`，不会再用一个容易被忽略的 `{"error": ...}` 假装成功返回。

响应：

```json
{
  "query": "对比小米和华为的研发投入",
  "answer": "……",
  "sources": [
    {"source": "...", "filename": "...", "page": 12, "score": 0.87}
  ],
  "num_sources": 5
}
```

LLM 未就绪时返回 503。

## 错误响应

统一格式（由 `hashmm/error_codes.py` 生成）：

```json
{
  "error": {
    "code": "llm_error",
    "message": "模型服务暂时不可用",
    "category": "upstream",
    "details": {},
    "trace_id": "a1b2c3d4..."
  }
}
```

常见 code → HTTP 映射：`bad_request` 400、`auth_error` 401、`forbidden` 403、`not_found` 404、`rate_limited` 429、`internal_error` 500、`llm_error`/`retrieval_error` 502、`upstream_timeout` 504。完整列表见错误码文档（`error_codes.generate_error_codes_md()`）。

## 限流与版本

建议在反向代理或网关层对 `/v1` 做限流（如每 key 每分钟 N 次）。当前 API 版本前缀为 `/v1`；不兼容变更将通过新前缀（如 `/v2`）发布，`/v1` 保持向后兼容。
# 可恢复长任务（V335）

所有接口均需登录，并按任务 owner 做对象级授权。

```http
GET  /api/loops
GET  /api/loops/{id}
POST /api/loops/{id}/pause
POST /api/loops/{id}/resume
POST /api/loops/{id}/stop
```

创建目标任务：

```http
POST /api/loops/goal
Content-Type: application/json

{
  "goal": "核实资料并生成有来源的报告",
  "acceptance": "至少 3 个官方来源并生成 report.md",
  "approval_mode": "read_only",
  "max_rounds": 4,
  "threshold": 85,
  "max_tokens": 50000,
  "max_seconds": 3600,
  "conv_id": "可选会话 ID"
}
```

`approval_mode` 只接受 `read_only` 或 `workspace`；非法值在服务端保守降级为
`read_only`。定时任务使用 `POST /api/loops/interval`，核心字段为 `prompt`、
`interval_min`、`max_runs`，其余预算与授权字段相同。

# 仓库计划与证据型 Review（V336）

两条接口均需登录。桌面端在本地读取 Git 与指令文件，后端不接收本机路径、也不执行 Git 命令。

```http
POST /api/repo/plan
Content-Type: application/json

{
  "goal": "修复登录回归并补测试",
  "instructions": "按 global → root → cwd 合并后的 AGENTS 内容",
  "plan_template": "最近的 PLANS.md 内容",
  "status": "当前分支和 Git status 文本"
}
```

返回最多 12 个同时包含 `action` 与 `acceptance` 的步骤。`notice` 会明确标记计划尚未执行；
桌面端必须等待用户确认后，才把计划交给 Computer Use 执行。

```http
POST /api/repo/review
Content-Type: application/json

{
  "diff": "staged 与 unstaged 的统一补丁",
  "instructions": "已加载的 AGENTS 内容",
  "scope": "working_tree"
}
```

`diff` 最大 350 KiB、指令最大 32 KiB。模型候选只有在文件确实位于补丁中、行号确实是新增行、
且至少 8 个字符的证据能在对应文件补丁中精确匹配时才会返回；其余计入
`discarded_findings`。这个确定性闸只能验证定位，不证明缺陷判断一定正确，仍需测试与人工复核。
