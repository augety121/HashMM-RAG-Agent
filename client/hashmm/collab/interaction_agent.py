"""hashmm/collab/interaction_agent.py —— 交互 Agent（对外通信安全网关）· V319。

一个专职的 agent：**所有与外部 agent 的通信都经它一手**。业务 agent（RAG/团队/记忆
等）不直接跟外面打交道，而是把"我要找 X 帮忙"或"外面有请求进来"交给交互 Agent。
它是唯一的对外出入口，把散落的安全能力收成一道统一关卡：

    ┌─────────────── 交互 Agent（唯一对外口）───────────────┐
    │                                                        │
    │  入站（别人找我）：                                     │
    │    速率限制 → 信任门禁 → 安全策略（作用域/注入）        │
    │    → 授权执行 → 出站脱敏 → 审计留痕                     │
    │                                                        │
    │  出站（我找别人）：                                     │
    │    出站内容预扫描（别把自己的敏感信息发出去）           │
    │    → 目标信任校验 → 发送 → 审计                         │
    │                                                        │
    └────────────────────────────────────────────────────────┘

为什么要独立成 agent 而不是散在各处：
  1. **单一职责**：安全逻辑集中一处，审计和加固只需盯这一个模块（攻击面收敛）。
  2. **对称防护**：不仅防别人套我的数据（入站），也防我的 agent 被诱导把敏感信息
     发出去（出站）——出站脱敏是很多协作系统忽略的方向。
  3. **速率限制**：防止恶意方高频探测（就算是好友，异常高频请求也该挡）。
  4. **可插拔**：业务侧只依赖交互 Agent 的两个入口，底层 RPC/进程模型可换。

依赖注入：trust/audit 存储与业务执行器都可注入，纯逻辑可离线测。
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Callable

from .orchestrator import handle_collab_request
from .policy import redact_sensitive, scan_sensitive
from .trust import get_trust_store

__all__ = ["InteractionAgent", "get_interaction_agent"]


# 速率限制：每个来源用户在窗口内的最大请求数（防高频探测/套取）。
_RATE_WINDOW_SEC = 60
_RATE_MAX_PER_WINDOW = 20


class InteractionAgent:
    """对外通信安全网关。业务 agent 通过它与外部 agent 交互。"""

    def __init__(self, *, trust_store=None, audit=None,
                 rate_window: int = _RATE_WINDOW_SEC,
                 rate_max: int = _RATE_MAX_PER_WINDOW):
        self._trust = trust_store
        self._audit = audit
        self._rate_window = rate_window
        self._rate_max = rate_max
        # {from_user: deque[timestamp]} —— 滑动窗口速率限制
        self._calls: dict[str, deque] = defaultdict(deque)

    def _trust_store(self):
        return self._trust or get_trust_store()

    def _rate_ok(self, from_user: str) -> tuple[bool, int]:
        """滑动窗口速率检查。返回 (是否放行, 窗口内已用次数)。"""
        now = time.time()
        dq = self._calls[from_user]
        cutoff = now - self._rate_window
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= self._rate_max:
            return False, len(dq)
        dq.append(now)
        return True, len(dq)

    # ── 入站：外部 agent 找我 ─────────────────────────────
    def handle_inbound(self, request: dict, *,
                       agent_executor: Callable[[str], str] | None = None,
                       my_user_id: str = "") -> dict:
        """处理一个入站协作请求（在 orchestrator 五关卡前，再加速率限制）。

        request: {"from_user", "scope", "task", "allow_private"?}
        my_user_id: 我的用户 ID（作为 to_user；不信任请求里的 to_user 防伪造）
        """
        from_user = str(request.get("from_user") or "")
        # 强制 to_user 为本机身份（防请求伪造成发给别人）
        req = dict(request)
        if my_user_id:
            req["to_user"] = str(my_user_id)

        # ★ 速率限制（信任门禁之前——就算是好友，异常高频也先挡，防探测/套取）
        ok, used = self._rate_ok(from_user)
        if not ok:
            if self._audit:
                try:
                    self._audit.record(from_user, req.get("to_user", ""), "collab_rate_limited",
                                       False, f"速率超限（{used}/{self._rate_max} 每 {self._rate_window}s）")
                except Exception:  # noqa: BLE001
                    pass
            return {"ok": False, "rejected_reason":
                    f"请求过于频繁（{used}/{self._rate_max} 每 {self._rate_window}s），已限流",
                    "rate_limited": True}

        # 交给 orchestrator 走完整五关卡（信任/安全/执行/脱敏/审计）
        return handle_collab_request(req, agent_executor=agent_executor,
                                     trust_store=self._trust_store(), audit=self._audit)

    # ── 出站：我找外部 agent ─────────────────────────────
    def send_outbound(self, from_user: str, to_user: str, scope: str, task: str,
                      *, transport: Callable[[dict], dict] | None = None,
                      allow_private: bool = False) -> dict:
        """我的 agent 主动向外部 agent 发起协作。

        ★ 出站防护（很多协作系统忽略的方向）：发之前先扫自己要发的内容，别把本机的
        敏感信息（API key/密码/私钥/内部路径）无意中发给外部——即便对方可信。

        transport: 实际投递函数 request(dict)->response(dict)（注入 RPC；测试注入桩）。
                   不传则仅做出站安全检查、不实际发送（dry-run）。
        """
        # ① 出站内容预扫描
        sensitive_hits = scan_sensitive(task)
        redacted_task, leaked = redact_sensitive(task)
        outbound_blocked = bool(sensitive_hits)

        # ② 目标信任校验（我信任对方才发）
        rel = self._trust_store().relationship(from_user, to_user)

        result = {"ok": False, "to_user": to_user, "scope": scope,
                  "outbound_scan": {"sensitive_found": [h["type"] for h in sensitive_hits],
                                    "redacted": bool(leaked)},
                  "trust": rel}

        if not rel.get("trusted"):
            result["rejected_reason"] = f"不向不可信对象发送：{rel.get('detail')}"
            self._audit_out(from_user, to_user, "outbound_blocked_untrusted", False, scope)
            return result

        # ③ 若原始任务含敏感信息 → 用脱敏版发送，并告警
        send_task = redacted_task if outbound_blocked else task
        if outbound_blocked:
            result["outbound_scan"]["action"] = "已脱敏后发送（原文含敏感信息）"

        # ④ 实际投递
        if not callable(transport):
            result.update({"ok": True, "dry_run": True, "would_send": send_task[:200]})
            self._audit_out(from_user, to_user, "outbound_dryrun", True, scope)
            return result
        try:
            resp = transport({"from_user": from_user, "to_user": to_user,
                              "scope": scope, "task": send_task,
                              "allow_private": allow_private})
        except Exception as e:  # noqa: BLE001
            result["rejected_reason"] = f"投递失败:{type(e).__name__}"
            self._audit_out(from_user, to_user, "outbound_failed", False, scope)
            return result

        # ⑤ 对返回内容也脱敏（对方回复可能夹带我方或第三方敏感信息）
        resp_text = str((resp or {}).get("result") or "")
        resp_redacted, _ = redact_sensitive(resp_text)
        result.update({"ok": bool((resp or {}).get("ok")), "response": resp_redacted,
                       "raw_response_meta": {k: v for k, v in (resp or {}).items() if k != "result"}})
        self._audit_out(from_user, to_user, "outbound_sent", result["ok"], scope)
        return result

    def _audit_out(self, from_user: str, to_user: str, action: str, ok: bool, scope: str) -> None:
        if self._audit:
            try:
                self._audit.record(from_user, to_user, action, ok, f"出站 scope={scope}", scope=scope)
            except Exception:  # noqa: BLE001
                pass

    def status(self) -> dict:
        """交互 Agent 自身状态（供 /api 与前端展示）。"""
        active_sources = sum(1 for dq in self._calls.values() if dq)
        return {
            "role": "交互 Agent（对外通信安全网关）",
            "rate_limit": f"{self._rate_max} 请求 / {self._rate_window}s",
            "active_peers": active_sources,
            "guards": ["速率限制", "信任门禁", "作用域授权", "注入检测",
                       "入站出站双向脱敏", "审计留痕"],
        }


_AGENT: InteractionAgent | None = None


def get_interaction_agent(**kwargs) -> InteractionAgent:
    global _AGENT
    if kwargs:
        return InteractionAgent(**kwargs)
    if _AGENT is None:
        _AGENT = InteractionAgent()
    return _AGENT
