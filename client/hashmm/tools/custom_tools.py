"""Custom API Tools — 用户可视化配置的外部 API 工具。

让用户在管理后台配置任意 REST API（天气、地图、订票、搜索……），配好后：
  1. 自动生成 OpenAI function-calling schema，注入 Agent Loop 的工具列表
  2. LLM 决定调用时，由通用 HTTP 执行器按配置发起请求
  3. 无需改代码——纯配置驱动（对标 CC 的可插拔工具 / MCP 思想）

存储在 SQLite/PG 的 custom_tools 表。一个配置示例（天气 API）：
{
  "name": "get_weather",
  "description": "查询指定城市的实时天气",
  "method": "GET",
  "url_template": "https://api.weather.com/v1/now?city={city}",
  "headers": {"Authorization": "Bearer xxx"},
  "params_schema": [
    {"name": "city", "type": "string", "description": "城市名", "required": true, "location": "path"}
  ],
  "response_path": "data.now",     # 可选：只取响应里的某个字段
  "enabled": true
}
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.tools.custom")

# Safety: block requests to internal/metadata addresses (SSRF protection)
_BLOCKED_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254",  # cloud metadata
    "::1", "metadata.google.internal",
}
_BLOCKED_PREFIXES = ("10.", "192.168.", "172.16.", "172.17.", "172.18.",
                     "172.19.", "172.2", "172.30.", "172.31.")


# ════════════════════════════════════════════════════════════════════════
# DB layer
# ════════════════════════════════════════════════════════════════════════

def _ensure_table():
    from hashmm.api import database as db
    with db._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS custom_tools (
                id            TEXT PRIMARY KEY,
                name          TEXT UNIQUE NOT NULL,
                description   TEXT DEFAULT '',
                method        TEXT DEFAULT 'GET',
                url_template  TEXT NOT NULL,
                headers       TEXT DEFAULT '{}',
                params_schema TEXT DEFAULT '[]',
                body_template TEXT DEFAULT '',
                response_path TEXT DEFAULT '',
                enabled       INTEGER DEFAULT 1,
                created_by    TEXT DEFAULT 'admin',
                created_at    REAL DEFAULT (strftime('%s','now')),
                call_count    INTEGER DEFAULT 0
            )
        """)


