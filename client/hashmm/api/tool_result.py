"""Structured Tool Results v27 — typed results instead of raw strings.

Every tool returns a ToolResult with:
  - success: bool
  - content: str (main output)
  - files: list of created files
  - metrics: dict (execution time, size, etc.)
  - error: str | None
  - suggestions: list[str] (what to do next)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import json


@dataclass
class ToolResult:
    success: bool
    content: str
    files: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    error: str | None = None
    suggestions: list[str] = field(default_factory=list)

    def to_str(self) -> str:
        """Convert to string for LLM context (backward compatible)."""
        if self.success:
            parts = [f"✅ {self.content}"]
            if self.files:
                parts.append(f"文件: {', '.join(f.get('filename', '?') for f in self.files)}")
            return "\n".join(parts)
        return f"❌ {self.error or self.content}"

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "content": self.content[:500],
            "files": self.files,
            "metrics": self.metrics,
            "error": self.error,
        }


def parse_tool_result(raw: str) -> ToolResult:
    """Parse legacy string result into ToolResult."""
    success = not any(k in raw for k in ["Error", "❌", "错误", "失败"])
    files = []
    
    # Extract filenames
    import re
    for m in re.finditer(r'文件\s+(\S+)\s+已创建', raw):
        files.append({"filename": m.group(1)})
    for m in re.finditer(r'CHART_SAVED:(\S+)', raw):
        files.append({"filename": m.group(1).split("/")[-1], "type": "image"})
    
    return ToolResult(
        success=success,
        content=raw[:2000],
        files=files,
        error=raw[:500] if not success else None,
    )
