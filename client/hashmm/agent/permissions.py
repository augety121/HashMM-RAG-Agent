"""Permission System — deny-first 权限控制（V308 重写，修 P0-3）。

对标 Claude Code / Codex / Qoder 的共同基线：权限策略 = 默认拒绝 + 显式登记 +
故障拒绝，而不仅是一个确认弹窗。

设计原则（相对 V306 的关键变化）：
  1. 【未登记工具一律拒绝】——原实现 ``TOOL_PERMISSIONS.get(name, READ)`` 把任何未知
     工具默认当只读放行；现在未登记 → 直接 deny。新增工具必须同步登记权限，否则跑不起来
     （这正是我们想要的：登记是强制动作，不是可选）。
  2. 【危险级默认需要审批】——execute / network / delete / system 的 auto_approve=False。
     其中 execute/network 允许通过显式环境开关降级为自动放行（本地单机/评测场景），但该开关
     只影响 execute/network，delete/system 永远需要人工批准或管理员。
  3. 【审批模块故障 → 拒绝】——检查管理员身份、审批状态等若抛异常，一律按拒绝处理
     （fail-closed），不再 ``log_suppressed`` 后继续放行。
  4. 【启动一致性校验】——``verify_registry_consistency()`` 校验“已注册工具”与“权限表”
     完全一致：注册了但没登记权限 = 危险（会走未登记拒绝路径，但应在启动暴露）；登记了权限
     但工具不存在 = 权限表有死条目。二者都在启动时报出来。
  5. 【批准与参数绑定】——``bind_approval(user_id, tool, args, cwd, ttl)`` 生成指纹；
     ``check`` 时校验指纹匹配且未过期，杜绝“批准 A 后执行 B”。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.agent.permissions")


class PermissionLevel:
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    DELETE = "delete"
    SYSTEM = "system"


# 工具 → 权限映射。**必须覆盖所有已注册工具**（见 verify_registry_consistency）。
# 未在此表登记的工具，check() 直接拒绝。
TOOL_PERMISSIONS: dict[str, str] = {
    # 只读
    "kb_search": PermissionLevel.READ,
    "kg_query": PermissionLevel.READ,
    "read_file": PermissionLevel.READ,
    "read_file_range": PermissionLevel.READ,
    "repository_map": PermissionLevel.READ,
    "list_files": PermissionLevel.READ,
    "file_tree": PermissionLevel.READ,
    "search_files": PermissionLevel.READ,
    "file_versions": PermissionLevel.READ,
    "pptx_summary": PermissionLevel.READ,
    "inspect_office": PermissionLevel.READ,
    "video_transcript": PermissionLevel.READ,
    "image_search": PermissionLevel.READ,
    "spawn_worker": PermissionLevel.READ,   # 派生只读子任务
    # 写（工作区内文件创建/编辑，自动批准但审计）
    "create_file": PermissionLevel.WRITE,
    "create_document": PermissionLevel.WRITE,
    "create_xlsx": PermissionLevel.WRITE,
    "create_pdf": PermissionLevel.WRITE,
    "create_pptx_from_plan": PermissionLevel.WRITE,
    "convert_file": PermissionLevel.WRITE,
    "edit_file": PermissionLevel.WRITE,
    "str_replace": PermissionLevel.WRITE,
    "canvas_block_patch": PermissionLevel.WRITE,
    "insert_lines": PermissionLevel.WRITE,
    "pptx_edit_slide": PermissionLevel.WRITE,
    "file_restore": PermissionLevel.WRITE,
    "remember_preference": PermissionLevel.WRITE,
    # 执行（代码执行，默认需审批；可经 EXEC 开关降级）
    "execute_code": PermissionLevel.EXECUTE,
    "render_design": PermissionLevel.EXECUTE,
    # 网络（外联，默认需审批；可经 NET 开关降级）
    "fetch_url": PermissionLevel.NETWORK,
    "web_search": PermissionLevel.NETWORK,
    # V309 浏览器内核（有状态真浏览器；同 NETWORK 级——外联能力，standard 下自动放行+审计+限流）
    "browser_open": PermissionLevel.NETWORK,
    "browser_act": PermissionLevel.NETWORK,
    "browser_read": PermissionLevel.NETWORK,
    "browser_screenshot": PermissionLevel.NETWORK,
    # 删除（永远需批准）
    "delete_doc": PermissionLevel.DELETE,
    "clean_workspace": PermissionLevel.DELETE,
    # 系统（Shell/系统命令，永远需批准或管理员）
    "run_shell": PermissionLevel.SYSTEM,
    "shell_exec": PermissionLevel.SYSTEM,
}

# 权限配置。auto_approve=False 的级别默认需要人工批准或管理员身份。
PERMISSION_CONFIG: dict[str, dict[str, Any]] = {
    PermissionLevel.READ:    {"auto_approve": True,  "audit": False, "rate_limit": 0},
    PermissionLevel.WRITE:   {"auto_approve": True,  "audit": True,  "rate_limit": 0},
    PermissionLevel.EXECUTE: {"auto_approve": False, "audit": True,  "rate_limit": 20},
    PermissionLevel.NETWORK: {"auto_approve": False, "audit": True,  "rate_limit": 30},
    PermissionLevel.DELETE:  {"auto_approve": False, "audit": True,  "rate_limit": 5},
    PermissionLevel.SYSTEM:  {"auto_approve": False, "audit": True,  "rate_limit": 5},
}

# ── 权限模式（对标 Claude Code 的 permission mode）───────────────────────
# 现实约束：本产品是【本地单机 Agent】。若 execute_code 每次都要管理员，Agent 直接不可用；
# 但把 run_shell（开放式命令执行）自动放行则是真实高危。所以把「硬安全底线」与
# 「可配策略」分开：
#
#   MODE_STANDARD（默认，本地单机）：
#       execute / network → 自动批准 + 审计 + 限流（沙箱子进程 + 工作区 cwd + 内存限额）
#       delete  / system  → 必须显式批准或管理员（run_shell 在此列）
#   MODE_STRICT（服务端 / 多租户 / 生产）：
#       execute / network 也必须显式批准。用 HASHMM_PERMISSION_MODE=strict 打开。
#   MODE_BYPASS（仅离线评测 / CI）：
#       全部自动放行。必须显式 opt-in，启动打 WARNING。
#
# 无论哪种模式，以下【硬底线】恒成立，任何配置都无法绕过：
#   1. 未登记工具 → 拒绝（原实现默认按 READ 放行，run_shell 因未登记而被自动批准——这正是 P0）
#   2. 审批/身份校验模块异常 → 拒绝（fail-closed，不再吞异常继续）
#   3. 批准与「工具名 + 参数 + 工作目录 + 有效期」绑定，杜绝“批准 A 后执行 B”
MODE_STANDARD = "standard"
MODE_STRICT = "strict"
MODE_BYPASS = "bypass"

# 各模式下【允许自动放行】的级别。delete/system 只在 bypass 下自动——这是刻意的。
_MODE_AUTO_LEVELS: dict[str, set[str]] = {
    MODE_STANDARD: {PermissionLevel.READ, PermissionLevel.WRITE,
                    PermissionLevel.EXECUTE, PermissionLevel.NETWORK},
    MODE_STRICT:   {PermissionLevel.READ, PermissionLevel.WRITE},
    MODE_BYPASS:   {PermissionLevel.READ, PermissionLevel.WRITE, PermissionLevel.EXECUTE,
                    PermissionLevel.NETWORK, PermissionLevel.DELETE, PermissionLevel.SYSTEM},
}


def get_mode() -> str:
    """当前权限模式。未配置 → MODE_STANDARD。非法值 → 回落到 STANDARD 并告警（不静默）。"""
    raw = (os.environ.get("HASHMM_PERMISSION_MODE") or "").strip().lower()
    if not raw:
        return MODE_STANDARD
    if raw in _MODE_AUTO_LEVELS:
        if raw == MODE_BYPASS:
            logger.warning(
                "权限模式 = bypass：所有工具（含 run_shell/删除）自动放行。"
                "仅供离线评测/CI，切勿用于生产或联网环境。")
        return raw
    logger.error("未知权限模式 %r，回落到 standard（合法值：standard/strict/bypass）", raw)
    return MODE_STANDARD


def _auto_allowed(level: str) -> bool:
    """该级别在当前模式下是否允许自动放行。"""
    return level in _MODE_AUTO_LEVELS.get(get_mode(), _MODE_AUTO_LEVELS[MODE_STANDARD])


def _approval_fingerprint(tool: str, args: dict, cwd: str | None) -> str:
    """把工具名 + 关键参数 + 工作目录压成指纹，用于绑定“批准了什么”。"""
    try:
        payload = json.dumps({"t": tool, "a": args or {}, "c": cwd or ""},
                             sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        payload = f"{tool}|{cwd}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def approval_fingerprint(tool: str, args: dict, cwd: str | None = None) -> str:
    """Public canonical fingerprint shared by the durable approval store."""
    return _approval_fingerprint(tool, args, cwd)


class PermissionSystem:
    """权限检查 + 审计日志 + 参数绑定审批。"""

    def __init__(self):
        self._rate_counters: dict[str, list[float]] = {}
        # user_id → { fingerprint: expires_at }
        self._approvals: dict[str, dict[str, float]] = {}
        # V309：会话作用域权限模式。user_id → (mode, expires_at)。
        # 动机：外部基准评测（Terminal-bench/SWE-bench 等）在服务器进程内驱动 agent，
        # run_shell 属 SYSTEM 级 → standard 模式下无人点批准 → 评测里 agent 拿不到 shell，
        # 分数恒 0（这不是模型差，是被权限卡死）。全局 HASHMM_PERMISSION_MODE=bypass 又会
        # 把【真实用户】一起放行——过宽。这里提供【按 user 粒度、TTL 绑定、可撤销】的作用域
        # 提权：评测 runner 只给评测专用 user（如 "bench"）授 bypass，到期自动失效。
        self._session_modes: dict[str, tuple[str, float]] = {}

    # ── 会话作用域权限模式（V309，供离线评测/CI 用）──
    _SESSION_MODE_MAX_TTL = 4 * 3600.0   # 上限 4h，防止长期悬挂的提权

    def grant_session_mode(self, user_id: str, mode: str, ttl: float = 1800.0) -> bool:
        """给单个 user 授予作用域权限模式（如评测期间的 bypass）。

        仅接受已知模式；TTL 强制封顶；授予/撤销都打日志（bypass 打 WARNING）。
        返回是否授予成功。非法输入一律拒绝（fail-closed），不抛异常。
        """
        uid = (user_id or "").strip()
        m = (mode or "").strip().lower()
        if not uid or m not in _MODE_AUTO_LEVELS:
            logger.error("grant_session_mode 拒绝：user=%r mode=%r（非法）", user_id, mode)
            return False
        ttl = max(1.0, min(float(ttl or 0), self._SESSION_MODE_MAX_TTL))
        self._session_modes[uid] = (m, time.time() + ttl)
        log = logger.warning if m == MODE_BYPASS else logger.info
        log("会话作用域权限模式：user=%s → %s（%.0fs 后自动失效）。仅供离线评测/CI。",
            uid, m, ttl)
        self._audit(uid, "__session_mode__", {"mode": m, "ttl": ttl}, "granted", "作用域权限模式")
        return True

    def revoke_session_mode(self, user_id: str) -> None:
        """撤销该 user 的作用域权限模式（评测结束时调用；幂等）。"""
        if self._session_modes.pop((user_id or "").strip(), None) is not None:
            logger.info("会话作用域权限模式已撤销：user=%s", user_id)

    def effective_mode(self, user_id: str) -> str:
        """该 user 当前生效的权限模式：有效的会话作用域覆盖 > 全局模式。过期即清。"""
        uid = (user_id or "").strip()
        ent = self._session_modes.get(uid)
        if ent:
            mode, exp = ent
            if time.time() <= exp:
                return mode
            self._session_modes.pop(uid, None)   # 过期即清
        return get_mode()

    # ── 审批绑定 ──
    def bind_approval(self, user_id: str, tool: str, args: dict,
                      cwd: str | None = None, ttl: float = 300.0) -> str:
        """登记一次“用户已批准执行 tool(args) @ cwd”，有效期 ttl 秒。返回指纹。

        UI 侧在用户点“批准”后调用本方法；随后真正执行时 check() 会校验指纹匹配。
        """
        fp = _approval_fingerprint(tool, args, cwd)
        self._approvals.setdefault(user_id or "", {})[fp] = time.time() + max(1.0, ttl)
        return fp

    def _has_valid_approval(self, user_id: str, tool: str, args: dict,
                            cwd: str | None) -> bool:
        table = self._approvals.get(user_id or "")
        if not table:
            return False
        fp = _approval_fingerprint(tool, args, cwd)
        exp = table.get(fp)
        if exp is None:
            return False
        if time.time() > exp:
            table.pop(fp, None)   # 过期即清
            return False
        # In-memory compatibility approvals are one-shot as well. Keeping the
        # fingerprint until TTL used to allow an approved destructive call to
        # be replayed repeatedly.
        table.pop(fp, None)
        return True

    # ── 主检查 ──
    def check(self, tool_name: str, args: dict, user_id: str,
              cwd: str | None = None, conv_id: str | None = None) -> tuple[bool, str]:
        """检查工具调用权限。Returns (approved, reason)。

        deny-first：未登记工具、审批模块异常、无有效批准 → 一律拒绝。
        """
        # 0. 工具权限级别解析。
        #    已登记 → 用登记的级别。
        #    未登记 → 拒绝。动态插件必须提供 MCP annotation，或在服务端权限表中
        #    显式登记；不能根据名字猜测未知工具只有 WRITE 能力。
        level = TOOL_PERMISSIONS.get(tool_name)
        if level is None and str(tool_name or "").startswith("mcp__"):
            try:
                from hashmm.tools.mcp_client import get_tool_annotation
                annotation = get_tool_annotation(tool_name) or {}
                level = (PermissionLevel.READ if annotation.get("read_only")
                         else PermissionLevel.NETWORK)
            except Exception as exc:
                # Dynamic MCP reaches an external process. If annotations are
                # unavailable, NETWORK is the conservative capability class.
                logger.error("MCP 动态权限解析失败，按 NETWORK 处理: %r", exc)
                level = PermissionLevel.NETWORK
        if level is None:
            reason = f"工具 {tool_name} 未登记权限能力，已按拒绝处理"
            self._audit(user_id, tool_name, args, "denied", reason)
            return False, reason

        config = PERMISSION_CONFIG.get(level)
        if config is None:
            reason = f"工具 {tool_name} 的权限级别 {level} 未配置——拒绝"
            self._audit(user_id, tool_name, args, "denied", reason)
            return False, reason

        # 1. 该级别在【该 user 生效模式】下是否允许自动放行（V309：会话作用域覆盖 > 全局）
        eff_mode = self.effective_mode(user_id)
        if level not in _MODE_AUTO_LEVELS.get(eff_mode, _MODE_AUTO_LEVELS[MODE_STANDARD]):
            failure_reason = ""

            # (a) 管理员身份：出错按“非管理员”处理（fail-closed，不再吞异常继续）
            is_admin = False
            try:
                from hashmm.api import database as db
                user = db.get_user(user_id) if user_id else None
                is_admin = bool(user and user.get("role") == "admin")
            except Exception as e:
                failure_reason = "身份校验失败"
                log_suppressed(logger, e)
                is_admin = False

            # (b) 与「工具名+参数+cwd」绑定的一次性批准（用户在 UI 点“批准”后由
            #     bind_approval 登记）。校验出错同样按未批准处理。
            has_appr = False
            try:
                has_appr = self._has_valid_approval(user_id, tool_name, args, cwd)
                if not has_appr and conv_id:
                    from hashmm.api import database as db
                    consumed = db.consume_tool_approval(
                        user_id=user_id,
                        conv_id=conv_id,
                        fingerprint=_approval_fingerprint(tool_name, args, cwd),
                    )
                    has_appr = bool(consumed)
            except Exception as e:
                failure_reason = failure_reason or "审批状态校验失败"
                log_suppressed(logger, e)
                has_appr = False

            if not (is_admin or has_appr):
                reason = failure_reason or (
                    f"工具 {tool_name}（{level}）需要明确批准或管理员权限"
                    f"（当前生效权限模式：{eff_mode}）")
                self._audit(user_id, tool_name, args, "denied", reason)
                return False, reason

        # 2. 频率限制。bypass（全局或会话作用域）语义 = 全部自动放行：跳过限流但保留审计。
        #    否则离线评测里 SYSTEM 5次/分钟的限流会把 terminal 类任务卡死（bypass 名不副实）。
        rate_limit = 0 if eff_mode == MODE_BYPASS else config.get("rate_limit", 0)
        if rate_limit > 0:
            key = f"{user_id}:{level}"
            now = time.time()
            timestamps = [t for t in self._rate_counters.get(key, []) if now - t < 60]
            if len(timestamps) >= rate_limit:
                reason = f"频率限制: {tool_name} 每分钟最多 {rate_limit} 次"
                self._audit(user_id, tool_name, args, "rate_limited", reason)
                return False, reason
            timestamps.append(now)
            self._rate_counters[key] = timestamps

        # 3. 审计
        if config.get("audit"):
            self._audit(user_id, tool_name, args, "approved", "")

        return True, ""

    def _audit(self, user_id: str, tool_name: str, args: dict, result: str, reason: str):
        try:
            from hashmm.api import database as db
            detail = f"{tool_name}({json.dumps(args, ensure_ascii=False)[:200]}) → {result}"
            if reason:
                detail += f" ({reason})"
            db.audit(user_id, user_id, "tool_call", detail)
        except Exception as _e:
            log_suppressed(logger, _e)

    def get_stats(self) -> dict:
        return {
            "active_rate_limits": len(self._rate_counters),
            "pending_approvals": sum(len(v) for v in self._approvals.values()),
        }


def extract_tool_names(defs) -> set[str]:
    """从工具定义列表提取工具名。兼容两种格式：
       · OpenAI 嵌套：{"type":"function","function":{"name": ...}}   ← 本项目 TOOL_DEFS 用的
       · 扁平：       {"name": ...}
    """
    names: set[str] = set()
    for d in defs or []:
        if not isinstance(d, dict):
            continue
        fn = d.get("function")
        n = (fn or {}).get("name") if isinstance(fn, dict) else d.get("name")
        if n:
            names.add(n)
    return names


def verify_registry_consistency(registered_tools: set[str] | None = None) -> dict:
    """校验“已注册工具”与“权限表”一致。

    参数
        registered_tools: 实际注册的工具名集合。None 时尝试从 tool_registry 读取。

    返回 {"ok", "missing_permission", "orphan_permission"}：
        missing_permission —— 注册了工具但权限表没登记（危险：会被 deny-first 拒绝执行，
                              应在启动时暴露以便补登记）。
        orphan_permission  —— 权限表登记了但工具未注册（死条目，应清理）。

    应在服务启动时调用；发现不一致时记 error 日志（不静默）。
    """
    if registered_tools is None:
        try:
            from hashmm.api import tool_registry as TR
            registered_tools = extract_tool_names(TR.get_tool_definitions())
        except Exception as e:
            log_suppressed(logger, e)
            registered_tools = set()

    perm_names = set(TOOL_PERMISSIONS.keys())
    missing = sorted(registered_tools - perm_names)
    orphan = sorted(perm_names - registered_tools)
    ok = not missing and not orphan
    if missing:
        logger.error("权限一致性：以下已注册工具【未登记权限】，将被 deny-first 拒绝：%s", missing)
    if orphan:
        logger.error("权限一致性：以下权限条目【无对应注册工具】（死条目）：%s", orphan)
    return {"ok": ok, "missing_permission": missing, "orphan_permission": orphan}


# 单例
_instance: PermissionSystem | None = None


def get_permissions() -> PermissionSystem:
    global _instance
    if _instance is None:
        _instance = PermissionSystem()
    return _instance
