"""Owner-scoped settings with explicit revisions and deterministic defaults.

This store is intentionally limited to non-secret account preferences. Device
paths and desktop capability availability stay in Electron; credentials stay
in the encrypted credential stores.  Every write is owner-bound, revisioned
and auditable by the route that invokes it.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from hashmm.api import database as db


class SettingValidationError(ValueError):
    pass


class SettingConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class Definition:
    default: Any
    validate: Callable[[Any], Any]
    description: str


def _boolean(value: Any) -> bool:
    if not isinstance(value, bool):
        raise SettingValidationError("设置值必须是布尔值")
    return value


def _choice(*allowed: str) -> Callable[[Any], str]:
    def validate(value: Any) -> str:
        if not isinstance(value, str) or value not in allowed:
            raise SettingValidationError("设置值不在允许范围内")
        return value
    return validate


def _text(limit: int) -> Callable[[Any], str]:
    def validate(value: Any) -> str:
        if not isinstance(value, str):
            raise SettingValidationError("设置值必须是文本")
        if len(value) > limit:
            raise SettingValidationError(f"设置文本不能超过 {limit} 个字符")
        return value
    return validate


def _integer(minimum: int, maximum: int) -> Callable[[Any], int]:
    def validate(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingValidationError("setting value must be an integer")
        if value < minimum or value > maximum:
            raise SettingValidationError(
                f"setting value must be between {minimum} and {maximum}"
            )
        return value
    return validate


DEFINITIONS: dict[str, Definition] = {
    "general.language": Definition("zh-CN", _choice("zh-CN", "en-US", "system"), "Interface language"),
    "general.send_behavior": Definition("enter", _choice("enter", "ctrl_enter"), "Message send shortcut"),
    # Durable next-turn queueing is not yet wired to the Agent run state
    # machine.  Expose only the effective behavior instead of persisting a
    # preference that the runtime silently ignores.
    "general.followup_mode": Definition("steer", _choice("steer"), "Follow-up behavior during a run"),
    "general.prevent_sleep": Definition(True, _boolean, "Prevent device sleep while work is active"),
    "appearance.theme": Definition("system", _choice("system", "light", "dark"), "Theme"),
    "appearance.density": Definition("comfortable", _choice("comfortable", "compact"), "Navigation and message density"),
    "notifications.desktop": Definition(False, _boolean, "Desktop notifications"),
    "notifications.sound": Definition(False, _boolean, "Sound notifications"),
    "notifications.task_complete": Definition(True, _boolean, "Task completion notifications"),
    "notifications.approval": Definition(True, _boolean, "Approval request notifications"),
    # V1100 bounded continuity domains.  The legacy handoff key remains only
    # for additive migration; new clients never combine these controls.
    "notifications.chat_continuation": Definition(True, _boolean, "New Chat continuation notifications"),
    "notifications.chat_mail": Definition(True, _boolean, "Chat mailbox notifications"),
    "notifications.device_resume": Definition(True, _boolean, "App/device resume notifications"),
    "notifications.handoff": Definition(True, _boolean, "Deprecated combined continuity notification"),
    "personalization.memory_enabled": Definition(True, _boolean, "Long-term memory"),
    "browser.external_submit_confirmation": Definition("always", _choice("always", "sensitive"), "External submission confirmation"),
    "computer.mode": Definition("strict", _choice("read_only", "standard", "strict"), "Computer-use safety mode"),
    "worktrees.auto_cleanup": Definition(False, _boolean, "Safe automatic worktree cleanup"),
    "worktrees.keep_limit": Definition(15, _integer(1, 100), "Worktree retention limit"),
    "personalization.answer_style": Definition(
        "analytical", _choice("factual", "analytical", "creative"), "默认回答风格",
    ),
    "personalization.custom_prompt": Definition("", _text(2000), "账号级自定义指令"),
    "capabilities.browser": Definition(True, _boolean, "受控浏览器入口"),
    "capabilities.computer": Definition(True, _boolean, "电脑操作入口"),
    "capabilities.canvas": Definition(True, _boolean, "工作画布入口"),
    "capabilities.team": Definition(True, _boolean, "多智能体协作入口"),
    "agent.approval_mode": Definition(
        "ask", _choice("ask", "workspace"), "默认审批偏好；服务端策略仍可收紧",
    ),
}


def _ensure_table() -> None:
    with db._conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_settings_v2 (
                user_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                updated_at DOUBLE PRECISION NOT NULL DEFAULT 0,
                updated_by TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (user_id, key)
            )
        """)
        # One-time compatibility projection from the old combined toggle.
        # INSERT OR IGNORE preserves any explicit per-domain choice.
        for key in (
            "notifications.chat_continuation",
            "notifications.chat_mail",
            "notifications.device_resume",
        ):
            conn.execute(
                "INSERT OR IGNORE INTO user_settings_v2(user_id,key,value_json,revision,updated_at,updated_by) "
                "SELECT user_id,?,value_json,revision,updated_at,'migration:v1100' "
                "FROM user_settings_v2 WHERE key='notifications.handoff'",
                (key,),
            )
        conn.execute(
            "UPDATE user_settings_v2 SET value_json='\"steer\"',revision=revision+1,"
            "updated_at=?,updated_by='migration:v1100-followup' "
            "WHERE key='general.followup_mode' AND value_json='\"queue\"'",
            (time.time(),),
        )


