"""统一安全策略中心 SecurityPolicy。

为什么需要：现有安全逻辑分散在 4 处，没有统一视图/决策入口/审计：
  1) agent/permissions.py     —— deny-first 权限（管理员/频率/审计）
  2) agent/tool_governance.py —— 高风险工具审批（PreTool）
  3) api/tool_functions.py    —— execute_code 代码黑名单（禁 subprocess 等）
  4) api/design_render.py     —— 渲染命令白名单

本模块**不重写**这 4 处逻辑（风险高），而是提供一个**统一决策外观**：
把一次工具调用按 Codex 的三层依次评估，返回统一的 Decision，并汇总审计。

三层（对齐 Codex）：
  - **Layer 1 · 安全工具自动放行**（对应 Codex 安全命令 ls/cat 自动 approve）：
    只读工具直接放行，不打扰。
  - **Layer 2 · 静态预检/白名单**：execute_code 黑名单 + render 白名单。
    注意：这不是 OS 沙箱；真正的执行隔离由 ``agent.sandbox`` 强制执行。
  - **Layer 3 · 策略/审批**（对应 Codex policy/approval）：权限等级 + 高风险审批 + 频率。

安全判断异常必须拒绝，不能为了可用性静默放行。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.security_policy")

# 只读/安全工具：Layer 1 自动放行（对齐 Codex 的 ls/cat/git status 自动批准）
_SAFE_TOOLS = frozenset({
    "kb_search", "kg_query", "corpus_stats", "read_file", "list_files",
    "get_weather", "calculate",
})

# 高风险工具（变更/外部/不可逆）：Layer 3 需审批
_HIGH_RISK_TOOLS = frozenset({
    "execute_code", "create_file", "edit_file", "delete_file",
    "render_design", "send_email", "http_post", "run_shell", "shell_exec",
    "browser_act",
})


@dataclass
class Decision:
    allowed: bool
    layer: str                 # 哪一层做的决定
    reason: str = ""
    audit: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "layer": self.layer,
                "reason": self.reason, "audit": self.audit}


def risk_of(tool_name: str) -> str:
    if tool_name in _SAFE_TOOLS:
        return "safe"
    if tool_name in _HIGH_RISK_TOOLS:
        return "high"
    try:
        from hashmm.api.tool_registry import TOOL_ANNOTATIONS, tool_annotation
        known_dynamic = str(tool_name or "").startswith("mcp__")
        if not known_dynamic and tool_name not in TOOL_ANNOTATIONS:
            try:
                from hashmm.api.plugins import get_plugin_manager
                known_dynamic = get_plugin_manager().get_tool_annotation(tool_name) is not None
            except Exception:
                known_dynamic = False
        if not known_dynamic and tool_name not in TOOL_ANNOTATIONS:
            return "medium"
        annotation = tool_annotation(tool_name)
        if annotation.get("destructive") or not annotation.get("read_only"):
            return "high"
        if annotation.get("open_world"):
            return "medium"
        return "safe"
    except Exception:
        pass
    return "medium"


def evaluate(tool_name: str, args: dict | None = None, *,
             user_id: str = "", approved: bool = False,
             permission_prechecked: bool = False,
             cwd: str = "", conv_id: str = "") -> Decision:
    """三层依次评估一次工具调用，返回统一 Decision。

    任一层拒绝即返回拒绝；全部通过才放行。复用现有 permissions/governance/render 逻辑。
    """
    args = args or {}
    audit: list = []
    risk = risk_of(tool_name)
    audit.append(f"risk={risk}")

    # ── Layer 1 · 安全工具无需额外风险审批，但仍经过权限系统 ──
    terminal_layer = "L3-policy"
    if risk == "safe":
        audit.append("L1-safe-candidate")
        terminal_layer = "L1-safe"

    # ── Layer 2 · 沙箱/白名单 ──
    # execute_code：复用其黑名单（禁 subprocess 等）
    if tool_name == "execute_code":
        code = args.get("code", "") or ""
        dangerous = ["os.system", "subprocess", "shutil.rmtree", "rm -rf",
                     "__import__('os')", "import socket", "import http"]
        for p in dangerous:
            if p in code:
                audit.append(f"L2-sandbox-deny:{p}")
                return Decision(False, "L2-sandbox", f"代码含禁止操作: {p}", audit)
        audit.append("L2-sandbox-ok")
    # render_design：复用其白名单（命令必须在白名单内）
    if tool_name == "render_design":
        try:
            from hashmm.api import design_render as DR
            if not DR.render_enabled():
                audit.append("L2-render-disabled")
                return Decision(False, "L2-sandbox", "设计渲染未启用", audit)
        except Exception as e:
            log_suppressed(logger, e)
            audit.append("L2-render-check-error")
            return Decision(False, "L2-sandbox", "设计渲染安全检查失败", audit)
        audit.append("L2-render-ok")

    # ── Layer 3 · 策略/权限/审批 ──
    # 复用 permissions.check（管理员/频率/审计）
    if permission_prechecked:
        audit.append("L3-perm-prechecked")
    else:
        try:
            from hashmm.agent.permissions import get_permissions
            ok, reason = get_permissions().check(
                tool_name, args, user_id, cwd=cwd or None, conv_id=conv_id or None)
            if not ok:
                audit.append(f"L3-perm-deny:{reason}")
                return Decision(False, "L3-policy", reason, audit)
            audit.append("L3-perm-ok")
        except Exception as e:
            log_suppressed(logger, e)
            audit.append("L3-perm-error")
            return Decision(False, "L3-policy", "权限系统校验失败，已按拒绝处理", audit)

    # 高风险工具：若开启审批且未批准 → 拒绝（deny-first）
    if risk == "high":
        try:
            from hashmm import settings
            _approval_on = settings.get_bool("HASHMM_TOOL_APPROVAL")
        except Exception:
            import os
            _approval_on = os.environ.get("HASHMM_TOOL_APPROVAL", "0").strip().lower() in {"1", "true", "yes", "on"}
        if _approval_on:
            if not approved:
                audit.append("L3-approval-required")
                return Decision(False, "L3-policy", f"高风险工具 {tool_name} 需显式审批", audit)
            audit.append("L3-approval-granted")

    return Decision(True, terminal_layer, "通过全部安全层", audit)


def summary() -> dict:
    """安全策略概览（便于向企业解释/审计面板）。"""
    return {
        "layers": ["L1-safe(低风险分类)", "L2-preflight(白名单/黑名单)", "L3-policy(权限/审批/频率)"],
        "safe_tools": sorted(_SAFE_TOOLS),
        "high_risk_tools": sorted(_HIGH_RISK_TOOLS),
        "model": "preflight→OS sandbox→policy；审批不能扩大沙箱能力",
    }
