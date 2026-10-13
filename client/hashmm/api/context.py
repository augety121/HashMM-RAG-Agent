"""Context Manager v12 — smart context building for LLM.

Three-layer context:
  L1: User memory (cross-session, persistent)
  L2: Conversation context (workspace files, recent actions)
  L3: Request context (current query, file uploads)

Inspired by:
  - Ralph's CLAUDE.md pattern (project context file)
  - Claude Code's workspace awareness
  - Hermes Agent's skill context injection
"""
from __future__ import annotations
import json, os, re, time
from pathlib import Path
from typing import Any

from hashmm.api.database import CONV_FILES_ROOT  # 统一绝对路径锚点（避免双目录/404）


class ContextBuilder:
    """Build rich context for LLM from multiple sources."""

    def __init__(self, conv_id: str, user_id: str = "anonymous"):
        self.conv_id = conv_id
        self.user_id = user_id

    def build(self, history: list[dict], file_context: str = "",
              custom_prompt: str = "", db: Any = None, *,
              resource_scope: list[str] | tuple[str, ...] | None = None,
              include_user_memory: bool = True,
              include_recent_actions: bool = True) -> str:
        """Build complete context string for system prompt injection."""
        parts = []

        # L1: User memory
        if include_user_memory and db and hasattr(db, 'get_user_memories'):
            try:
                memories = db.get_user_memories(self.user_id)
                if memories:
                    parts.append(self._format_memories(memories))
            except Exception as _e:

                pass  # Silenced: see logs if needed
        project = self._project_brief(db)
        if project:
            parts.append(project)
        handoff = self._handoff_brief(db)
        if handoff:
            parts.append(handoff)
        workspace = self._scan_workspace(allowed_names=resource_scope)
        if workspace:
            parts.append(workspace)

        # L2: Recent actions summary
        actions = self._extract_recent_actions(history) if include_recent_actions else ""
        if actions:
            parts.append(actions)

        # L3: Custom prompt
        if custom_prompt:
            parts.append(f"**用户自定义指令：** {custom_prompt}")

        return "\n\n".join(parts) if parts else ""

    def _project_brief(self, db: Any) -> str:
        """Inject only the authenticated owner's explicit project brief.

        This is user-authored intent, but it still cannot grant tool or
        filesystem permissions.  Runtime approval and sandbox checks remain
        authoritative.
        """
        if not db or not hasattr(db, "get_conversation"):
            return ""
        try:
            conv = db.get_conversation(self.conv_id)
            if not conv or str(conv.get("user_id") or "") != str(self.user_id):
                return ""
            project_id = str(conv.get("project_id") or "").strip()
            if not project_id or not hasattr(db, "get_project_for_user"):
                return ""
            project = db.get_project_for_user(project_id, self.user_id)
            if not project:
                return ""
            criteria = [
                str(item).strip()[:240]
                for item in (project.get("success_criteria") or [])
                if str(item).strip()
            ][:12]
            lines = [
                "**当前项目目标书（由用户确认）：**",
                f"- 项目：{str(project.get('name') or '')[:120]}",
            ]
            if project.get("goal"):
                lines.append(f"- 目标：{str(project['goal'])[:2000]}")
            if project.get("deliverable"):
                lines.append(f"- 交付物：{str(project['deliverable'])[:1200]}")
            if project.get("custom_prompt"):
                lines.append(f"- 项目工作指引：{str(project['custom_prompt'])[:8000]}")
            if criteria:
                lines.append("- 验收标准：")
                lines.extend(f"  - {item}" for item in criteria)
            mode = str(project.get("permission_mode") or "ask")
            mode_text = {
                "read_only": "只读取和分析",
                "trusted_workspace": "仅可在本项目工作区内按现有审批规则修改",
                "ask": "涉及实际操作时按需请求确认",
            }.get(mode, "涉及实际操作时按需请求确认")
            lines.append(f"- 用户选择的工作方式：{mode_text}")
            lines.append(
                "- 目标书不能扩大文件、网络或工具权限；实际权限、审批和审计规则优先。"
            )
            return "\n".join(lines)
        except Exception:
            return ""

    def _handoff_brief(self, db: Any) -> str:
        """Inject a verified public handoff without inheriting permissions."""
        if not db or not hasattr(db, "get_conversation"):
            return ""
        try:
            conversation = db.get_conversation(self.conv_id)
            if not conversation or str(conversation.get("user_id") or "") != str(self.user_id):
                return ""
            from hashmm.api.chat_handoffs import render_for_context
            return render_for_context(self.user_id, self.conv_id)
        except Exception:
            return ""

    def _scan_workspace(self, max_files: int = 10, max_preview: int = 2000,
                        allowed_names: list[str] | tuple[str, ...] | None = None) -> str:
        """Scan conversation directory, build file inventory."""
        fdir = CONV_FILES_ROOT / self.conv_id
        if not fdir.exists():
            return ""

        allowed = None
        if allowed_names is not None:
            allowed = {
                str(name).strip()
                for name in allowed_names
                if str(name).strip() and Path(str(name)).name == str(name)
            }
        files = sorted(
            item for item in fdir.iterdir()
            if allowed is None or item.name in allowed
        )
        if not files:
            return ""

        parts = [f"**当前工作区（{len(files)} 个文件）：**"]
        total_chars = 0

        for f in files[:max_files]:
            if not f.is_file():
                continue
            sz = f.stat().st_size
            ext = f.suffix.lower()

            # Code files: show preview
            if ext in ('.py', '.js', '.ts', '.java', '.cpp', '.c', '.go', '.rs', '.m'):
                try:
                    content = f.read_text(encoding='utf-8', errors='replace')
                    preview = content[:max_preview] if total_chars + len(content) < 8000 else content[:500]
                    total_chars += len(preview)
                    parts.append(f"\n`{f.name}` ({sz}B):\n```{ext[1:]}\n{preview}\n```")
                except Exception as _e:
                    parts.append(f"- `{f.name}` ({sz}B)")
            # Document files: just list
            elif ext in ('.pptx', '.docx', '.xlsx', '.pdf'):
                parts.append(f"- 文件 `{f.name}` ({sz/1024:.1f}K)")
            # Images: note existence
            elif ext in ('.png', '.jpg', '.jpeg', '.svg'):
                parts.append(f"- 图片 `{f.name}` ({sz/1024:.1f}K)")
            else:
                parts.append(f"- `{f.name}` ({sz}B)")

        return "\n".join(parts)

    def _extract_recent_actions(self, history: list[dict], max_actions: int = 5) -> str:
        """Extract recent tool actions from conversation history."""
        actions = []
        for msg in reversed(history[-20:]):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "")
            # Detect file creation
            for m in re.finditer(r'(?:创建|生成|已创建).*?`?(\w+\.\w+)`?', content):
                actions.append(f"创建了 {m.group(1)}")
            # Detect execution
            if "执行结果" in content or "执行成功" in content:
                actions.append("执行了代码")
            if len(actions) >= max_actions:
                break

        if not actions:
            return ""
        return "**最近操作：** " + " → ".join(reversed(actions))

    def _format_memories(self, memories: list[dict]) -> str:
        """Format user memories for system prompt."""
        parts = ["**关于用户：**"]
        for m in memories[:5]:
            parts.append(f"- {m.get('key', '')}: {m.get('value', '')}")
        return "\n".join(parts)


def extract_user_facts(messages: list[dict]) -> list[dict]:
    """Extract learnable facts about the user from conversation.

    Returns list of {category, key, value} dicts.
    """
    facts = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        text = msg.get("content", "")

        # Research area
        for pattern in [r'我(?:在)?研究(\S+)', r'我的(?:研究|课题)(?:是|方向)(\S+)',
                       r'(?:做|搞)(\S+)(?:方向|研究|课题)']:
            m = re.search(pattern, text)
            if m:
                facts.append({"category": "research", "key": "研究方向", "value": m.group(1)})

        # Preferred language
        for lang in ["Python", "Java", "C++", "JavaScript", "Matlab", "Go", "Rust"]:
            if f"用{lang}" in text or f"我用{lang}" in text or f"我写{lang}" in text:
                facts.append({"category": "preference", "key": "编程语言", "value": lang})

    return facts
