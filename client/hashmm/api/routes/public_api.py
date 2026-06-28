"""对外 RESTful API —— /v1 稳定契约（API-first，对标 R2R）。

定位（BENCHMARK_AND_ROADMAP_2026 §三-3，最有价值但最重）：HashMM 此前对外只有 Web 控制台
（`/api/*` 多为内部端点）。本模块补上一组**稳定的对外契约** `/v1/*`，让开发者能用 API key
集成 HashMM 的检索/RAG，配合薄 client（`clients/python/hashmm_client.py`）可像 R2R 那样
`client.rag("...")` 一行调用。这是把 HashMM 从"一个应用"升级为"一个可被集成的平台"。

为什么单开 /v1 而不复用 /api：
  - 安全隔离：/api/* 是内部端点（含 admin/写操作），不应直接对外；/v1 只暴露只读检索/RAG。
  - 契约稳定：/v1 是对外承诺，字段保持向后兼容；内部 /api 可以自由演进。

端点（全部只读，绝不暴露写/admin）：
  POST /v1/search  {query, top_k}                  → 混合检索结果
  POST /v1/rag     {query, top_k, history}         → 检索+生成（带来源），R2R 风
  GET  /v1/info                                    → 能力/版本/工具描述

铁律：
  - **默认关**：`HASHMM_PUBLIC_API` 未开 → 路由 404、零暴露面、零行为变化。
  - **鉴权**：默认需要 API key（`HASHMM_API_KEY`，Bearer / X-API-Key），或 admin；
    显式 `HASHMM_PUBLIC_API_OPEN=1` 才免鉴权（仅限可信内网）。
  - 永不抛错：内部异常包成标准错误响应。
"""
from __future__ import annotations

import os
import hmac as _hmac

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.api.public_api")

router = APIRouter(prefix="/v1", tags=["public-api"])


def _err(code: str, message: str = "", status: int | None = None,
         details: dict | None = None) -> JSONResponse:
    """S1-1：用统一错误码体系返回标准错误体（含 trace_id）。

    退化安全：error_codes/trace_context 缺失时回退到简单 {"error": ...}。
    """
    try:
        from hashmm import error_codes as EC
        from hashmm.trace_context import current_trace_id
        spec = EC.spec_for(code)
        body = {"error": {
            "code": spec.code,
            "message": message or spec.message,
            "category": spec.category,
            "details": details or {},
        }}
        tid = current_trace_id()
        if tid:
            body["error"]["trace_id"] = tid
        try:
            from hashmm import observability as _obs
            _obs.record_error(spec.code, trace_id=tid)
        except Exception:
            pass
        return JSONResponse(body, status_code=status or spec.http_status)
    except Exception:
        return JSONResponse({"error": message or code}, status_code=status or 500)


def public_api_enabled() -> bool:
    return os.environ.get("HASHMM_PUBLIC_API", "0").strip().lower() in {"1", "true", "yes", "on"}


def _authorized(request: Request) -> bool:
    if os.environ.get("HASHMM_PUBLIC_API_OPEN", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    key_cfg = os.environ.get("HASHMM_API_KEY", "")
    if key_cfg:
        auth = request.headers.get("Authorization", "")
        bearer = auth[7:] if auth.startswith("Bearer ") else ""
        supplied = (bearer or request.headers.get("X-API-Key", "")
                    or request.query_params.get("api_key", ""))
        if supplied and _hmac.compare_digest(supplied, key_cfg):
            return True
    try:
        from hashmm.api.auth import get_current_user
        user = get_current_user(request)
        return bool(user and user.get("role") == "admin")
    except Exception:
        return False


def _guard(request: Request):
    """返回 None=放行；否则返回应当直接响应的 JSONResponse。"""
    if not public_api_enabled():
        return _err("not_found", "public API disabled (set HASHMM_PUBLIC_API=1)", status=404)
    if not _authorized(request):
        return _err("auth_error", "unauthorized: 需要 API key（HASHMM_API_KEY / X-API-Key / Bearer）")
    return None


@router.get("")
@router.get("/")
@router.get("/info")
async def v1_info(request: Request):
    # info 在服务开启时返回能力描述（不含数据，可无鉴权探活）；未开启则 404。
    if not public_api_enabled():
        return _err("not_found", "public API disabled (set HASHMM_PUBLIC_API=1)", status=404)
    return JSONResponse({
        "service": "hashmm",
        "version": "17.0",
        "endpoints": {
            "POST /v1/search": "混合检索（向量+BM25+RRF+重排），body: {query, top_k}",
            "POST /v1/rag": "检索增强生成（带来源），body: {query, top_k, history}",
            "GET /v1/info": "能力描述",
        },
        "auth": "Bearer HASHMM_API_KEY / X-API-Key / admin",
    })


@router.post("/search")
async def v1_search(request: Request):
    try:
        from hashmm.trace_context import new_trace
        new_trace(request.headers.get("x-trace-id"))
    except Exception:
        pass
    g = _guard(request)
    if g is not None:
        return g
    try:
        body = await request.json()
    except Exception:
        return _err("bad_request", "invalid JSON body")
    query = str(body.get("query", "")).strip()
    if not query:
        return _err("bad_request", "query is required")
    top_k = int(body.get("top_k", 5) or 5)
    try:
        from hashmm.retriever_bridge import kb_search_bridge
        res = kb_search_bridge({"query": query, "top_k": top_k})
        return JSONResponse({
            "query": query,
            "results": res.get("results", []),
            "num_results": res.get("num_results", 0),
            "elapsed_ms": res.get("elapsed_ms"),
        })
    except Exception as e:
        log_suppressed(logger, e)
        return _err("retrieval_error", "search failed", details={"type": type(e).__name__})


@router.post("/rag")
async def v1_rag(request: Request):
    """检索 + 生成（R2R 风 client.rag）。复用既有 kb_search_bridge + server.generate。"""
    try:
        from hashmm.trace_context import new_trace
        new_trace(request.headers.get("x-trace-id"))
    except Exception:
        pass
    g = _guard(request)
    if g is not None:
        return g
    try:
        body = await request.json()
    except Exception:
        return _err("bad_request", "invalid JSON body")
    query = str(body.get("query", "")).strip()
    if not query:
        return _err("bad_request", "query is required")
    top_k = int(body.get("top_k", 5) or 5)
    history = body.get("history", []) or []
    try:
        from hashmm.retriever_bridge import kb_search_bridge
        from hashmm.api import server
        search = kb_search_bridge({"query": query, "top_k": top_k})
        results = search.get("results", [])
        # 复用 server.generate；它读 r.get("text")，而 bridge 给的是 content → 适配。
        gen_input = [{"text": r.get("content", r.get("text", "")), **r} for r in results]
        if getattr(server, "_llm_fn", None) is None:
            return _err("internal_error", "LLM 未就绪（服务未完成初始化）", status=503)
        answer = server.generate(query, gen_input, history, "chat")
        return JSONResponse({
            "query": query,
            "answer": str(answer),
            "sources": [{"source": r.get("source"), "filename": r.get("filename"),
                         "page": r.get("page"), "score": r.get("score")} for r in results],
            "num_sources": len(results),
        })
    except Exception as e:
        log_suppressed(logger, e)
        return _err("llm_error", "rag failed", details={"type": type(e).__name__})