def create_tool(cfg: dict) -> str:
    _ensure_table()
    from hashmm.api import database as db
    tid = uuid.uuid4().hex[:12]
    with db._conn() as c:
        c.execute(
            """INSERT INTO custom_tools
               (id,name,description,method,url_template,headers,params_schema,
                body_template,response_path,enabled,created_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, cfg["name"], cfg.get("description", ""),
             cfg.get("method", "GET").upper(), cfg["url_template"],
             json.dumps(cfg.get("headers", {}), ensure_ascii=False),
             json.dumps(cfg.get("params_schema", []), ensure_ascii=False),
             cfg.get("body_template", ""), cfg.get("response_path", ""),
             1 if cfg.get("enabled", True) else 0, cfg.get("created_by", "admin")),
        )
    return tid


def update_tool(tool_id: str, cfg: dict) -> bool:
    _ensure_table()
    from hashmm.api import database as db
    with db._conn() as c:
        c.execute(
            """UPDATE custom_tools SET
               name=?, description=?, method=?, url_template=?, headers=?,
               params_schema=?, body_template=?, response_path=?, enabled=?
               WHERE id=?""",
            (cfg["name"], cfg.get("description", ""), cfg.get("method", "GET").upper(),
             cfg["url_template"], json.dumps(cfg.get("headers", {}), ensure_ascii=False),
             json.dumps(cfg.get("params_schema", []), ensure_ascii=False),
             cfg.get("body_template", ""), cfg.get("response_path", ""),
             1 if cfg.get("enabled", True) else 0, tool_id),
        )
    return True


def delete_tool(tool_id: str) -> bool:
    _ensure_table()
    from hashmm.api import database as db
    with db._conn() as c:
        c.execute("DELETE FROM custom_tools WHERE id=?", (tool_id,))
    return True


def list_tools(only_enabled: bool = False) -> list[dict]:
    _ensure_table()
    from hashmm.api import database as db
    with db._conn() as c:
        q = "SELECT * FROM custom_tools"
        if only_enabled:
            q += " WHERE enabled=1"
        q += " ORDER BY created_at DESC"
        rows = c.execute(q).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["headers"] = json.loads(d.get("headers") or "{}")
        d["params_schema"] = json.loads(d.get("params_schema") or "[]")
        d["enabled"] = bool(d.get("enabled", 0))
        # never leak full auth headers to the UI list
        out.append(d)
    return out


def get_tool(tool_id: str) -> dict | None:
    for t in list_tools():
        if t["id"] == tool_id:
            return t
    return None


def _incr_call_count(name: str):
    try:
        from hashmm.api import database as db
        with db._conn() as c:
            c.execute("UPDATE custom_tools SET call_count=call_count+1 WHERE name=?", (name,))
    except Exception as _e:
        log_suppressed(logger, _e)


# ════════════════════════════════════════════════════════════════════════
# Schema generation — turn a config into an OpenAI function-calling schema
# ════════════════════════════════════════════════════════════════════════

def tool_to_schema(cfg: dict) -> dict:
    """Convert a stored custom-tool config into an OpenAI tool schema."""
    props = {}
    required = []
    for p in cfg.get("params_schema", []):
        props[p["name"]] = {
            "type": p.get("type", "string"),
            "description": p.get("description", ""),
        }
        if p.get("required"):
            required.append(p["name"])
    return {
        "type": "function",
        "function": {
            "name": cfg["name"],
            "description": cfg.get("description", ""),
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


def get_enabled_schemas() -> list[dict]:
    """All enabled custom tools as OpenAI schemas (for injecting into the loop)."""
    return [tool_to_schema(t) for t in list_tools(only_enabled=True)]


# ════════════════════════════════════════════════════════════════════════
# Generic HTTP executor — calls any configured API
# ════════════════════════════════════════════════════════════════════════

def _is_blocked_url(url: str) -> bool:
    """SSRF guard: refuse internal/metadata hosts and malformed URLs."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        scheme = (parsed.scheme or "").lower()
    except Exception:
        return True
    # Malformed / non-http(s) / no host → block (safer default)
    if scheme not in ("http", "https") or not host:
        return True
    if host in _BLOCKED_HOSTS:
        return True
    if any(host.startswith(p) for p in _BLOCKED_PREFIXES):
        return True
    return False


def _fill_template(template: str, args: dict) -> str:
    """Replace {key} placeholders with url-encoded arg values."""
    from urllib.parse import quote
    out = template
    for k, v in args.items():
        out = out.replace("{" + k + "}", quote(str(v), safe=""))
    return out


def _extract_path(data: Any, path: str) -> Any:
    """Walk a dotted path like 'data.now.temp' into a parsed JSON object."""
    if not path:
        return data
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return data  # path not found → return whole thing
    return cur


def make_executor(cfg: dict):
    """Build an executor closure for one custom tool config.

    The executor signature matches the registry: (args: dict, ctx: dict) -> str
    """
    def _exec(args: dict, ctx: dict | None = None) -> str:
        import httpx

        # 1. URL (path params filled from args)
        url = _fill_template(cfg["url_template"], args)
        if _is_blocked_url(url):
            return f"（拒绝：{url} 指向内网/元数据地址，出于安全不允许）"

        # 2. Query params = args not consumed by the URL template and marked query
        query_params = {}
        body_args = {}
        path_keys = set()
        import re as _re
        for m in _re.finditer(r"\{(\w+)\}", cfg["url_template"]):
            path_keys.add(m.group(1))
        for p in cfg.get("params_schema", []):
            name = p["name"]
            if name in path_keys:
                continue
            if name not in args:
                continue
            loc = p.get("location", "query")
            if loc == "query":
                query_params[name] = args[name]
            elif loc == "body":
                body_args[name] = args[name]

        method = cfg.get("method", "GET").upper()
        headers = dict(cfg.get("headers", {}))

        # 3. Body
        body = None
        if cfg.get("body_template"):
            body = _fill_template(cfg["body_template"], args)
            headers.setdefault("Content-Type", "application/json")
        elif body_args:
            body = json.dumps(body_args, ensure_ascii=False)
            headers.setdefault("Content-Type", "application/json")

        # 4. Call
        try:
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                resp = client.request(
                    method, url, params=query_params or None,
                    content=body.encode("utf-8") if body else None,
                    headers=headers,
                )
            _incr_call_count(cfg["name"])
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                data = resp.json()
                data = _extract_path(data, cfg.get("response_path", ""))
                text = json.dumps(data, ensure_ascii=False)
            else:
                text = resp.text
            if resp.status_code >= 400:
                return f"（API 返回 {resp.status_code}）{text[:1500]}"
            return text[:4000]
        except Exception as e:
            return f"（调用 {cfg['name']} 失败: {type(e).__name__}: {str(e)[:150]}）"

    return _exec


def test_tool(cfg: dict, test_args: dict) -> dict:
    """Run a one-off test of a config (used by the admin 'test' button)."""
    executor = make_executor(cfg)
    t0 = time.time()
    result = executor(test_args, {})
    return {"result": result, "elapsed_ms": round((time.time() - t0) * 1000)}
