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

import hashlib
import json
import re
import time
import uuid
from urllib.parse import urlsplit
from typing import Any

from hashmm.utils import get_logger

logger = get_logger("hashmm.tools.mcp_client")

# Tool name separator (mirrors Claude Code's mcp__{server}__{tool} convention)
_SEP = "__"
_PREFIX = "mcp__"
_CLIENT_PROTOCOL_VERSION = "2025-11-25"
_MAX_TOOL_PAGES = 50
_MAX_DISCOVERED_TOOLS = 1000
_HOP_BY_HOP_HEADERS = {
    "connection", "content-length", "host", "transfer-encoding", "upgrade",
    "mcp-session-id",
}
_REDACTED = "<redacted>"


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
                created_at  REAL DEFAULT (strftime('%s','now')),
                last_status TEXT DEFAULT 'unknown',
                last_error  TEXT DEFAULT '',
                last_checked_at REAL DEFAULT 0,
                protocol_version TEXT DEFAULT '',
                server_info TEXT DEFAULT '{}',
                capabilities TEXT DEFAULT '{}'
            )
        """)
        columns = {str(row[1]) for row in c.execute("PRAGMA table_info(mcp_servers)").fetchall()}
        additions = {
            "last_status": "TEXT DEFAULT 'unknown'",
            "last_error": "TEXT DEFAULT ''",
            "last_checked_at": "REAL DEFAULT 0",
            "protocol_version": "TEXT DEFAULT ''",
            "server_info": "TEXT DEFAULT '{}'",
            "capabilities": "TEXT DEFAULT '{}'",
        }
        for name, ddl in additions.items():
            if name not in columns:
                c.execute(f"ALTER TABLE mcp_servers ADD COLUMN {name} {ddl}")


def create_server(cfg: dict) -> str:
    _ensure_table()
    from hashmm.api import database as db
    endpoint = _validate_endpoint(cfg.get("endpoint", ""))
    transport = str(cfg.get("transport", "http") or "http").lower()
    if transport not in {"http", "sse"}:
        raise ValueError("MCP transport 只支持 http/sse")
    _request_headers(cfg.get("headers", {}))  # validate before persistence
    sid = uuid.uuid4().hex[:12]
    with db._conn() as c:
        c.execute(
            """INSERT INTO mcp_servers (id,name,transport,endpoint,headers,enabled,created_by)
               VALUES (?,?,?,?,?,?,?)""",
            (sid, cfg["name"], transport, endpoint,
             json.dumps(cfg.get("headers", {}), ensure_ascii=False),
             1 if cfg.get("enabled", True) else 0, cfg.get("created_by", "admin")),
        )
    return sid


def update_server(sid: str, cfg: dict) -> bool:
    _ensure_table()
    from hashmm.api import database as db
    endpoint = _validate_endpoint(cfg.get("endpoint", ""))
    transport = str(cfg.get("transport", "http") or "http").lower()
    if transport not in {"http", "sse"}:
        raise ValueError("MCP transport 只支持 http/sse")
    incoming_headers = dict(cfg.get("headers", {}) or {})
    _request_headers(incoming_headers)
    with db._conn() as c:
        row = c.execute("SELECT headers FROM mcp_servers WHERE id=?", (sid,)).fetchone()
        if not row:
            return False
        try:
            existing_headers = json.loads(row[0] or "{}")
        except (TypeError, ValueError):
            existing_headers = {}
        for key, value in list(incoming_headers.items()):
            if value == _REDACTED and key in existing_headers:
                incoming_headers[key] = existing_headers[key]
        cur = c.execute(
            """UPDATE mcp_servers SET name=?, transport=?, endpoint=?, headers=?, enabled=?
               WHERE id=?""",
            (cfg["name"], transport, endpoint,
             json.dumps(incoming_headers, ensure_ascii=False),
             1 if cfg.get("enabled", True) else 0, sid),
        )
    return bool(cur.rowcount)


def delete_server(sid: str) -> bool:
    _ensure_table()
    from hashmm.api import database as db
    with db._conn() as c:
        cur = c.execute("DELETE FROM mcp_servers WHERE id=?", (sid,))
    return bool(cur.rowcount)


def list_servers(only_enabled: bool = False, *, redact_headers: bool = False) -> list[dict]:
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
        for key, fallback in (("headers", {}), ("tools_cache", []),
                              ("server_info", {}), ("capabilities", {})):
            try:
                value = json.loads(d.get(key) or json.dumps(fallback))
                d[key] = value if isinstance(value, type(fallback)) else fallback
            except (TypeError, ValueError):
                d[key] = fallback
        d["enabled"] = bool(d.get("enabled", 0))
        if redact_headers:
            d["headers"] = {str(key): _REDACTED for key in d["headers"]}
        out.append(d)
    return out


def _cache_tools(sid: str, tools: list[dict]):
    from hashmm.api import database as db
    with db._conn() as c:
        c.execute("UPDATE mcp_servers SET tools_cache=? WHERE id=?",
                  (json.dumps(tools, ensure_ascii=False), sid))


def _cache_health(sid: str, *, status: str, error: str = "",
                  protocol_version: str = "", server_info: dict | None = None,
                  capabilities: dict | None = None) -> None:
    if not sid:
        return
    from hashmm.api import database as db
    with db._conn() as c:
        c.execute(
            """UPDATE mcp_servers
               SET last_status=?, last_error=?, last_checked_at=?, protocol_version=?,
                   server_info=?, capabilities=? WHERE id=?""",
            (str(status)[:32], str(error)[:500], time.time(), str(protocol_version)[:40],
             json.dumps(server_info or {}, ensure_ascii=False),
             json.dumps(capabilities or {}, ensure_ascii=False), sid),
        )


# ════════════════════════════════════════════════════════════════════════
# JSON-RPC over HTTP — the MCP wire protocol
# ════════════════════════════════════════════════════════════════════════

def _validate_endpoint(endpoint: str) -> str:
    value = str(endpoint or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("MCP endpoint 只支持完整的 http/https URL")
    if parsed.username or parsed.password:
        raise ValueError("MCP endpoint 不允许在 URL 中携带用户名或密码")
    return value


def _request_headers(headers: dict | None) -> dict[str, str]:
    out = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    for raw_name, raw_value in (headers or {}).items():
        name = str(raw_name or "").strip()
        if not name or name.lower() in _HOP_BY_HOP_HEADERS:
            continue
        if "\r" in name or "\n" in name or "\r" in str(raw_value) or "\n" in str(raw_value):
            raise ValueError("MCP header 包含非法换行")
        out[name] = str(raw_value)
    return out


def _new_http_client(timeout: float):
    import httpx
    # Redirects are deliberately disabled: following an admin-supplied endpoint
    # to a different origin can forward custom credentials to an unintended host.
    return httpx.Client(timeout=timeout, follow_redirects=False)


def _sse_messages(text: str) -> list[dict]:
    """Parse complete SSE events, joining multi-line ``data:`` fields."""
    out: list[dict] = []
    data_lines: list[str] = []
    for raw in (text or "").splitlines() + [""]:
        line = raw.rstrip("\r")
        if line == "":
            if data_lines:
                try:
                    value = json.loads("\n".join(data_lines))
                    if isinstance(value, dict):
                        out.append(value)
                except json.JSONDecodeError:
                    pass
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    return out


class _HttpMcpSession:
    """One negotiated MCP Streamable-HTTP session.

    ``initialize`` and ``notifications/initialized`` run once, the server's
    ``Mcp-Session-Id`` is propagated to every later request, and every RPC is
    matched by id instead of accepting an unrelated SSE event.
    """

    def __init__(self, endpoint: str, headers: dict | None, timeout: float = 20.0):
        self.endpoint = _validate_endpoint(endpoint)
        self.headers = _request_headers(headers)
        self.client = _new_http_client(timeout)
        self.session_id = ""
        self.protocol_version = ""
        self.server_info: dict = {}
        self.capabilities: dict = {}

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def request(self, method: str, params: dict | None = None, *,
                notification: bool = False) -> dict:
        request_id = None if notification else uuid.uuid4().hex[:12]
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if request_id is not None:
            payload["id"] = request_id
        if params is not None:
            payload["params"] = params
        headers = dict(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        response = self.client.post(self.endpoint, json=payload, headers=headers)
        status = int(getattr(response, "status_code", 0) or 0)
        if 300 <= status < 400:
            raise RuntimeError("MCP endpoint 返回重定向，已拒绝跨地址转发凭据")
        response.raise_for_status()
        new_session = str(response.headers.get("Mcp-Session-Id", "") or "").strip()
        if new_session:
            self.session_id = new_session
        if notification and (status == 202 or not str(response.text or "").strip()):
            return {}
        ctype = str(response.headers.get("content-type", "") or "").lower()
        if "text/event-stream" in ctype:
            rows = _sse_messages(response.text)
            if notification:
                return rows[-1] if rows else {}
            match = next((row for row in rows if row.get("id") == request_id), None)
            if match is None:
                raise RuntimeError("MCP SSE 响应未包含当前 JSON-RPC id")
            return match
        try:
            body = response.json()
        except Exception as exc:
            raise RuntimeError("MCP 响应不是合法 JSON") from exc
        if not isinstance(body, dict):
            raise RuntimeError("MCP JSON-RPC 响应必须是对象")
        if not notification and body.get("id") != request_id:
            raise RuntimeError("MCP JSON-RPC 响应 id 不匹配")
        return body

    def initialize(self) -> dict:
        response = self.request("initialize", {
            "protocolVersion": _CLIENT_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "hashmm", "version": "17.0"},
        })
        if response.get("error"):
            raise RuntimeError(f"MCP initialize 失败: {str(response['error'])[:240]}")
        result = response.get("result") or {}
        if not isinstance(result, dict):
            raise RuntimeError("MCP initialize 缺少 result")
        self.protocol_version = str(result.get("protocolVersion") or "")
        if not self.protocol_version:
            raise RuntimeError("MCP initialize 未返回 protocolVersion")
        self.server_info = result.get("serverInfo") if isinstance(result.get("serverInfo"), dict) else {}
        self.capabilities = result.get("capabilities") if isinstance(result.get("capabilities"), dict) else {}
        self.request("notifications/initialized", notification=True)
        return result


def _rpc(endpoint: str, headers: dict, method: str, params: dict | None = None,
         timeout: float = 20.0) -> dict:
    """Compatibility helper for one fully negotiated MCP request."""
    with _HttpMcpSession(endpoint, headers, timeout) as session:
        session.initialize()
        return session.request(method, params or {})


def _discover(cfg: dict) -> tuple[list[dict], dict]:
    tools: list[dict] = []
    seen_cursors: set[str] = set()
    with _HttpMcpSession(cfg["endpoint"], cfg.get("headers", {})) as session:
        session.initialize()
        cursor = ""
        for _ in range(_MAX_TOOL_PAGES):
            params = {"cursor": cursor} if cursor else {}
            response = session.request("tools/list", params)
            if response.get("error"):
                raise RuntimeError(f"MCP tools/list 失败: {str(response['error'])[:240]}")
            result = response.get("result") or {}
            page = result.get("tools") or [] if isinstance(result, dict) else []
            if not isinstance(page, list):
                raise RuntimeError("MCP tools/list 的 tools 必须是数组")
            tools.extend(item for item in page if isinstance(item, dict))
            if len(tools) > _MAX_DISCOVERED_TOOLS:
                raise RuntimeError(f"MCP 工具数超过安全上限 {_MAX_DISCOVERED_TOOLS}")
            next_cursor = str(result.get("nextCursor") or "") if isinstance(result, dict) else ""
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise RuntimeError("MCP tools/list 返回了循环分页游标")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise RuntimeError(f"MCP tools/list 分页超过 {_MAX_TOOL_PAGES} 页")
        meta = {
            "protocol_version": session.protocol_version,
            "server_info": session.server_info,
            "capabilities": session.capabilities,
        }
    return tools, meta


def discover_tools(cfg: dict) -> list[dict]:
    """Negotiate one MCP session and discover every bounded tools/list page."""
    return _discover(cfg)[0]


def call_tool(cfg: dict, tool_name: str, arguments: dict) -> dict:
    """Call one MCP tool and preserve structured error/content evidence."""
    with _HttpMcpSession(cfg["endpoint"], cfg.get("headers", {})) as session:
        session.initialize()
        response = session.request("tools/call", {
            "name": tool_name,
            "arguments": arguments or {},
        })
    if response.get("error"):
        return {"status": "error", "message":
                f"MCP 工具 {tool_name} 出错: {str(response['error'])[:240]}"}
    result = response.get("result") or {}
    content = result.get("content", []) if isinstance(result, dict) else []
    parts: list[str] = []
    for item in content if isinstance(content, list) else []:
        if isinstance(item, dict):
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
    is_error = bool(result.get("isError")) if isinstance(result, dict) else False
    text = "\n".join(parts) or json.dumps(result, ensure_ascii=False)
    return {
        "status": "error" if is_error else "ok",
        "message": text[:12000],
        "mcp": {"protocol_version": session.protocol_version,
                "server": session.server_info.get("name", "")},
    }


# ════════════════════════════════════════════════════════════════════════
# Schema generation + injection into the agent loop
# ════════════════════════════════════════════════════════════════════════

def _prefixed_name(server_name: str, tool_name: str) -> str:
    raw = f"{_PREFIX}{server_name}{_SEP}{tool_name}"
    clean = re.sub(r"[^A-Za-z0-9_-]", "_", raw)
    if len(clean) <= 64:
        return clean
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{clean[:53]}_{digest}"


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
    tool_name = str(tool.get("name") or "").strip()
    if not tool_name:
        raise ValueError("MCP tool 缺少 name")
    input_schema = tool.get("inputSchema")
    if not isinstance(input_schema, dict):
        input_schema = {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": _prefixed_name(server_name, tool_name),
            "description": tool.get("description", "")[:1000],
            "parameters": input_schema,
        },
    }


def annotation_for_tool(tool: dict, *, title: str = "") -> dict:
    """Map MCP ToolAnnotations to HashMM's conservative runtime contract."""
    annotations = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    read_only = annotations.get("readOnlyHint") is True
    return {
        "read_only": read_only,
        "destructive": (annotations.get("destructiveHint") is not False) and not read_only,
        "idempotent": annotations.get("idempotentHint") is True,
        # MCP is an external trust boundary. Missing hints never mean safe.
        "open_world": (annotations.get("openWorldHint") is not False) or not read_only,
        "title": str(annotations.get("title") or title or tool.get("name") or "MCP 工具")[:120],
    }


