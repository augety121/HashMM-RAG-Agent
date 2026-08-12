"""v17 Phase 30 — high-risk agent action human-in-the-loop (OWASP LLM06).

Excessive Agency: an agent (esp. driven by injected content) may call tools that
write, delete, send, pay, or otherwise take irreversible / external actions. OWASP
mitigation for high-risk or irreversible actions is human-in-the-loop approval.

This classifies a tool call as low/high risk and decides whether it needs approval,
plus a tiny in-memory approval gate. Read-only tools (search/retrieve/calc/read)
stay low-risk and flow freely; mutating/external tools require approval.

Pure + offline-testable. Wire: before executing a tool, if requires_approval(...),
pause and ask the user (or an approver) instead of auto-running it.
"""
from __future__ import annotations

import re
import time

# Tool-name signals of a mutating / external / irreversible action.
_HIGH_RISK_NAME = re.compile(
    r"(write|create|update|delete|remove|drop|send|email|post|put|transfer|pay|"
    r"purchase|deploy|exec|run_command|shell|http_post|webhook|move|rename|"
    r"删除|发送|写入|修改|转账|支付|部署|执行)", re.IGNORECASE)
# Read-only tools that are always safe to auto-run.
_LOW_RISK_NAME = re.compile(
    r"(search|retrieve|read|get|list|fetch|lookup|calc|calculator|datetime|"
    r"查询|检索|读取|计算)", re.IGNORECASE)
# Arg signals (e.g. an HTTP method that mutates).
_HIGH_RISK_ARG = re.compile(r"\b(POST|PUT|DELETE|PATCH)\b", re.IGNORECASE)   # strict 档沿用
# V228 balanced 档专用：只有"真危险"参数模式才升危（普通文本里出现 delete/post 一律不误伤）
_TRULY_DANGEROUS_ARG = re.compile(
    r"(rm\s+-rf\s+/|del\s+/[sq]\b|format\s+[a-z]:|mkfs\.|drop\s+(table|database)\b|"
    r"shutdown\s+(-|/)|reg\s+delete|dd\s+if=)", re.IGNORECASE)


def classify_tool_risk(tool_name: str, args: dict | None = None) -> str:
    """Return 'high' or 'low' risk for a tool call.

    V228 三档（HASHMM_SAFETY_MODE，默认 balanced）——既保安全、不误伤：
      strict   老行为：名字或参数出现 POST/DELETE 等字样即 high（最严，也最容易误拦）。
      balanced 默认：①只读类工具（search/read/fetch/查询…）**永不**因参数升危——
               普通文本里出现 "delete/发送" 不再误伤；②非只读工具仅命中
               「真危险模式」（rm -rf / format / drop table / shutdown…）或高危名才 high。
      off      一律 low（仅审计留痕，不做任何拦截）——自己电脑自己做主。
    """
    import os as _os
    mode = (_os.environ.get("HASHMM_SAFETY_MODE") or "balanced").strip().lower()
    name = tool_name or ""
    blob = " ".join(f"{k}={v}" for k, v in (args or {}).items())
    if mode == "off":
        return "low"
    if mode == "strict":
        if _HIGH_RISK_NAME.search(name):
            return "high"
        if _HIGH_RISK_ARG.search(blob):
            return "high"
        if _LOW_RISK_NAME.search(name):
            return "low"
        return "low"
    # balanced（默认）
    if _LOW_RISK_NAME.search(name):
        return "low"                       # 只读工具无条件低危：参数里有啥词都不误伤
    if _HIGH_RISK_NAME.search(name):
        return "high"                      # 名字本身就是写/删/发/付 → 该确认还得确认
    if _TRULY_DANGEROUS_ARG.search(blob):
        return "high"                      # 未知工具但参数是真毁灭性命令
    return "low"


def requires_approval(tool_name: str, args: dict | None = None,
                      auto_approve: set | None = None) -> bool:
    """High-risk tools require approval unless explicitly in auto_approve allowlist."""
    if auto_approve and tool_name in auto_approve:
        return False
    return classify_tool_risk(tool_name, args) == "high"


class ApprovalGate:
    """Minimal in-memory approval queue for high-risk tool calls."""

    def __init__(self):
        self._pending: dict = {}
        self._decisions: dict = {}

    def request(self, tool_name: str, args: dict | None = None) -> str:
        token = f"appr_{len(self._pending) + len(self._decisions) + 1}_{int(time.time())}"
        self._pending[token] = {"tool": tool_name, "args": args or {}, "ts": time.time()}
        return token

    def resolve(self, token: str, approved: bool) -> bool:
        if token in self._pending:
            self._pending.pop(token)
            self._decisions[token] = approved
            return True
        return False

    def is_approved(self, token: str) -> bool:
        return self._decisions.get(token, False) is True

    def pending(self) -> list:
        return [{"token": t, **v} for t, v in self._pending.items()]
