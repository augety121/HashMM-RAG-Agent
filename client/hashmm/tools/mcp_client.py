"""MCP Client — 连接外部 MCP 服务器，发现并调用它们的工具。

MCP (Model Context Protocol) 是 Claude Code 等使用的开放协议：一个 MCP 服务器
暴露一批工具，客户端连上去就能用全部工具。接一个服务器 = 接一批工具。

本模块实现一个【MCP 客户端】：
  1. 用户在后台配置一个 MCP 服务器（HTTP/SSE 端点 或 stdio 命令）
  2. 客户端连上去，调用标准的 `tools/list` 拿到工具清单
  3. 把这些工具转成 OpenAI function-calling schema，注入 Agent Loop
  4. AI 调用时，客户端用标准 `tools/call` 转发给 MCP 服务器

支持两种传输：
  - http  : JSON-RPC over HTTP（POST 到 endpoint）—— 最通用，本模块完整实现
  - sse   : HTTP + Server-Sent Events —— 同 http POST 发起，结果走 SSE（简化处理）

stdio 传输（本地子进程）在容器里较少用，本期先支持 http/sse。

配置存在 mcp_servers 表。schema 注入时，工具名加服务器前缀避免冲突：
  "mcp__<server>__<tool>"
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from hashmm.utils import get_logger

logger = get_logger("hashmm.tools.mcp_client")

# Tool name separator (mirrors Claude Code's mcp__{server}__{tool} convention)
_SEP = "__"
_PREFIX = "mcp__"


# ════════════════════════════════════════════════════════════════════════
# DB layer — configured MCP servers
# ════════════════════════════════════════════════════════════════════════

def _ensure_table():
    from hashmm.api import database as db
    with db._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS mcp_servers (
                id          TEXT PRIMARY KEY,
                name        TEXT UNIQUE NOT NULL,
                transport   TEXT DEFAULT 'http',
                endpoint    TEXT NOT NULL,
                headers     TEXT DEFAULT '{}',
                enabled     INTEGER DEFAULT 1,
                tools_cache TEXT DEFAULT '[]',
                created_by  TEXT DEFAULT 'admin',
                created_at  REAL DEFAULT (strftime('%s','now'))
            )
        """)


def create_server(cfg: dict) -> str:
    _ensure_table()
    from hashmm.api import database as db
    sid = uuid.uuid4().hex[:12]
    with db._conn() as c:
        c.execute(
            """INSERT INTO mcp_servers (id,name,transport,endpoint,headers,enabled,created_by)
               VALUES (?,?,?,?,?,?,?)""",
            (sid, cfg["name"], cfg.get("transport", "http"), cfg["endpoint"],
             json.dumps(cfg.get("headers", {}), ensure_ascii=False),
             1 if cfg.get("enabled", True) else 0, cfg.get("created_by", "admin")),
        )
    return sid


def update_server(sid: str, cfg: dict) -> bool:
    _ensure_table()
    from hashmm.api import database as db
    with db._conn() as c:
        c.execute(
            """UPDATE mcp_servers SET name=?, transport=?, endpoint=?, headers=?, enabled=?
               WHERE id=?""",
            (cfg["name"], cfg.get("transport", "http"), cfg["endpoint"],
             json.dumps(cfg.get("headers", {}), ensure_ascii=False),
             1 if cfg.get("enabled", True) else 0, sid),
        )
    return True


def delete_server(sid: str) -> bool:
    _ensure_table()
    from hashmm.api import database as db
    with db._conn() as c:
        c.execute("DELETE FROM mcp_servers WHERE id=?", (sid,))
    return True


def list_servers(only_enabled: bool = False) -> list[dict]:
    _ensure_table()
    from hashmm.api import database as db
    with db._conn() as c:
        q = "SELECT * FROM mcp_servers"
        if only_enabled:
            q += " WHERE enabled=1"
        q += " ORDER BY created_at DESC"
        rows = c.execute(q).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["headers"] = json.loads(d.get("headers") or "{}")
        d["tools_cache"] = json.loads(d.get("tools_cache") or "[]")
        d["enabled"] = bool(d.get("enabled", 0))
        out.append(d)
    return out


def _cache_tools(sid: str, tools: list[dict]):
    from hashmm.api import database as db
    with db._conn() as c:
        c.execute("UPDATE mcp_servers SET tools_cache=? WHERE id=?",
                  (json.dumps(tools, ensure_ascii=False), sid))


# ════════════════════════════════════════════════════════════════════════
# JSON-RPC over HTTP — the MCP wire protocol
# ════════════════════════════════════════════════════════════════════════

