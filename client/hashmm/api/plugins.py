"""Plugin System v18 — extensible skill architecture.

Inspired by Hermes Agent's SKILL.md pattern.
Each plugin is a directory with:
  - SKILL.md: description + capabilities
  - __init__.py: tool registration
  - tools.py: tool implementations

Plugins can be:
  - Built-in (shipped with the system)
  - User-installed (uploaded or pip installed)
  - Remote (MCP servers, API integrations)

Plugin lifecycle:
  1. Discovery: scan plugins/ directory
  2. Loading: import + validate + register tools
  3. Execution: SmartAgent calls plugin tools via registry
  4. Unloading: cleanup on shutdown
"""
from __future__ import annotations
import importlib, json, logging, os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("hashmm.plugins")


@dataclass
class PluginInfo:
    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    capabilities: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    enabled: bool = True
    path: Path | None = None


@dataclass
class PluginTool:
    """A tool provided by a plugin."""
    name: str
    description: str
    parameters: dict
    executor: Callable
    plugin_name: str


class PluginManager:
    """Manages plugin discovery, loading, and execution."""

    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = Path(plugins_dir)
        self.plugins: dict[str, PluginInfo] = {}
        self.tools: dict[str, PluginTool] = {}

    def discover(self) -> list[PluginInfo]:
        """Scan plugins directory for available plugins."""
        if not self.plugins_dir.exists():
            return []

        found = []
        for d in sorted(self.plugins_dir.iterdir()):
            if not d.is_dir() or d.name.startswith('_'):
                continue

            skill_md = d / "SKILL.md"
            init_py = d / "__init__.py"

            info = PluginInfo(name=d.name, path=d)

            # Read SKILL.md for metadata
            if skill_md.exists():
                content = skill_md.read_text(encoding='utf-8', errors='replace')
                # Parse simple frontmatter
                for line in content.splitlines()[:20]:
                    if line.startswith("# "):
                        info.description = line[2:].strip()
                    elif line.startswith("version:"):
                        info.version = line.split(":", 1)[1].strip()
                    elif line.startswith("author:"):
                        info.author = line.split(":", 1)[1].strip()

            # Check if loadable
            if init_py.exists():
                info.enabled = True

            found.append(info)
            self.plugins[info.name] = info

        return found

    def load(self, name: str) -> bool:
        """Load a plugin and register its tools."""
        info = self.plugins.get(name)
        if not info or not info.path:
            return False

        try:
            # Add plugin dir to path temporarily
            import sys
            plugin_path = str(info.path)
            if plugin_path not in sys.path:
                sys.path.insert(0, plugin_path)

            # Import the plugin module
            spec = importlib.util.spec_from_file_location(
                f"plugin_{name}", info.path / "__init__.py"
            )
            if not spec or not spec.loader:
                return False

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Register tools from the module
            if hasattr(module, 'register_tools'):
                tools = module.register_tools()
                for tool in tools:
                    self.tools[tool["name"]] = PluginTool(
                        name=tool["name"],
                        description=tool.get("description", ""),
                        parameters=tool.get("parameters", {}),
                        executor=tool["executor"],
                        plugin_name=name,
                    )
                    info.tools.append(tool["name"])

            logger.info(f"Plugin loaded: {name} ({len(info.tools)} tools)")
            return True

        except Exception as e:
            logger.error(f"Failed to load plugin {name}: {e}")
            return False

    def load_all(self):
        """Discover and load all available plugins."""
        plugins = self.discover()
        for p in plugins:
            if p.enabled:
                self.load(p.name)

    def execute_tool(self, name: str, args: dict, ctx: dict) -> str:
        """Execute a plugin tool."""
        tool = self.tools.get(name)
        if not tool:
            return f"Error: Plugin tool '{name}' not found"
        try:
            return tool.executor(args, ctx)
        except Exception as e:
            return f"Error: Plugin tool failed: {repr(e)[:200]}"

    def get_tool_definitions(self) -> list[dict]:
        """Get OpenAI-format tool definitions for all plugin tools."""
        defs = []
        for tool in self.tools.values():
            defs.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": f"[Plugin: {tool.plugin_name}] {tool.description}",
                    "parameters": tool.parameters,
                },
            })
        return defs

    def list_plugins(self) -> list[dict]:
        """List all plugins with their status."""
        return [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "enabled": p.enabled,
                "tools": p.tools,
            }
            for p in self.plugins.values()
        ]


# Global instance
_plugin_manager = PluginManager()

def get_plugin_manager() -> PluginManager:
    return _plugin_manager
