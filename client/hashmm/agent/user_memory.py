"""hashmm/agent/user_memory.py — 用户级长期记忆（V80，方案 E 阶段）。

对标 ChatGPT Memory 的轻量一期：模型在对话中发现用户的**长期偏好**
（"以后代码都用中文注释"、"我的论文领域是跨模态哈希"）时主动调用
remember_preference 记下；下次任何会话开始时自动注入系统提示。

与会话记忆（ctx 表）的分层：ctx=本会话内的事实；本模块=跨会话的用户画像。

安全设计（铁律）：
- **默认关**：HASHMM_USER_MEMORY=1 才生效（写入与注入都受控）；
- **永不抛错**：任何 IO 故障静默返回；
- **容量限制**：每用户 ≤30 条、每条 ≤200 字、注入 ≤800 字（防提示膨胀）；
- 存储在 data/user_memory/<user_id>.json（用户数据，交付打包自动排除）。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

MAX_ITEMS = 30
MAX_VALUE_LEN = 200
MAX_INJECT_LEN = 800


def enabled() -> bool:
    return os.environ.get("HASHMM_USER_MEMORY", "0").strip().lower() in {"1", "true", "yes", "on"}


def _dir() -> Path:
    d = Path(os.environ.get("HASHMM_USER_MEMORY_DIR", "data/user_memory"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(user_id: str) -> Path:
    safe = re.sub(r"[^\w\-]", "_", str(user_id or "default"))[:64] or "default"
    return _dir() / f"{safe}.json"


def _load(user_id: str) -> dict:
    try:
        return json.loads(_path(user_id).read_text(encoding="utf-8"))
    except Exception:
        return {}


def remember(user_id: str, key: str, value: str) -> dict:
    """记一条偏好（同 key 覆盖）。返回 {ok, count} 或 {ok: False, reason}。"""
    if not enabled():
        return {"ok": False, "reason": "用户记忆未启用（HASHMM_USER_MEMORY=1 开启）"}
    key = str(key or "").strip()[:60]
    value = str(value or "").strip()[:MAX_VALUE_LEN]
    if not key or not value:
        return {"ok": False, "reason": "key 和 value 都不能为空"}
    try:
        mem = _load(user_id)
        if key not in mem and len(mem) >= MAX_ITEMS:
            # 满了：淘汰最旧的一条（按更新时间）
            oldest = min(mem.items(), key=lambda kv: kv[1].get("ts", 0))[0]
            mem.pop(oldest, None)
        mem[key] = {"v": value, "ts": time.time()}
        _path(user_id).write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": True, "count": len(mem)}
    except Exception as e:
        return {"ok": False, "reason": f"写入失败: {type(e).__name__}"}


def forget(user_id: str, key: str) -> dict:
    if not enabled():
        return {"ok": False, "reason": "用户记忆未启用"}
    try:
        mem = _load(user_id)
        existed = mem.pop(str(key or "").strip()[:60], None) is not None
        _path(user_id).write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": True, "removed": existed, "count": len(mem)}
    except Exception as e:
        return {"ok": False, "reason": f"删除失败: {type(e).__name__}"}


def recall(user_id: str) -> dict:
    """全部偏好 {key: value}（关闭/无数据/出错 → 空 dict）。"""
    if not enabled():
        return {}
    try:
        return {k: v.get("v", "") for k, v in _load(user_id).items()}
    except Exception:
        return {}


def inject_block(user_id: str) -> str:
    """生成系统提示注入段（≤800 字；关闭/无数据 → 空串）。"""
    mem = recall(user_id)
    if not mem:
        return ""
    lines = [f"- {k}: {v}" for k, v in sorted(mem.items())]
    block = "## 用户长期偏好（跨会话记忆，遵循它们）\n" + "\n".join(lines)
    return block[:MAX_INJECT_LEN]
