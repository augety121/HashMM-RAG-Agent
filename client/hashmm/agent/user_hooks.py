"""hashmm/agent/user_hooks.py — V300 第四期：用户可配置 Hooks（对标 Claude Code hooks）。

区别于 tool_pipeline 里的代码级 PRE_TOOL_HOOKS（开发者写死的）：本模块让**用户在配置里**
声明式地定义 hook——"某类工具调用前/后触发某动作"，无需改代码。

支持的 hook 动作（声明式，保守）：
  · notify  —— 匹配时记一条通知事件（如"任何写文件都提醒我"）；
  · confirm —— 匹配时要求确认（返回短路结果，让 Agent 转为请求用户确认）；
  · block   —— 匹配时直接拦截（返回拒绝结果，如"禁止执行 rm 类命令"）。

匹配条件（任一命中即触发）：
  · tools   —— 工具名列表（如 ["write_file","run_shell"]）；
  · pattern —— 对参数做正则匹配（如 "rm\\s+-rf" 匹配危险删除）。

配置存 data/user_hooks.json，格式：
  {"hooks":[{"name":"写文件提醒","when":{"tools":["write_file"]},"action":"notify","message":"即将写文件"}]}

安全：规则位于统一工具执行入口；配置损坏或规则求值异常时按拒绝处理，不能静默绕过。
"""
from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.user_hooks")

_VALID_ACTIONS = ("notify", "confirm", "block")
_MAX_RULES = 100
_MAX_TOOLS = 64
_MAX_PATTERN = 160
_MAX_MATCH_TEXT = 2048
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")


