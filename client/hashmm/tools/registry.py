"""Dynamic Tool Registry — extensible tool management for Agent Loop.

Wraps the existing _EXECUTORS pattern into a proper registry with:
  - Tool metadata (name, description, parameters schema)
  - CRUD via admin API
  - Custom tool plugins
  - OpenAI function calling format export

Usage:
    from hashmm.tools.registry import ToolRegistry

    # Register a new tool
    ToolRegistry.register(
        name="my_tool",
        description="Does something useful",
        parameters={"type": "object", "properties": {...}},
        handler=my_handler_fn,
    )

    # Get all tools as OpenAI format
    tools = ToolRegistry.get_openai_tools()

    # Execute a tool
    result = ToolRegistry.execute("my_tool", {"arg1": "val1"})
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from hashmm.utils import get_logger

logger = get_logger("hashmm.tools.registry")


@dataclass
class ToolDef:
    """A registered tool with metadata, schema, and handler."""
    name: str
    description: str
    parameters: dict                   # JSON Schema for arguments
    handler: Callable[..., str]        # Function(args_dict, ctx) → str
    category: str = "builtin"          # "builtin" | "plugin" | "custom"
    enabled: bool = True
    created_at: float = 0.0
    use_count: int = 0

    def to_openai_schema(self) -> dict:
        """Export as OpenAI function calling tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "category": self.category,
            "enabled": self.enabled,
            "use_count": self.use_count,
        }


class ToolRegistry:
    """Process-global dynamic tool registry.

    Thread-safe: tools are registered at startup or via admin API (rare writes).
    """
    _tools: dict[str, ToolDef] = {}

    @classmethod
    def register(
        cls,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable,
        category: str = "builtin",
    ) -> None:
        """Register a tool. Overwrites if name already exists."""
        cls._tools[name] = ToolDef(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            category=category,
            created_at=time.time(),
        )

    @classmethod
    def unregister(cls, name: str) -> bool:
        """Remove a tool by name."""
        if name in cls._tools:
            del cls._tools[name]
            return True
        return False

    @classmethod
    def get(cls, name: str) -> ToolDef | None:
        """Get a tool definition by name."""
        return cls._tools.get(name)

    @classmethod
    def list_tools(cls) -> list[dict]:
        """List all registered tools as dicts."""
        return [t.to_dict() for t in cls._tools.values()]

    @classmethod
    def get_openai_tools(cls, category: str | None = None) -> list[dict]:
        """Export all enabled tools as OpenAI function calling format."""
        tools = []
        for t in cls._tools.values():
            if not t.enabled:
                continue
            if category and t.category != category:
                continue
            tools.append(t.to_openai_schema())
        return tools

    @classmethod
    def execute(cls, name: str, args: dict, ctx: dict | None = None) -> str:
        """Execute a tool by name. Returns result string."""
        tool = cls._tools.get(name)
        if not tool:
            return f"Error: tool '{name}' not found"
        if not tool.enabled:
            return f"Error: tool '{name}' is disabled"

        tool.use_count += 1
        try:
            # Support both (args) and (args, ctx) signatures
            import inspect
            sig = inspect.signature(tool.handler)
            if len(sig.parameters) >= 2:
                return tool.handler(args, ctx or {})
            return tool.handler(args)
        except Exception as e:
            logger.error(f"Tool '{name}' execution error: {e}", exc_info=True)
            return f"Error executing {name}: {str(e)[:200]}"

    @classmethod
    def toggle(cls, name: str, enabled: bool) -> bool:
        """Enable or disable a tool."""
        tool = cls._tools.get(name)
        if tool:
            tool.enabled = enabled
            return True
        return False

    @classmethod
    def stats(cls) -> dict:
        """Return registry stats."""
        return {
            "total": len(cls._tools),
            "enabled": sum(1 for t in cls._tools.values() if t.enabled),
            "by_category": _count_by(cls._tools.values(), "category"),
            "top_used": sorted(
                [{"name": t.name, "count": t.use_count} for t in cls._tools.values()],
                key=lambda x: x["count"], reverse=True
            )[:10],
        }

    @classmethod
    def sync_from_legacy(cls) -> None:
        """Import tools from the legacy _EXECUTORS + TOOL_DEFS in tool_registry.py.

        Called once at startup to bridge old and new registry.
        """
        try:
            from hashmm.api.tool_registry import TOOL_DEFS, _EXECUTORS
            for td in TOOL_DEFS:
                func = td.get("function", {})
                name = func.get("name", "")
                if name and name in _EXECUTORS:
                    cls.register(
                        name=name,
                        description=func.get("description", ""),
                        parameters=func.get("parameters", {}),
                        handler=_EXECUTORS[name],
                        category="builtin",
                    )
            logger.info(f"[ToolRegistry] Synced {len(cls._tools)} tools from legacy registry")
        except Exception as e:
            logger.warning(f"[ToolRegistry] Legacy sync failed: {e}")


def _count_by(items, attr):
    counts = {}
    for item in items:
        key = getattr(item, attr, "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts
