"""统一错误码体系（P3-1）—— 集中登记所有错误码 + HTTP 状态映射 + 稳定文档。

为什么：现有 exceptions.py 的每个异常各自硬编码 code 字符串，散落、无统一目录、
无 HTTP 状态映射、对外不稳定。这里做一个【集中注册表】：
- 每个错误码登记一次：code / HTTP 状态 / 面向用户的中文描述 / 类别。
- 提供 code → HTTP 状态 的映射（对外 API 返回标准状态码）。
- 提供 to_error_response()：把异常转成稳定的 JSON 错误体（对标大厂 API 错误格式）。

不改现有异常类（它们的 .code 继续有效），只是给 code 一个权威目录和映射。
新代码可 `from hashmm.error_codes import ErrorCode, http_status_for`。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    http_status: int
    message: str       # 面向用户的默认中文描述
    category: str      # 分类：client/auth/server/safety/upstream


# ── 错误码注册表（单一事实来源）──
# 4xx = 客户端/请求问题；401/403 = 鉴权；5xx = 服务端；502/504 = 上游(LLM/检索)。
_REGISTRY: dict[str, ErrorSpec] = {
    # 客户端请求类
    "bad_request":      ErrorSpec("bad_request", 400, "请求参数有误", "client"),
    "not_found":        ErrorSpec("not_found", 404, "资源不存在", "client"),
    "rate_limited":     ErrorSpec("rate_limited", 429, "请求过于频繁，请稍后再试", "client"),
    "config_error":     ErrorSpec("config_error", 400, "配置错误", "client"),
    "invalid_input":    ErrorSpec("invalid_input", 400, "输入格式无效", "client"),
    "invalid_request":  ErrorSpec("invalid_request", 400, "请求无效", "client"),
    "invalid_action":   ErrorSpec("invalid_action", 400, "操作无效", "client"),
    "invalid_decision": ErrorSpec("invalid_decision", 400, "决策无效", "client"),
    "invalid_status":   ErrorSpec("invalid_status", 400, "状态无效", "client"),
    "idempotency_key_required": ErrorSpec("idempotency_key_required", 400, "写操作必须提供 Idempotency-Key", "client"),
    "invalid_idempotency_key": ErrorSpec("invalid_idempotency_key", 400, "Idempotency-Key 格式无效", "client"),
    "idempotency_conflict": ErrorSpec("idempotency_conflict", 409, "幂等键已被不同请求使用", "client"),
    "request_in_progress": ErrorSpec("request_in_progress", 409, "同一幂等请求仍在执行", "client"),
    "revision_conflict": ErrorSpec("revision_conflict", 409, "资源版本已变化", "client"),
    "budget_exceeded":  ErrorSpec("budget_exceeded", 402, "预算不足", "client"),
    "rate_limit_exceeded": ErrorSpec("rate_limit_exceeded", 429, "API Key 请求速率已达上限", "client"),
    "concurrency_limit_exceeded": ErrorSpec("concurrency_limit_exceeded", 429, "API Key 并发数已达上限", "client"),
    "quota_exceeded": ErrorSpec("quota_exceeded", 429, "API Key 配额不足", "client"),
    "invalid_scope": ErrorSpec("invalid_scope", 400, "API Key Scope 无效", "client"),
    "invalid_ip_rule": ErrorSpec("invalid_ip_rule", 400, "IP 访问规则无效", "client"),
    "revision_required": ErrorSpec("revision_required", 400, "更新必须提供资源版本", "client"),
    # 鉴权类
    "auth_error":       ErrorSpec("auth_error", 401, "认证失败或令牌已过期", "auth"),
    "forbidden":        ErrorSpec("forbidden", 403, "无权访问", "auth"),
    "invalid_api_key": ErrorSpec("invalid_api_key", 401, "API Key 无效或已失效", "auth"),
    "insufficient_scope": ErrorSpec("insufficient_scope", 403, "API Key 权限不足", "auth"),
    "resource_not_allowed": ErrorSpec("resource_not_allowed", 403, "API Key 无权访问该资源", "auth"),
    # 安全类
    "safety_error":     ErrorSpec("safety_error", 400, "检测到不安全内容", "safety"),
    # 上游依赖类（LLM / 检索）
    "llm_error":        ErrorSpec("llm_error", 502, "模型服务暂时不可用", "upstream"),
    "llm_unavailable":  ErrorSpec("llm_unavailable", 503, "模型服务尚未就绪", "upstream"),
    "retrieval_error":  ErrorSpec("retrieval_error", 502, "知识检索暂时不可用", "upstream"),
    "upstream_timeout": ErrorSpec("upstream_timeout", 504, "上游服务超时", "upstream"),
    # 服务端类
    "ingest_error":     ErrorSpec("ingest_error", 500, "文档处理失败", "server"),
    "internal_error":   ErrorSpec("internal_error", 500, "服务器内部错误", "server"),
    "idempotency_unavailable": ErrorSpec("idempotency_unavailable", 503, "幂等账本不可用", "server"),
    "idempotency_commit_unavailable": ErrorSpec("idempotency_commit_unavailable", 503, "幂等结果无法提交", "server"),
    "recovery_incomplete": ErrorSpec("recovery_incomplete", 503, "恢复所需的运行状态不完整", "server"),
}


def all_codes() -> list[ErrorSpec]:
    """返回所有已登记错误码（便于生成文档/对外公布）。"""
    return list(_REGISTRY.values())


def spec_for(code: str) -> ErrorSpec:
    """查错误码规格；未知 code 退化为 internal_error。"""
    return _REGISTRY.get(code, _REGISTRY["internal_error"])


def http_status_for(code: str) -> int:
    """code → HTTP 状态码。"""
    return spec_for(code).http_status


def to_error_response(exc: Exception, *, trace_id: str = "") -> tuple[int, dict]:
    """把异常转成 (http_status, json_body)（对标大厂稳定错误格式）。

    优先用 HashMMError 的 .code/.message/.details；其他异常归为 internal_error。
    返回的 body 形如：
        {"error": {"code": "...", "message": "...", "details": {...}, "trace_id": "..."}}
    """
    code = "internal_error"
    message = ""
    details: dict = {}
    try:
        # HashMMError 有 code/message/details
        code = getattr(exc, "code", None) or "internal_error"
        message = getattr(exc, "message", None) or ""
        details = getattr(exc, "details", None) or {}
    except Exception:
        pass
    spec = spec_for(code)
    body = {
        "error": {
            "code": spec.code,
            "message": message or spec.message,
            "category": spec.category,
            "details": details,
        }
    }
    if trace_id:
        body["error"]["trace_id"] = trace_id
    # S3-2: 错误码计数（接入 observability，便于运维面板看"哪类错误最多"）。永不抛错。
    try:
        from hashmm import observability as _obs
        _obs.record_error(spec.code, trace_id=trace_id)
    except Exception:
        pass
    return spec.http_status, body


def generate_error_codes_md() -> str:
    """生成错误码文档（Markdown 表格），对外公布用。"""
    lines = ["# HashMM 错误码", "",
             "| code | HTTP | 类别 | 说明 |", "|---|---|---|---|"]
    for s in sorted(_REGISTRY.values(), key=lambda x: (x.http_status, x.code)):
        lines.append(f"| `{s.code}` | {s.http_status} | {s.category} | {s.message} |")
    return "\n".join(lines) + "\n"
