# HashMM 对外 API（/v1）

HashMM 提供一组对外 RESTful API，风格参考 R2R。默认**关闭**，需显式启用。

## 启用与鉴权

```bash
export HASHMM_PUBLIC_API=1          # 开启 /v1
export HASHMM_PUBLIC_API_KEY=sk-... # 可选：设置后所有 /v1 请求需带 Authorization
```

鉴权方式：请求头 `Authorization: Bearer <HASHMM_PUBLIC_API_KEY>`。未设置 key 时不校验（仅建议内网使用）。

所有错误响应遵循统一错误体（见文末错误码）。

## 端点

### GET /v1/info

返回服务信息与能力探测。无需请求体。

响应示例：

```json
{
  "name": "HashMM-RAG",
  "version": "...",
  "capabilities": ["search", "rag"]
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
