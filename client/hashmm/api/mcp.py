"""MCP (Model Context Protocol) compatible tool interface v29.

Standardizes tool definitions and execution to be compatible
with the MCP protocol used by Claude Code and other agents.

Each tool has:
  - name: unique identifier
  - description: what it does
  - input_schema: JSON Schema for parameters
  - execute(params, context) -> result
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable
import json


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict
    executor: Callable
    category: str = "general"
    requires_approval: bool = False  # Dangerous tools need user confirmation


class MCPToolRegistry:
    """MCP-compatible tool registry."""
    
    def __init__(self):
        self._tools: dict[str, MCPTool] = {}
    
    def register(self, tool: MCPTool):
        self._tools[tool.name] = tool
    
    def get(self, name: str) -> MCPTool | None:
        return self._tools.get(name)
    
    def list_tools(self) -> list[dict]:
        """List tools in MCP format."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
                "category": t.category,
            }
            for t in self._tools.values()
        ]
    
    def to_openai_tools(self) -> list[dict]:
        """Convert to OpenAI function calling format (for DeepSeek)."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in self._tools.values()
        ]
    
    def execute(self, name: str, params: dict, context: dict) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found"
        try:
            result = tool.executor(params, context)
            return str(result) if result else "(no output)"
        except Exception as e:
            return f"Error: {repr(e)[:300]}"
    
    def get_by_category(self, category: str) -> list[MCPTool]:
        return [t for t in self._tools.values() if t.category == category]


# Global registry
_mcp_registry = MCPToolRegistry()

def get_mcp_registry() -> MCPToolRegistry:
    return _mcp_registry
