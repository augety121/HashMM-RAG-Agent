"""MCP Server 暴露端 —— 把 HashMM 的检索/图谱能力暴露成标准 MCP server。

定位（BENCHMARK_AND_ROADMAP_2026 §三-1，最高 ROI）：此前 HashMM 只做了 MCP **消费端**
（`tools/mcp_client.py` 连别人的 MCP server）。本模块补上**暴露端** —— 把自己暴露成一个
标准 MCP server，让 Claude Code / Cursor / 任何 MCP 客户端都能直接把 HashMM 的
知识库检索 + GraphRAG 当工具调用。这是"本地优先 RAG-Agent"叙事的放大器：别人的 Agent
能用你的本地 RAG，且数据不出你的机器。

协议：Model Context Protocol，JSON-RPC 2.0 over HTTP（与消费端 `tools/mcp_client.py`
对称：initialize / tools/list / tools/call）。支持普通 JSON 响应；客户端可用 SSE Accept。

暴露的工具（只读、安全；不暴露任何写操作 / admin 能力）：
  - kb_search(query, top_k)        : 混合检索（向量+BM25+RRF+重排），复用 kb_search_bridge
  - kg_query(query, mode, top_k)   : 知识图谱检索（local/mix/global），复用 KGRetriever
  - corpus_stats()                 : 语料/图谱规模概览

铁律：
  - **默认关**（`HASHMM_MCP_SERVER` 未开 → 路由不注册 → 零变化、零暴露面）。
  - **鉴权**：复用 metrics 同款（HASHMM_MCP_TOKEN / admin / 显式 PUBLIC），默认需要 token。
  - **只读**：绝不暴露写图谱、改配置、删数据等能力。
  - 永不抛错：任何内部异常都包成 JSON-RPC error，不把服务带崩。
"""
from __future__ import annotations

import os
import hmac as _hmac

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.api.mcp_server")

router = APIRouter(prefix="/mcp", tags=["mcp-server"])

_PROTOCOL_VERSION = "2024-11-05"


def server_enabled() -> bool:
    return os.environ.get("HASHMM_MCP_SERVER", "0").strip().lower() in {"1", "true", "yes", "on"}