def _wire(key: str, row: Any | None) -> dict[str, Any]:
    definition = DEFINITIONS[key]
    if row is None:
        value = definition.default
        revision = 0
        updated_at = 0.0
        source = "default"
    else:
        try:
            value = definition.validate(json.loads(str(row["value_json"])))
        except Exception:
            value = definition.default
            source = "default_invalid_stored_value"
        else:
            source = "user"
        revision = int(row["revision"] or 0)
        updated_at = float(row["updated_at"] or 0)
    return {
        "key": key,
        "scope": "user",
        "desired_value": value,
        "effective_value": value,
        "source": source,
        "editable": True,
        "managed_by": "current_user",
        "revision": revision,
        "updated_at": updated_at,
        "restart_required": False,
        "availability": "available",
        "description": definition.description,
    }


def list_settings(owner_id: str) -> list[dict[str, Any]]:
    _ensure_table()
    with db._conn() as conn:
        rows = {
            str(row["key"]): row
            for row in conn.execute(
                "SELECT key,value_json,revision,updated_at FROM user_settings_v2 WHERE user_id=?",
                (owner_id,),
            ).fetchall()
        }
    return [_wire(key, rows.get(key)) for key in DEFINITIONS]


def update_settings(
    owner_id: str,
    actor: str,
    changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply a bounded batch with optimistic revisions in one transaction."""
    if not changes or len(changes) > 32:
        raise SettingValidationError("changes 必须包含 1 到 32 个设置")
    normalized: list[tuple[str, Any, int]] = []
    seen: set[str] = set()
    for raw in changes:
        if not isinstance(raw, dict):
            raise SettingValidationError("设置变更格式无效")
        key = str(raw.get("key") or "")
        if key not in DEFINITIONS or key in seen:
            raise SettingValidationError(f"未知或重复的设置项: {key}")
        revision = raw.get("revision")
        if not isinstance(revision, int) or revision < 0:
            raise SettingValidationError(f"设置 {key} 缺少有效 revision")
        normalized.append((key, DEFINITIONS[key].validate(raw.get("value")), revision))
        seen.add(key)

    _ensure_table()
    now = time.time()
    with db._conn() as conn:
        for key, value, expected in normalized:
            row = conn.execute(
                "SELECT revision FROM user_settings_v2 WHERE user_id=? AND key=?",
                (owner_id, key),
            ).fetchone()
            actual = int(row["revision"] or 0) if row else 0
            if actual != expected:
                raise SettingConflictError(f"设置 {key} 已在其他设备修改")
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if row:
                cursor = conn.execute(
                    "UPDATE user_settings_v2 SET value_json=?,revision=revision+1,updated_at=?,updated_by=? "
                    "WHERE user_id=? AND key=? AND revision=?",
                    (encoded, now, actor, owner_id, key, expected),
                )
                if cursor.rowcount != 1:
                    raise SettingConflictError(f"设置 {key} 已在其他设备修改")
            else:
                conn.execute(
                    "INSERT INTO user_settings_v2(user_id,key,value_json,revision,updated_at,updated_by) "
                    "VALUES(?,?,?,?,?,?)",
                    (owner_id, key, encoded, 1, now, actor),
                )
    return list_settings(owner_id)