def _config_path() -> Path:
    d = Path(os.environ.get("HASHMM_DATA_DIR", "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d / "user_hooks.json"


class HookConfigError(RuntimeError):
    """声明式 Hook 配置无法可靠读取或验证。"""


def _validate_pattern(pattern: str) -> None:
    """Accept a deliberately small regex subset and bound backtracking work.

    Python's built-in ``re`` has no execution timeout.  Groups/backreferences
    are therefore rejected, the input is capped, and at most two unbounded
    quantifiers are allowed.  This keeps useful patterns such as
    ``rm\\s+-rf`` while excluding common ReDoS constructions like
    ``(a+)+$`` and ``(a|aa)+$``.
    """
    if len(pattern) > _MAX_PATTERN:
        raise HookConfigError(f"参数正则不能超过 {_MAX_PATTERN} 个字符")
    escaped = False
    in_class = False
    unbounded = 0
    optional = 0
    for ch in pattern:
        if escaped:
            if ch.isdigit():
                raise HookConfigError("参数正则不支持反向引用")
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "[":
            in_class = True
            continue
        if ch == "]":
            in_class = False
            continue
        if in_class:
            continue
        if ch in "()":
            raise HookConfigError("参数正则不支持分组或断言；请改用工具名或简单字符类")
        if ch in "{}":
            raise HookConfigError("参数正则不支持花括号量词")
        if ch in "*+":
            unbounded += 1
        elif ch == "?":
            optional += 1
    if unbounded > 2:
        raise HookConfigError("参数正则最多允许两个 * 或 + 量词")
    if optional > 4:
        raise HookConfigError("参数正则最多允许四个 ? 量词")
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise HookConfigError(f"参数正则无效：{exc}") from exc


def _normalize_hooks(hooks: list) -> list[dict]:
    if len(hooks) > _MAX_RULES:
        raise HookConfigError(f"Hook 规则不能超过 {_MAX_RULES} 条")
    clean: list[dict] = []
    for raw in hooks:
        if not isinstance(raw, dict):
            raise HookConfigError("Hook 规则必须是对象")
        action = raw.get("action")
        if not isinstance(action, str) or action.strip().lower() not in _VALID_ACTIONS:
            raise HookConfigError("Hook action 只支持 notify / confirm / block")
        when = raw.get("when")
        if not isinstance(when, dict):
            raise HookConfigError("Hook when 必须是对象")
        raw_tools = when.get("tools", [])
        if not isinstance(raw_tools, list) or len(raw_tools) > _MAX_TOOLS:
            raise HookConfigError(f"tools 必须是最多 {_MAX_TOOLS} 项的数组")
        tools: list[str] = []
        for value in raw_tools:
            if not isinstance(value, str) or not _TOOL_NAME_RE.fullmatch(value.strip()):
                raise HookConfigError("tools 包含非法工具名")
            tool = value.strip()
            if tool not in tools:
                tools.append(tool)
        raw_pattern = when.get("pattern", "")
        if not isinstance(raw_pattern, str):
            raise HookConfigError("pattern 必须是字符串")
        pattern = raw_pattern.strip()
        if pattern:
            _validate_pattern(pattern)
        if not tools and not pattern:
            raise HookConfigError("Hook 至少需要一个工具名或参数正则")
        name = raw.get("name", "")
        message = raw.get("message", "")
        enabled = raw.get("enabled", True)
        if not isinstance(name, str) or not isinstance(message, str) or not isinstance(enabled, bool):
            raise HookConfigError("Hook name/message/enabled 类型无效")
        clean.append({
            "name": (name.strip() or "未命名规则")[:60],
            "when": {"tools": tools, "pattern": pattern},
            "action": action.strip().lower(),
            "message": message[:200],
            "enabled": enabled,
        })
    return clean


def _load_hooks(*, strict: bool) -> list[dict]:
    """读取规则；安全裁决链使用 ``strict=True``，管理页使用兼容模式。"""
    try:
        p = _config_path()
        if not p.is_file():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("hooks"), list):
            raise HookConfigError("Hook 配置必须是包含 hooks 数组的 JSON 对象")
        return _normalize_hooks(data["hooks"])
    except Exception as e:
        log_suppressed(logger, e, "user_hooks.load")
        if strict:
            if isinstance(e, HookConfigError):
                raise
            raise HookConfigError("Hook 配置读取失败") from e
        return []


def load_hooks() -> list[dict]:
    """管理页读取入口；畸形配置返回空，执行入口不会使用这个宽松路径。"""
    return _load_hooks(strict=False)


def load_hooks_strict() -> list[dict]:
    """安全/管理边界读取入口；损坏配置必须显式失败。"""
    return _load_hooks(strict=True)


def save_hooks(hooks: list[dict]) -> bool:
    """保存 hook 配置（校验+规整）。返回是否成功。"""
    try:
        clean = _normalize_hooks(hooks or [])
        p = _config_path()
        tmp = p.with_name(f".{p.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
        try:
            with tmp.open("x", encoding="utf-8") as handle:
                json.dump({"hooks": clean}, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, p)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        return True
    except Exception as e:
        log_suppressed(logger, e, "user_hooks.save")
        return False


def _matches(hook: dict, name: str, args: dict) -> bool:
    """判断一个 hook 是否匹配当前工具调用。"""
    when = hook.get("when") or {}
    tools = when.get("tools") or []
    if tools and name in tools:
        return True
    pattern = when.get("pattern") or ""
    if pattern:
        values: list[str] = []
        seen: set[int] = set()

        def _collect(value) -> None:
            if sum(len(v) for v in values) >= _MAX_MATCH_TEXT or len(values) >= 128:
                return
            if isinstance(value, (dict, list, tuple)):
                identity = id(value)
                if identity in seen:
                    return
                seen.add(identity)
                iterable = value.values() if isinstance(value, dict) else value
                for child in iterable:
                    _collect(child)
                return
            if isinstance(value, (str, int, float, bool)):
                values.append(str(value))

        _collect(args or {})
        blob = " ".join(values)[:_MAX_MATCH_TEXT]
        try:
            if re.search(pattern, blob, re.IGNORECASE):
                return True
        except re.error:
            pass   # 非法正则忽略，不影响其它 hook
    return False


# 通知累积（notify 动作把消息塞这里，供上层读取展示；进程内、轻量）
_notifications: list[str] = []


def drain_notifications() -> list[str]:
    """取走并清空累积的 hook 通知。"""
    global _notifications
    out = _notifications[:]
    _notifications = []
    return out


def evaluate_user_hooks(name: str, args: dict) -> dict | None:
    """PRE_TOOL_HOOK 入口：按用户配置的 hook 裁决。
    返回 None=放行；返回 dict=短路结果（confirm/block 用其作为工具结果）。
    配置损坏或求值异常属于策略边界故障，必须 fail-closed。
    """
    try:
        for hook in _load_hooks(strict=True):
            if not hook.get("enabled", True):
                continue
            if not _matches(hook, name, args):
                continue
            action = hook.get("action")
            msg = hook.get("message") or hook.get("name") or ""
            if action == "notify":
                _notifications.append(f"[{hook.get('name', 'Hook')}] {msg}")
                # notify 不短路，继续放行
            elif action == "confirm":
                return {"status": "needs_confirm",
                        "message": f"【用户规则：{hook.get('name', '')}】{msg or '此操作需要你确认'}。"
                                   "确认后我再执行；若不想执行请直接说。"}
            elif action == "block":
                return {"status": "denied",
                        "message": f"【用户规则拦截：{hook.get('name', '')}】{msg or '此操作被你的规则禁止'}。"}
    except Exception as e:
        log_suppressed(logger, e, "user_hooks.evaluate")
        return {
            "status": "denied",
            "message": "声明式 Hook 安全规则读取失败，已按拒绝处理；请让管理员检查配置。",
        }
    return None


def _central_pre_hook(name: str, args: dict, _ctx: dict):
    """把声明式结果适配到全局、不可绕过的 HookDecision。"""
    from hashmm.hooks import HookDecision

    result = evaluate_user_hooks(name, args)
    if not result:
        return HookDecision(allow=True, hook="user_declarative")
    status = str(result.get("status") or "")
    if status in {"needs_confirm", "denied"}:
        return HookDecision(
            allow=False,
            reason=str(result.get("message") or "被用户声明式 Hook 拒绝"),
            require_approval=status == "needs_confirm",
            risk="high",
            hook="user_declarative",
        )
    return HookDecision(allow=True, hook="user_declarative")


def register() -> None:
    """注册到所有执行路径共同经过的 ``hashmm.hooks`` 单一入口。"""
    try:
        from hashmm import hooks as central_hooks
        if not any(name == "user_declarative" for name, _ in central_hooks._PRE_HOOKS):
            central_hooks.register_pre_hook(
                "user_declarative", _central_pre_hook, before="permission",
                critical=True,
            )
        logger.info("[user_hooks] 已注册声明式 hooks（统一工具入口）")
    except Exception as e:
        log_suppressed(logger, e, "user_hooks.register")
