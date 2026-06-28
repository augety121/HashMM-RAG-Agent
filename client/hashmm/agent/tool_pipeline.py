"""hashmm/agent/tool_pipeline.py — V56 工具守卫管线（Agent harness 核心层）。

对标 Claude Code / OpenHands 这类系统的 harness 设计：每个工具调用在真正执行前
流经一条**显式有序、各自独立可测**的守卫链，而不是散落在循环里的巨型 if/elif。

    工具调用 → [pre hooks] → PermissionGuard → ExecBudgetGuard
             → SearchBudgetGuard → ConsecutiveDedupGuard → 执行 → [post hooks]

设计原则（与大厂一致）：
- 守卫只【读】TurnState 做判定，计数器的【写】留在循环里（单一写者，避免漂移）；
- 守卫自身异常绝不拦执行（harness 故障不能放大为任务故障）；
- Hooks 是空默认的扩展点（对标 Claude Code hooks）：pre 钩子可短路（返回 result
  dict 即拒绝），post 钩子纯观察，异常一律吞掉；
- 预算常量定义在这里（harness 层），loop 侧 re-export 保持所有既有 import 兼容。

V49-V53 的全部防御语义在迁移中逐字保留（165 个回归测试看守零变化）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# ─────────────────────── 预算常量（自 loop.py 迁入） ───────────────────────

# V55: 长任务余量（多文件/反复编辑验证）；失控由下方守卫体系兜底。
MAX_TOOL_CALLS = 24
# 防"无限检索不产出"模式。
MAX_SEARCH_CALLS = 3
# 防"反复执行代码直到超时"模式（真机多次观测）。V55: 3→5 给合法改-跑循环留余量。
MAX_EXEC_CALLS = 5

_SEARCH_TOOLS = {"kb_search", "web_search", "kg_query", "deep_search"}
_EXEC_TOOLS = {"execute_code"}

# 幂等只读工具：瞬态错误（超时/连接抖动）允许自动重试一次。
RETRYABLE_TOOLS = {"kb_search", "web_search", "kg_query", "fetch_url",
                   "read_file_range", "file_tree"}


# ─────────────────────── 循环状态对象 ───────────────────────

@dataclass
class TurnState:
    """一次 run() 的全部预算/去重状态——单一对象贯穿，取代散落的局部变量。"""
    total_tool_calls: int = 0
    search_calls: int = 0
    exec_calls: int = 0
    last_call_key: Optional[tuple] = None   # 上一次工具调用的 (name, 规范化参数)
    last_result_text: str = ""              # 上一次结果（供连续重复复用）


@dataclass
class GuardDecision:
    """守卫裁决。allow=False 时 result 即直接回给模型的工具结果。"""
    allow: bool
    result: Optional[dict] = None
    guard: str = ""


# ─────────────────────── 守卫（顺序即语义） ───────────────────────

class PermissionGuard:
    name = "permission"

    def check(self, name, args, call_key, state, *, permissions=None, user_id=None):
        if permissions is None:
            return None
        approved, reason = permissions.check(name, args, user_id)
        if approved:
            return None
        return GuardDecision(False, {"status": "denied",
                                     "message": f"权限不足: {reason}"}, self.name)


class ExecBudgetGuard:
    """V49: 防"反复 execute_code 直到超时"。计数在循环里【后】加，故用 >=。"""
    name = "exec_budget"

    def check(self, name, args, call_key, state, **_):
        if name in _EXEC_TOOLS and state.exec_calls >= MAX_EXEC_CALLS:
            return GuardDecision(False, {"status": "denied", "message": (
                f"⚠️ 代码执行次数已达本轮上限（{MAX_EXEC_CALLS} 次），不再执行。"
                "请停止调用 execute_code，基于已有的执行结果直接给出最终回答；"
                "若代码仍有问题，把修正后的完整代码用 create_file 保存并向用户说明。"
            )}, self.name)
        return None


class SearchBudgetGuard:
    """V52: 单调用级短路（真机 bench 抓获的单批次并发漏洞）。计数在循环顶【先】加，故用 >。"""
    name = "search_budget"

    def check(self, name, args, call_key, state, **_):
        if name in _SEARCH_TOOLS and state.search_calls > MAX_SEARCH_CALLS:
            return GuardDecision(False, {"status": "denied", "message": (
                f"⚠️ 检索次数已达本轮上限（{MAX_SEARCH_CALLS} 次），本次未执行。"
                "请停止继续检索，基于已检索到的内容直接给出最终回答；"
                "如确实信息不足，明确告诉用户缺什么。"
            )}, self.name)
        return None


class ConsecutiveDedupGuard:
    """V49: 只拦"同一调用紧跟同一调用"的卡死模式；读→写→读不误伤。"""
    name = "dedup"

    def check(self, name, args, call_key, state, **_):
        if call_key is not None and call_key == state.last_call_key:
            return GuardDecision(False, {"status": "ok", "message": (
                "⚠️ 重复调用：本次工具与参数和上一次完全相同，结果不会变化（已复用上次结果，"
                "未重复执行）。请基于该结果继续推进，不要再发起相同调用。\n"
                "--- 上次结果 ---\n" + state.last_result_text[:2000]
            )}, self.name)
        return None


DEFAULT_GUARDS = (PermissionGuard(), ExecBudgetGuard(),
                  SearchBudgetGuard(), ConsecutiveDedupGuard())


# ─────────────────────── Hooks 扩展点（对标 Claude Code hooks） ───────────────────────

PRE_TOOL_HOOKS: list[Callable] = []    # fn(name, args) -> None | dict(短路结果)
POST_TOOL_HOOKS: list[Callable] = []   # fn(name, args, result) -> None（纯观察）


def register_pre_tool_hook(fn: Callable) -> None:
    PRE_TOOL_HOOKS.append(fn)


def register_post_tool_hook(fn: Callable) -> None:
    POST_TOOL_HOOKS.append(fn)


# ─────────────────────── 管线 ───────────────────────

class ToolPipeline:
    def __init__(self, guards=None):
        self.guards = list(guards) if guards is not None else list(DEFAULT_GUARDS)

    def evaluate(self, name, args, call_key, state: TurnState,
                 *, permissions=None, user_id=None) -> Optional[GuardDecision]:
        """返回 None=放行执行；返回 GuardDecision=用其 result 作为工具结果。"""
        for hook in list(PRE_TOOL_HOOKS):
            try:
                r = hook(name, args)
                if isinstance(r, dict):
                    return GuardDecision(False, r, "pre_hook")
            except Exception:
                pass   # 钩子异常绝不拦执行
        for g in self.guards:
            try:
                d = g.check(name, args, call_key, state,
                            permissions=permissions, user_id=user_id)
            except Exception:
                d = None  # 守卫自身异常绝不拦执行（harness 故障不放大）
            if d is not None:
                return d
        return None

    def notify_post(self, name, args, result) -> None:
        for hook in list(POST_TOOL_HOOKS):
            try:
                hook(name, args, result)
            except Exception:
                pass


# ─────────────────────── 瞬态错误分类（重试策略用） ───────────────────────

_TRANSIENT_MARKERS = ("timeout", "timed out", "connection", "temporarily",
                      "reset by peer", "503", "502", "rate limit", "again")


def is_transient_error(exc: BaseException) -> bool:
    """瞬态（超时/连接抖动/限流）→ 幂等只读工具值得自动重试一次。"""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)