def _authorized(request: Request) -> bool:
    """与 metrics 端点同款鉴权：PUBLIC 开 / token 匹配 / admin。默认需要 token。"""
    if os.environ.get("HASHMM_MCP_PUBLIC", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    token_cfg = os.environ.get("HASHMM_MCP_TOKEN", "")
    if token_cfg:
        auth = request.headers.get("Authorization", "")
        bearer = auth[7:] if auth.startswith("Bearer ") else ""
        supplied = (bearer or request.query_params.get("token", "")
                    or request.headers.get("X-MCP-Token", ""))
        if supplied and _hmac.compare_digest(supplied, token_cfg):
            return True
    try:
        from hashmm.api.auth import get_current_user
        user = get_current_user(request)
        return bool(user and user.get("role") == "admin")
    except Exception:
        return False


# ── 工具定义（MCP tools/list 的标准 schema）──────────────────────────────
_TOOLS = [
    {
        "name": "kb_search",
        "description": "在 HashMM 知识库中做混合检索（向量+BM25+RRF+重排），返回最相关的若干片段及来源。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索查询"},
                "top_k": {"type": "integer", "description": "返回条数，默认 5", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "kg_query",
        "description": "在 HashMM 知识图谱中检索实体与关系（GraphRAG），用于多跳/对比/关系类问题。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索查询"},
                "mode": {"type": "string", "description": "kg | mix | global", "default": "mix"},
                "top_k": {"type": "integer", "description": "实体/关系各取多少，默认 8", "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "corpus_stats",
        "description": "返回 HashMM 当前语料与知识图谱的规模概览（文档/分块/实体/关系数量）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ── 工具执行（全部只读，复用既有 bridge / retriever）─────────────────────
def _exec_kb_search(args: dict) -> str:
    from hashmm.retriever_bridge import kb_search_bridge
    res = kb_search_bridge({"query": args.get("query", ""), "top_k": int(args.get("top_k", 5) or 5)})
    return res.get("text") or "知识库中未找到相关结果。"


def _exec_kg_query(args: dict) -> str:
    try:
        from hashmm.kg.kg_retriever import KGRetriever
        r = KGRetriever()
        out = r.search(args.get("query", ""),
                       mode=str(args.get("mode", "mix")),
                       top_k_entities=int(args.get("top_k", 8) or 8),
                       top_k_relations=int(args.get("top_k", 8) or 8))
        # KGSearchResult 真实字段：entities / relations / chunk_ids（无 context/text）
        ents = getattr(out, "entities", None) or []
        rels = getattr(out, "relations", None) or []
        lines = [f"图谱检索：{len(ents)} 实体 / {len(rels)} 关系。"]
        if ents:
            names = [str(e.get("name", e.get("id", ""))) for e in ents[:8] if isinstance(e, dict)]
            lines.append("相关实体：" + "、".join(n for n in names if n))
        if rels:
            for rel in rels[:8]:
                if isinstance(rel, dict):
                    h = rel.get("source", rel.get("head", ""))
                    t = rel.get("target", rel.get("tail", ""))
                    desc = rel.get("relation", rel.get("description", ""))
                    if h and t:
                        lines.append(f"  · {h} —{desc}→ {t}")
        return "\n".join(lines)[:2000]
    except Exception as e:
        log_suppressed(logger, e)
        return f"图谱检索暂不可用：{type(e).__name__}"


def _exec_corpus_stats(args: dict) -> str:
    parts = []
    try:
        from hashmm.retriever_bridge import get_pipeline
        p = get_pipeline()
        if p is not None:
            n = getattr(p, "num_vectors", None) or getattr(p, "n_docs", None)
            if n:
                parts.append(f"检索索引：{n} 向量")
    except Exception as e:
        log_suppressed(logger, e)
    try:
        from hashmm.kg.storage import KGStorage
        s = KGStorage().get_stats()
        parts.append(f"知识图谱：{s.get('entities', 0)} 实体 / {s.get('relations', 0)} 关系 / "
                     f"{s.get('communities', 0)} 社区")
    except Exception as e:
        log_suppressed(logger, e)
    return "；".join(parts) if parts else "暂无统计信息。"


_EXECUTORS = {
    "kb_search": _exec_kb_search,
    "kg_query": _exec_kg_query,
    "corpus_stats": _exec_corpus_stats,
}


# ── JSON-RPC 处理 ────────────────────────────────────────────────────────
def _rpc_result(req_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _handle(method: str, params: dict, req_id) -> dict:
    if method == "initialize":
        return _rpc_result(req_id, {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "hashmm", "version": "17.0"},
        })
    if method in ("notifications/initialized", "initialized"):
        return _rpc_result(req_id, {})
    if method == "tools/list":
        return _rpc_result(req_id, {"tools": _TOOLS})
    if method == "tools/call":
        name = (params or {}).get("name", "")
        args = (params or {}).get("arguments", {}) or {}
        fn = _EXECUTORS.get(name)
        if not fn:
            return _rpc_error(req_id, -32601, f"unknown tool: {name}")
        try:
            text = fn(args)
        except Exception as e:
            log_suppressed(logger, e)
            return _rpc_error(req_id, -32603, f"tool execution failed: {type(e).__name__}")
        # MCP tools/call 标准返回：content 数组
        return _rpc_result(req_id, {"content": [{"type": "text", "text": str(text)}]})
    return _rpc_error(req_id, -32601, f"method not found: {method}")


@router.post("")
@router.post("/")
async def mcp_endpoint(request: Request):
    """MCP server 主端点（JSON-RPC 2.0 over HTTP）。

    默认关：HASHMM_MCP_SERVER 未开时返回 404，对外完全不可见、零暴露面。
    """
    if not server_enabled():
        return JSONResponse({"error": "MCP server disabled (set HASHMM_MCP_SERVER=1)"}, status_code=404)
    if not _authorized(request):
        return JSONResponse(
            _rpc_error(None, -32001, "unauthorized: 需要 HASHMM_MCP_TOKEN / admin（或设 HASHMM_MCP_PUBLIC=1）"),
            status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_rpc_error(None, -32700, "parse error"), status_code=400)

    # 支持单条与批量
    if isinstance(body, list):
        return JSONResponse([_handle(m.get("method", ""), m.get("params", {}), m.get("id"))
                             for m in body])
    return JSONResponse(_handle(body.get("method", ""), body.get("params", {}), body.get("id")))


@router.get("")
@router.get("/")
async def mcp_info(request: Request):
    """GET 探活/自描述：返回 server 是否开启与暴露的工具名（不需鉴权，只暴露元信息）。"""
    if not server_enabled():
        return JSONResponse({"enabled": False, "hint": "set HASHMM_MCP_SERVER=1 to enable"})
    return JSONResponse({
        "enabled": True,
        "protocolVersion": _PROTOCOL_VERSION,
        "transport": "http-jsonrpc",
        "tools": [t["name"] for t in _TOOLS],
        "auth": "Bearer HASHMM_MCP_TOKEN / admin / HASHMM_MCP_PUBLIC=1",
    })
