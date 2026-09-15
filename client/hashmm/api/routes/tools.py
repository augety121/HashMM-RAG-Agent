"""Tool management routes — list, toggle, stats."""
from __future__ import annotations
from hashmm.utils import get_logger as _get_logger, log_suppressed
_obs_logger = _get_logger(__name__)

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from hashmm.api.auth import require_admin

router = APIRouter(prefix="/api/admin/tools", tags=["tools"])


@router.get("")
async def list_tools(request: Request):
    """List all registered tools."""
    from hashmm.tools.registry import ToolRegistry
    return {"tools": ToolRegistry.list_tools()}


@router.get("/stats")
async def tool_stats(request: Request):
    """Tool usage statistics."""
    require_admin(request)
    from hashmm.tools.registry import ToolRegistry
    return ToolRegistry.stats()


class ToggleRequest(BaseModel):
    enabled: bool


@router.patch("/{tool_name}")
async def toggle_tool(tool_name: str, req: ToggleRequest, request: Request):
    """Enable or disable a tool."""
    require_admin(request)
    from hashmm.tools.registry import ToolRegistry
    ok = ToolRegistry.toggle(tool_name, req.enabled)
    if not ok:
        raise HTTPException(404, f"Tool '{tool_name}' not found")
    return {"ok": True, "name": tool_name, "enabled": req.enabled}


@router.get("/openai-schema")
async def openai_schema(request: Request):
    """Export tools in OpenAI function calling format (for debugging)."""
    from hashmm.tools.registry import ToolRegistry
    return {"tools": ToolRegistry.get_openai_tools()}


# ════════════════════════════════════════════════════════════════════════
# v14 Phase 3: Custom API tools — user-configured external APIs
# ════════════════════════════════════════════════════════════════════════

class ParamSchema(BaseModel):
    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    location: str = "query"   # query | path | body


class CustomToolConfig(BaseModel):
    name: str
    description: str = ""
    method: str = "GET"
    url_template: str
    headers: dict = {}
    params_schema: list[ParamSchema] = []
    body_template: str = ""
    response_path: str = ""
    enabled: bool = True


class TestToolRequest(BaseModel):
    config: CustomToolConfig
    test_args: dict = {}


def _cfg_dict(cfg: CustomToolConfig) -> dict:
    d = cfg.model_dump()
    d["params_schema"] = [p.model_dump() if hasattr(p, "model_dump") else p
                          for p in d.get("params_schema", [])]
    return d


@router.get("/custom")
async def list_custom_tools(request: Request):
    """List all user-configured custom API tools."""
    require_admin(request)
    from hashmm.tools import custom_tools
    return {"tools": custom_tools.list_tools()}


@router.post("/custom")
async def create_custom_tool(cfg: CustomToolConfig, request: Request):
    """Create a new custom API tool."""
    require_admin(request)
    from hashmm.tools import custom_tools
    import re
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,40}$", cfg.name):
        raise HTTPException(400, "工具名只能是字母/数字/下划线，以字母开头")
    # v15: reserve built-in + core tool names to avoid shadowing
    _reserved = {"get_weather", "get_datetime", "calculator", "web_search",
                 "kb_search", "kg_query", "create_document", "create_file",
                 "execute_code", "fetch_url", "spawn_worker"}
    if cfg.name in _reserved:
        raise HTTPException(400, f"工具名 '{cfg.name}' 是内置工具保留名，请换一个")
    try:
        tid = custom_tools.create_tool(_cfg_dict(cfg))
    except Exception as e:
        raise HTTPException(400, f"创建失败（可能重名）: {str(e)[:120]}")
    return {"ok": True, "id": tid}


@router.put("/custom/{tool_id}")
async def update_custom_tool(tool_id: str, cfg: CustomToolConfig, request: Request):
    """Update an existing custom API tool."""
    require_admin(request)
    from hashmm.tools import custom_tools
    custom_tools.update_tool(tool_id, _cfg_dict(cfg))
    return {"ok": True}


@router.delete("/custom/{tool_id}")
async def delete_custom_tool(tool_id: str, request: Request):
    """Delete a custom API tool."""
    require_admin(request)
    from hashmm.tools import custom_tools
    custom_tools.delete_tool(tool_id)
    return {"ok": True}


@router.post("/custom/test")
async def test_custom_tool(req: TestToolRequest, request: Request):
    """Run a one-off test of a custom tool config without saving it."""
    require_admin(request)
    from hashmm.tools import custom_tools
    return custom_tools.test_tool(_cfg_dict(req.config), req.test_args)


# ════════════════════════════════════════════════════════════════════════
# v14 Phase 4: MCP servers — connect external MCP servers (a batch of tools)
# ════════════════════════════════════════════════════════════════════════

class MCPServerConfig(BaseModel):
    name: str
    transport: str = "http"        # http | sse
    endpoint: str
    headers: dict = {}
    enabled: bool = True


@router.get("/mcp")
async def list_mcp_servers(request: Request):
    """List configured MCP servers (with cached tool lists)."""
    require_admin(request)
    from hashmm.tools import mcp_client
    return {"servers": mcp_client.list_servers(redact_headers=True)}


@router.post("/mcp")
async def create_mcp_server(cfg: MCPServerConfig, request: Request):
    """Add an MCP server, then auto-discover its tools."""
    require_admin(request)
    from hashmm.tools import mcp_client
    import re
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,30}$", cfg.name):
        raise HTTPException(400, "服务器名只能是字母/数字/下划线，以字母开头")
    try:
        sid = mcp_client.create_server(cfg.model_dump())
    except Exception as e:
        raise HTTPException(400, f"创建失败（可能重名）: {str(e)[:120]}")
    # Best-effort discovery so tools are usable immediately
    discovered = 0
    discovery_error = ""
    try:
        r = mcp_client.refresh_server(sid)
        discovered = r.get("count", 0)
    except Exception as _e:
        log_suppressed(_obs_logger, _e)
        discovery_error = f"{type(_e).__name__}: {str(_e)[:180]}"
    return {"ok": True, "id": sid, "discovered": discovered,
            "status": "ready" if not discovery_error else "error",
            "discovery_error": discovery_error}


@router.put("/mcp/{sid}")
async def update_mcp_server(sid: str, cfg: MCPServerConfig, request: Request):
    require_admin(request)
    from hashmm.tools import mcp_client
    if not mcp_client.update_server(sid, cfg.model_dump()):
        raise HTTPException(404, "MCP 服务器不存在")
    return {"ok": True}


@router.delete("/mcp/{sid}")
async def delete_mcp_server(sid: str, request: Request):
    require_admin(request)
    from hashmm.tools import mcp_client
    if not mcp_client.delete_server(sid):
        raise HTTPException(404, "MCP 服务器不存在")
    return {"ok": True}


@router.post("/mcp/{sid}/refresh")
async def refresh_mcp_server(sid: str, request: Request):
    """Re-discover tools from an MCP server and cache them."""
    require_admin(request)
    from hashmm.tools import mcp_client
    try:
        result = mcp_client.refresh_server(sid)
    except Exception as exc:
        raise HTTPException(502, f"MCP 握手或工具发现失败: {type(exc).__name__}: {str(exc)[:160]}")
    if result.get("error") == "server not found":
        raise HTTPException(404, "MCP 服务器不存在")
    return result


@router.post("/mcp/test")
async def test_mcp_server(cfg: MCPServerConfig, request: Request):
    """Test connectivity to an MCP server without saving."""
    require_admin(request)
    from hashmm.tools import mcp_client
    return mcp_client.test_server(cfg.model_dump())
