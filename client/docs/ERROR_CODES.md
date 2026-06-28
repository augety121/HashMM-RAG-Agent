# HashMM 错误码

| code | HTTP | 类别 | 说明 |
|---|---|---|---|
| `bad_request` | 400 | client | 请求参数有误 |
| `config_error` | 400 | client | 配置错误 |
| `safety_error` | 400 | safety | 检测到不安全内容 |
| `auth_error` | 401 | auth | 认证失败或令牌已过期 |
| `forbidden` | 403 | auth | 无权访问 |
| `not_found` | 404 | client | 资源不存在 |
| `rate_limited` | 429 | client | 请求过于频繁，请稍后再试 |
| `ingest_error` | 500 | server | 文档处理失败 |
| `internal_error` | 500 | server | 服务器内部错误 |
| `llm_error` | 502 | upstream | 模型服务暂时不可用 |
| `retrieval_error` | 502 | upstream | 知识检索暂时不可用 |
| `upstream_timeout` | 504 | upstream | 上游服务超时 |
