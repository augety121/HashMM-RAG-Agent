"""v17 Phase 82 — connector schema → agent tools (轴 F).

Turn an external system's schema into agent-callable tool definitions, in the
exact function-calling shape the tool registry already uses
(``{type:"function", function:{name, description, parameters:<JSON Schema>}}``),
so connecting a REST API or a database to the agent is "point at the schema",
not "hand-write N tools".

- ``openapi_to_tools(spec)`` — one tool per OpenAPI operation (path+query+header
  params + JSON request body merged into the parameter schema).
- ``db_schema_to_tools(tables)`` — a ``query_<table>`` tool per table with an
  optional equality filter per column + a limit.
- ``generate_tools(kind, spec)`` — dispatch.

Pure transformation (no network/DB), **never raises** (a malformed operation is
skipped, not fatal). Executing the generated tools against the live API/DB is the
caller's job — this only produces the definitions.
"""
from __future__ import annotations

import re
from typing import Any

from hashmm.utils import get_logger

logger = get_logger(__name__)

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")

# SQL / language types → JSON Schema types.
_TYPE_MAP = {
    "int": "integer", "integer": "integer", "bigint": "integer", "smallint": "integer",
    "serial": "integer", "bigserial": "integer",
    "float": "number", "double": "number", "real": "number", "numeric": "number",
    "decimal": "number", "money": "number",
    "bool": "boolean", "boolean": "boolean",
    "json": "object", "jsonb": "object",
    "array": "array",
    "text": "string", "varchar": "string", "char": "string", "str": "string",
    "uuid": "string", "date": "string", "timestamp": "string", "datetime": "string",
    "time": "string",
}


def _json_type(t: Any) -> str:
    if not t:
        return "string"
    base = str(t).strip().lower().split("(")[0].split(" ")[0]  # varchar(255) → varchar
    return _TYPE_MAP.get(base, "string" if base not in ("number", "object", "array", "boolean") else base)


def sanitize_name(s: str, fallback: str = "tool") -> str:
    """A valid, readable tool/function name."""
    s = re.sub(r"[^0-9A-Za-z_]+", "_", str(s or "")).strip("_").lower()
    if not s:
        s = fallback
    if s[0].isdigit():
        s = f"_{s}"
    return s[:64]


def _resolve_ref(spec: dict, schema: dict) -> dict:
    """Shallow $ref resolution against components/schemas (best-effort)."""
    if isinstance(schema, dict) and "$ref" in schema:
        ref = schema["$ref"]
        if isinstance(ref, str) and ref.startswith("#/"):
            node: Any = spec
            for part in ref[2:].split("/"):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    return {}
            return node if isinstance(node, dict) else {}
    return schema if isinstance(schema, dict) else {}


def _empty_params() -> dict:
    return {"type": "object", "properties": {}, "required": []}


def openapi_to_tools(spec: dict, *, name_prefix: str = "") -> list[dict]:
    """Generate one tool per OpenAPI 3 operation. Skips malformed operations."""
    tools: list[dict] = []
    try:
        paths = (spec or {}).get("paths", {})
        if not isinstance(paths, dict):
            return tools
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method in _HTTP_METHODS:
                op = methods.get(method)
                if not isinstance(op, dict):
                    continue
                try:
                    tools.append(_op_to_tool(spec, path, method, op, name_prefix))
                except Exception as e:
                    logger.debug(f"skip {method} {path}: {e}")
    except Exception as e:
        logger.debug(f"openapi_to_tools failed: {e}")
    return tools


def _op_to_tool(spec: dict, path: str, method: str, op: dict, name_prefix: str) -> dict:
    raw_name = op.get("operationId") or f"{method}_{path}"
    name = sanitize_name(f"{name_prefix}{raw_name}" if name_prefix else raw_name)
    desc = (op.get("summary") or op.get("description") or f"{method.upper()} {path}").strip()

    params = _empty_params()
    props, required = params["properties"], params["required"]

    # path / query / header parameters
    for p in op.get("parameters", []) or []:
        if not isinstance(p, dict) or "name" not in p:
            continue
        pschema = _resolve_ref(spec, p.get("schema", {}) or {})
        props[p["name"]] = {
            "type": _json_type(pschema.get("type", "string")),
            "description": (p.get("description") or f"{p.get('in', 'query')} parameter").strip(),
        }
        if p.get("required"):
            required.append(p["name"])

    # JSON request body → merge object properties
    body = op.get("requestBody", {}) or {}
    content = (body.get("content", {}) or {}).get("application/json", {}) or {}
    bschema = _resolve_ref(spec, content.get("schema", {}) or {})
    if bschema.get("type") == "object" or "properties" in bschema:
        for pname, pdef in (bschema.get("properties", {}) or {}).items():
            pdef = _resolve_ref(spec, pdef)
            props[pname] = {
                "type": _json_type(pdef.get("type", "string")),
                "description": (pdef.get("description") or "").strip(),
            }
        for r in bschema.get("required", []) or []:
            if r not in required:
                required.append(r)

    return {"type": "function",
            "function": {"name": name, "description": desc, "parameters": params}}


def db_schema_to_tools(tables: list[dict], *, max_limit_default: int = 50) -> list[dict]:
    """Generate a query_<table> tool per table (optional equality filter per
    column + a limit). ``tables`` = [{name, columns:[{name,type}], description?}]."""
    tools: list[dict] = []
    for t in tables or []:
        try:
            if not isinstance(t, dict) or not t.get("name"):
                continue
            tname = t["name"]
            cols = t.get("columns", []) or []
            params = _empty_params()
            for c in cols:
                if not isinstance(c, dict) or not c.get("name"):
                    continue
                params["properties"][c["name"]] = {
                    "type": _json_type(c.get("type", "string")),
                    "description": f"按 {c['name']} 精确过滤（可选）",
                }
            params["properties"]["limit"] = {
                "type": "integer",
                "description": f"返回行数上限（默认 {max_limit_default}）",
            }
            desc = (t.get("description")
                    or f"查询数据表 {tname}：按任意列等值过滤，返回匹配行。").strip()
            tools.append({"type": "function",
                          "function": {"name": sanitize_name(f"query_{tname}"),
                                       "description": desc, "parameters": params}})
        except Exception as e:
            logger.debug(f"skip table {t}: {e}")
    return tools


def generate_tools(kind: str, spec: Any) -> list[dict]:
    """Dispatch by connector kind: 'openapi' | 'db'."""
    k = (kind or "").strip().lower()
    if k in ("openapi", "swagger", "rest"):
        return openapi_to_tools(spec if isinstance(spec, dict) else {})
    if k in ("db", "sql", "database"):
        return db_schema_to_tools(spec if isinstance(spec, list) else [])
    logger.debug(f"unknown connector kind: {kind}")
    return []
