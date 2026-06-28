"""Permission System — deny-first 权限控制。

对标 Claude Code 的七层权限模型（简化版）。
每个工具调用都经过权限检查 + 审计记录。

权限级别：
  read     → 自动批准（检索/读取）
  write    → 自动批准 + 审计（文件创建）
  execute  → 自动批准 + 审计（代码执行，沙箱内）
  network  → 自动批准 + 限速（联网操作）
  delete   → 需要确认（删除操作）
  system   → 需要管理员权限（Shell/系统）
"""
from __future__ import annotations

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


# 工具 → 权限映射
TOOL_PERMISSIONS: dict[str, str] = {
    "kb_search": PermissionLevel.READ,
    "kg_query": PermissionLevel.READ,
    "fetch_url": PermissionLevel.NETWORK,
    "web_search": PermissionLevel.NETWORK,
    "create_file": PermissionLevel.WRITE,
    "create_document": PermissionLevel.WRITE,
    "execute_code": PermissionLevel.EXECUTE,
    "read_file": PermissionLevel.READ,
    "edit_file": PermissionLevel.WRITE,
    "list_files": PermissionLevel.READ,
    "shell_exec": PermissionLevel.SYSTEM,
    "delete_doc": PermissionLevel.DELETE,
    "clean_workspace": PermissionLevel.DELETE,
    "spawn_worker": PermissionLevel.READ,
    "remember_preference": PermissionLevel.WRITE,  # V80 用户记忆写入  # delegating a sub-task is read-level
}

# 权限配置
PERMISSION_CONFIG: dict[str, dict[str, Any]] = {
    PermissionLevel.READ:    {"auto_approve": True,  "audit": False, "rate_limit": 0},
    PermissionLevel.WRITE:   {"auto_approve": True,  "audit": True,  "rate_limit": 0},
    PermissionLevel.EXECUTE: {"auto_approve": True,  "audit": True,  "rate_limit": 20},  # 20/min
    PermissionLevel.NETWORK: {"auto_approve": True,  "audit": True,  "rate_limit": 30},  # 30/min
    PermissionLevel.DELETE:  {"auto_approve": False, "audit": True,  "rate_limit": 5},
    PermissionLevel.SYSTEM:  {"auto_approve": False, "audit": True,  "rate_limit": 5},
}


class PermissionSystem:
    """权限检查 + 审计日志。"""

    def __init__(self):
        self._rate_counters: dict[str, list[float]] = {}

    def check(self, tool_name: str, args: dict, user_id: str) -> tuple[bool, str]:
        """检查工具调用权限。

        Returns:
            (approved, reason)
        """
        level = TOOL_PERMISSIONS.get(tool_name, PermissionLevel.READ)
        config = PERMISSION_CONFIG.get(level, PERMISSION_CONFIG[PermissionLevel.READ])

        # 1. 自动批准检查
        if not config["auto_approve"]:
            # 需要管理员权限的工具
            try:
                from hashmm.api import database as db
                user = db.get_user_by_id(user_id) if user_id else None
                is_admin = user and user.get("role") == "admin" if user else False
                if not is_admin:
                    reason = f"工具 {tool_name} 需要管理员权限"
                    self._audit(user_id, tool_name, args, "denied", reason)
                    return False, reason
            except Exception as _e:
                log_suppressed(logger, _e)

        # 2. 频率限制
        rate_limit = config.get("rate_limit", 0)
        if rate_limit > 0:
            key = f"{user_id}:{level}"
            now = time.time()
            timestamps = self._rate_counters.get(key, [])
            # 清理过期（1分钟窗口）
            timestamps = [t for t in timestamps if now - t < 60]
            if len(timestamps) >= rate_limit:
                reason = f"频率限制: {tool_name} 每分钟最多 {rate_limit} 次"
                self._audit(user_id, tool_name, args, "rate_limited", reason)
                return False, reason
            timestamps.append(now)
            self._rate_counters[key] = timestamps

        # 3. 审计记录
        if config.get("audit"):
            self._audit(user_id, tool_name, args, "approved", "")

        return True, ""

    def _audit(self, user_id: str, tool_name: str, args: dict, result: str, reason: str):
        """记录审计日志。"""
        try:
            from hashmm.api import database as db
            detail = f"{tool_name}({json.dumps(args, ensure_ascii=False)[:200]}) → {result}"
            if reason:
                detail += f" ({reason})"
            db.audit(user_id, user_id, "tool_call", detail)
        except Exception as _e:
            log_suppressed(logger, _e)

    def get_stats(self) -> dict:
        """获取权限统计。"""
        return {
            "active_rate_limits": len(self._rate_counters),
        }


# 单例
import json

_instance: PermissionSystem | None = None


def get_permissions() -> PermissionSystem:
    global _instance
    if _instance is None:
        _instance = PermissionSystem()
    return _instance
