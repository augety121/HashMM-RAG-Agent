"""Session 运行时补丁（V204，对标 Qoder Cloud Agents 的「Session 动态 Patch」）。

运行中的会话可直接改配置——不用归档旧会话重建新会话：
    PATCH /api/conversations/{cid}/runtime   body 里给要改的键
下一轮 turn 立即生效，上下文一点不丢。支持的键（白名单，其余忽略）：
    model          str    模型偏好（传给路由层 user_prefs，本地单模型部署时为提示性）
    temperature    float  0~2，Agent 循环与直答路径都生效
    system_append  str    追加到 system prompt 末尾（A/B 调 prompt 的主力；≤4000 字）
    tools_allow    [str]  只允许这些工具（空/缺省=不限制）
    tools_deny     [str]  禁用这些工具（在 allow 之后再扣除）
把某键的值传 null 即删除该键；DELETE 整个 runtime 清空全部补丁。

存储：独立小 SQLite <DATA_DIR>/session_runtime.db（conv_id 主键 + JSON），
与业务库解耦；全部操作永不抛错，补丁层任何故障都退化为"无补丁"。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.api.session_runtime")

_LOCK = threading.Lock()
_ALLOWED_KEYS = ("model", "temperature", "system_append", "tools_allow", "tools_deny")
_MAX_SYSTEM_APPEND = 4000


def _db_path() -> Path:
    d = os.environ.get("HASHMM_DATA_DIR") or os.environ.get("DATA_DIR") or "data"
    return Path(d).expanduser().resolve() / "session_runtime.db"


def _conn() -> sqlite3.Connection:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=5)
    c.execute(
        "CREATE TABLE IF NOT EXISTS session_runtime ("
        " conv_id TEXT PRIMARY KEY,"
        " overrides TEXT NOT NULL,"
        " updated_at REAL NOT NULL)"
    )
    return c


def _sanitize(patch: dict) -> tuple[dict, dict]:
    """按白名单清洗补丁：返回 (要设置的键值, 要删除的键集)。"""
    setv: dict[str, Any] = {}
    delv: set[str] = set()
    for k, v in (patch or {}).items():
        if k not in _ALLOWED_KEYS:
            continue
        if v is None:
            delv.add(k)
            continue
        if k == "temperature":
            try:
                setv[k] = max(0.0, min(2.0, float(v)))
            except Exception:
                continue
        elif k == "system_append":
            s = str(v).strip()
            if s:
                setv[k] = s[:_MAX_SYSTEM_APPEND]
            else:
                delv.add(k)
        elif k in ("tools_allow", "tools_deny"):
            if isinstance(v, str):
                v = [x.strip() for x in v.split(",")]
            items = [str(x).strip() for x in (v or []) if str(x).strip()]
            if items:
                setv[k] = items[:64]
            else:
                delv.add(k)
        elif k == "model":
            s = str(v).strip()[:120]
            if s:
                setv[k] = s
            else:
                delv.add(k)
    return setv, {k: None for k in delv}


def get_overrides(conv_id: str) -> dict:
    """读某会话的运行时补丁；无/失败 → {}。"""
    if not conv_id:
        return {}
    try:
        with _LOCK, _conn() as c:
            row = c.execute(
                "SELECT overrides, updated_at FROM session_runtime WHERE conv_id=?", (conv_id,)
            ).fetchone()
        if not row:
            return {}
        ov = json.loads(row[0]) or {}
        ov["_updated_at"] = row[1]
        return ov
    except Exception as e:
        log_suppressed(logger, e, "runtime.get")
        return {}


def patch_overrides(conv_id: str, patch: dict) -> dict:
    """合并式打补丁（值为 null 的键删除），返回补丁后的完整 overrides。"""
    if not conv_id:
        return {}
    setv, delv = _sanitize(patch)
    try:
        with _LOCK, _conn() as c:
            row = c.execute(
                "SELECT overrides FROM session_runtime WHERE conv_id=?", (conv_id,)
            ).fetchone()
            cur = json.loads(row[0]) if row else {}
            cur.update(setv)
            for k in delv:
                cur.pop(k, None)
            if cur:
                c.execute(
                    "INSERT INTO session_runtime (conv_id, overrides, updated_at) VALUES (?,?,?)"
                    " ON CONFLICT(conv_id) DO UPDATE SET overrides=excluded.overrides,"
                    " updated_at=excluded.updated_at",
                    (conv_id, json.dumps(cur, ensure_ascii=False), time.time()),
                )
            else:
                c.execute("DELETE FROM session_runtime WHERE conv_id=?", (conv_id,))
        logger.info(f"session runtime patched: conv={conv_id} keys={sorted(setv) + sorted(delv)}")
        return cur
    except Exception as e:
        log_suppressed(logger, e, "runtime.patch")
        return get_overrides(conv_id)


def clear_overrides(conv_id: str) -> None:
    try:
        with _LOCK, _conn() as c:
            c.execute("DELETE FROM session_runtime WHERE conv_id=?", (conv_id,))
    except Exception as e:
        log_suppressed(logger, e, "runtime.clear")


def apply_to_loop(loop: Any, ov: dict) -> list[str]:
    """把补丁应用到 AgentLoop 实例（下一轮 turn 生效的落点）。返回生效项描述（进 trace）。"""
    applied: list[str] = []
    if not ov:
        return applied
    try:
        t = ov.get("temperature")
        if isinstance(t, (int, float)):
            loop.temperature = float(t)
            applied.append(f"temperature={t}")
        sa = ov.get("system_append")
        if sa:
            loop.runtime_system_append = str(sa)
            applied.append(f"system_append(+{len(str(sa))}字)")
        allow = set(ov.get("tools_allow") or [])
        deny = set(ov.get("tools_deny") or [])
        if allow or deny:
            before = len(loop.tools or [])
            def _keep(t_schema: dict) -> bool:
                name = str(t_schema.get("name", ""))
                if allow and name not in allow:
                    return False
                if name in deny:
                    return False
                return True
            loop.tools = [t for t in (loop.tools or []) if _keep(t)]
            applied.append(f"tools {before}->{len(loop.tools)}")
        if ov.get("model"):
            fn, label = resolve_llm_fn(str(ov["model"]))
            if fn is not None:
                loop.llm_fn = fn
                applied.append(f"model→{label}")
            else:
                applied.append(f"model?{label}")
    except Exception as e:
        log_suppressed(logger, e, "runtime.apply")
    return applied


def resolve_llm_fn(model_query: str):
    """V205 P0-3：把 runtime.model 变成真实热切换。
    按 精确id → 精确name → 精确model_name → 名称子串（不区分大小写）匹配
    管理后台已配置的模型，用 model_manager 同款工厂构建 llm_fn。
    返回 (llm_fn, 解析出的展示名)；找不到/构建失败返回 (None, 原因)。永不抛错。"""
    q = (model_query or "").strip()
    if not q:
        return None, "空模型名"
    try:
        from hashmm.api import database as _db
        from hashmm.api.model_manager import make_llm_fn_from_model as _mk
        rows = _db.list_models() or []
        ql = q.lower()
        def _pick():
            for r in rows:
                if str(r.get("id", "")) == q:
                    return r
            for key in ("name", "model_name"):
                for r in rows:
                    if str(r.get(key, "")).lower() == ql:
                        return r
            for r in rows:
                if ql in str(r.get("name", "")).lower() or ql in str(r.get("model_name", "")).lower():
                    return r
            return None
        row = _pick()
        if not row:
            return None, f"未找到匹配模型：{q}（可在管理后台查看已配置模型）"
        fn = _mk(dict(row))
        if not fn:
            return None, f"模型 {row.get('name') or row.get('model_name')} 构建失败（检查 API Key / URL）"
        label = str(row.get("name") or row.get("model_name") or q)
        return fn, label
    except Exception as e:  # pragma: no cover - 防御
        log_suppressed(logger, e, "runtime.resolve_model")
        return None, str(e)[:120]