def _rpc(endpoint: str, headers: dict, method: str, params: dict | None = None,
         timeout: float = 20.0) -> dict:
    """Send one JSON-RPC 2.0 request to an MCP server over HTTP."""
    import httpx
    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex[:8],
        "method": method,
        "params": params or {},
    }
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    h.update(headers or {})
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.post(endpoint, json=payload, headers=h)
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            # SSE response: parse the first data: line that is JSON-RPC
            for line in resp.text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    body = line[5:].strip()
                    try:
                        return json.loads(body)
                    except json.JSONDecodeError:
                        continue
            return {"error": {"message": "no JSON in SSE response"}}
        return resp.json()


def discover_tools(cfg: dict) -> list[dict]:
    """Connect to an MCP server and list its tools (standard tools/list)."""
    endpoint = cfg["endpoint"]
    headers = cfg.get("headers", {})
    # MCP handshake: initialize, then tools/list. Some servers skip initialize.
    try:
        _rpc(endpoint, headers, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "hashmm", "version": "14.0"},
        })
    except Exception as e:
        logger.debug(f"MCP initialize skipped/failed: {e}")
    result = _rpc(endpoint, headers, "tools/list", {})
    if "error" in result and result.get("error"):
        raise RuntimeError(str(result["error"])[:200])
    tools = (result.get("result") or {}).get("tools", [])
    return tools


def call_tool(cfg: dict, tool_name: str, arguments: dict) -> str:
    """Call a tool on an MCP server (standard tools/call)."""
    result = _rpc(cfg["endpoint"], cfg.get("headers", {}), "tools/call", {
        "name": tool_name,
        "arguments": arguments,
    })
    if result.get("error"):
        return f"（MCP 工具 {tool_name} 出错: {str(result['error'])[:200]}）"
    content = (result.get("result") or {}).get("content", [])
    # MCP content is a list of {type, text|...}
    parts = []
    for item in content:
        if isinstance(item, dict):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
    return ("\n".join(parts) or json.dumps(result.get("result", {}), ensure_ascii=False))[:4000]


# ════════════════════════════════════════════════════════════════════════
# Schema generation + injection into the agent loop
# ════════════════════════════════════════════════════════════════════════

def _prefixed_name(server_name: str, tool_name: str) -> str:
    return f"{_PREFIX}{server_name}{_SEP}{tool_name}"


def _parse_prefixed(name: str) -> tuple[str, str] | None:
    if not name.startswith(_PREFIX):
        return None
    rest = name[len(_PREFIX):]
    if _SEP not in rest:
        return None
    server, tool = rest.split(_SEP, 1)
    return server, tool


def mcp_tool_to_schema(server_name: str, tool: dict) -> dict:
    """Convert one MCP tool descriptor into an OpenAI function schema."""
    return {
        "type": "function",
        "function": {
            "name": _prefixed_name(server_name, tool["name"]),
            "description": tool.get("description", "")[:1000],
            "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
        },
    }


def get_enabled_schemas() -> list[dict]:
    """All tools from all enabled MCP servers (from cache), as OpenAI schemas."""
    schemas = []
    for s in list_servers(only_enabled=True):
        for tool in s.get("tools_cache", []):
            try:
                schemas.append(mcp_tool_to_schema(s["name"], tool))
            except Exception:
                continue
    return schemas


def get_executors() -> dict:
    """Map prefixed tool name → executor closure for all enabled MCP tools."""
    executors = {}
    servers = {s["name"]: s for s in list_servers(only_enabled=True)}

    def _make(server_cfg, real_tool_name):
        def _exec(args: dict, ctx: dict | None = None) -> str:
            try:
                return call_tool(server_cfg, real_tool_name, args or {})
            except Exception as e:
                return f"（MCP 调用失败: {type(e).__name__}: {str(e)[:150]}）"
        return _exec

    for s in servers.values():
        for tool in s.get("tools_cache", []):
            pname = _prefixed_name(s["name"], tool["name"])
            executors[pname] = _make(s, tool["name"])
    return executors


def refresh_server(sid: str) -> dict:
    """Re-discover tools for a server and cache them. Returns {count, tools}."""
    cfg = next((s for s in list_servers() if s["id"] == sid), None)
    if not cfg:
        return {"error": "server not found"}
    t0 = time.time()
    tools = discover_tools(cfg)
    _cache_tools(sid, tools)
    return {"count": len(tools), "tools": tools, "elapsed_ms": round((time.time() - t0) * 1000)}


def test_server(cfg: dict) -> dict:
    """Test connectivity to an MCP server (used by admin 'test' button)."""
    t0 = time.time()
    try:
        tools = discover_tools(cfg)
        return {"ok": True, "tool_count": len(tools),
                "tools": [{"name": t.get("name"), "description": t.get("description", "")[:80]}
                          for t in tools[:20]],
                "elapsed_ms": round((time.time() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}",
                "elapsed_ms": round((time.time() - t0) * 1000)}
