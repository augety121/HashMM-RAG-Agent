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
    evidence_refs: list[str] = field(default_factory=list)
    receipt: dict = field(default_factory=dict)

    def to_str(self) -> str:
        """Convert to string for LLM context (backward compatible)."""
        if self.success:
            parts = [f"完成 · {self.content}"]   # V203: 去 emoji；旧消息 ✅ 由各检测端兼容
            if self.files:
                parts.append(f"文件: {', '.join(f.get('filename', '?') for f in self.files)}")
            return "\n".join(parts)
        return f"失败 · {self.error or self.content}"

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "content": self.content[:500],
            "files": self.files,
            "metrics": self.metrics,
            "error": self.error,
            "suggestions": self.suggestions,
            "evidence_refs": self.evidence_refs,
            "receipt": self.receipt,
        }


def parse_tool_result(raw: Any) -> ToolResult:
    """Normalize old prose and new structured executor results.

    Run state must not depend on decorative characters inside tool prose.
    Older callers did that and classified a real ``执行成功`` result as an
    error, which also left the desktop run view without a stable status.
    """
    if isinstance(raw, ToolResult):
        return raw
    if isinstance(raw, dict):
        success_value = raw.get("success")
        status_value = str(raw.get("status") or "").lower()
        if isinstance(success_value, bool):
            success = success_value
        elif status_value:
            success = status_value in {"ok", "done", "success", "completed"}
        else:
            success = not bool(raw.get("error"))
        content = str(raw.get("content") or raw.get("output") or "")
        error = str(raw.get("error") or "") or None
        return ToolResult(
            success=success,
            content=content or (error or ""),
            files=list(raw.get("files") or []),
            metrics=dict(raw.get("metrics") or {}),
            error=error if not success else None,
            suggestions=list(raw.get("suggestions") or []),
            evidence_refs=[
                str(ref)[:160] for ref in list(raw.get("evidence_refs") or [])[:32]
                if str(ref or "").strip()
            ],
            receipt=dict(
                raw.get("receipt") or raw.get("_execution_receipt") or {}
            ) if isinstance(
                raw.get("receipt") or raw.get("_execution_receipt") or {}, dict
            ) else {},
        )

    raw = "" if raw is None else str(raw)
    lowered = raw.strip().lower()
    failure_prefixes = ("error", "失败", "错误", "denied", "拒绝")
    success = not (
        lowered.startswith(failure_prefixes)
        or any(k in raw for k in ["❌", "Traceback (most recent call last)"])
    )
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


def tool_result_success(raw: Any) -> bool:
    """Return the authoritative success flag for any executor generation."""
    return parse_tool_result(raw).success


def tool_result_content(raw: Any) -> str:
    """Return user-facing output without transport/decorative markers."""
    result = parse_tool_result(raw)
    return result.content if result.success else (result.error or result.content)