def get_tool_annotation(prefixed_name: str) -> dict | None:
    for server in list_servers(only_enabled=True):
        for tool in server.get("tools_cache", []):
            if _prefixed_name(server["name"], str(tool.get("name") or "")) == prefixed_name:
                return annotation_for_tool(tool, title=f"{server['name']} / {tool.get('name', '')}")
    return None


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
        def _exec(args: dict, ctx: dict | None = None) -> dict:
            try:
                return call_tool(server_cfg, real_tool_name, args or {})
            except Exception as e:
                return {"status": "error",
                        "message": f"MCP 调用失败: {type(e).__name__}: {str(e)[:180]}"}
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
    try:
        tools, meta = _discover(cfg)
        _cache_tools(sid, tools)
        _cache_health(sid, status="ready", **meta)
        return {"ok": True, "count": len(tools), "tools": tools,
                "protocol_version": meta["protocol_version"],
                "server_info": meta["server_info"],
                "elapsed_ms": round((time.time() - t0) * 1000)}
    except Exception as exc:
        _cache_health(sid, status="error", error=f"{type(exc).__name__}: {str(exc)[:400]}")
        raise


def test_server(cfg: dict) -> dict:
    """Test connectivity to an MCP server (used by admin 'test' button)."""
    t0 = time.time()
    try:
        tools, meta = _discover(cfg)
        return {"ok": True, "tool_count": len(tools),
                "tools": [{"name": t.get("name"), "description": t.get("description", "")[:80]}
                          for t in tools[:20]],
                "protocol_version": meta["protocol_version"],
                "server_info": meta["server_info"],
                "capabilities": meta["capabilities"],
                "elapsed_ms": round((time.time() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}",
                "elapsed_ms": round((time.time() - t0) * 1000)}
