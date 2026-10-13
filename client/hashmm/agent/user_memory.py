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

# V276 长期记忆场景分类（面试资料 5.3）：不同场景沉淀不同类别的记忆，
# 注入时分组呈现，让模型更好地个性化。旧记录无类别 → 默认「偏好」，完全向后兼容。
CATEGORIES = {
    "preference": "用户偏好",       # 以后代码用中文注释、喜欢结构化回答……
    "behavior":   "行为模式",       # 历史行为/习惯（常在晚上工作、偏好先看结论）
    "topic":      "关注话题",       # 长期关注的主题/领域（跨模态哈希、RAG）
    "issue":      "历史事项",       # 遇到过的问题/解决记录（客服/售后场景）
}
DEFAULT_CATEGORY = "preference"


def _norm_cat(cat: str) -> str:
    c = str(cat or "").strip().lower()
    return c if c in CATEGORIES else DEFAULT_CATEGORY


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


def remember(user_id: str, key: str, value: str, category: str = DEFAULT_CATEGORY) -> dict:
    """记一条长期记忆（同 key 覆盖）。返回 {ok, count} 或 {ok: False, reason}。

    category（V276，资料 5.3）：preference/behavior/topic/issue 之一，非法值归为偏好。
    旧调用 remember(uid, k, v) 不传 category 仍工作（默认偏好），完全向后兼容。
    """
    if not enabled():
        return {"ok": False, "reason": "用户记忆未启用（HASHMM_USER_MEMORY=1 开启）"}
    key = str(key or "").strip()[:60]
    value = str(value or "").strip()[:MAX_VALUE_LEN]
    cat = _norm_cat(category)
    if not key or not value:
        return {"ok": False, "reason": "key 和 value 都不能为空"}
    try:
        mem = _load(user_id)
        if key not in mem and len(mem) >= MAX_ITEMS:
            # 满了：淘汰最旧的一条（按更新时间）
            oldest = min(mem.items(), key=lambda kv: kv[1].get("ts", 0))[0]
            mem.pop(oldest, None)
        mem[key] = {"v": value, "ts": time.time(), "cat": cat}
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


def recall_grouped(user_id: str) -> dict:
    """按类别分组返回 {category: {key: value}}（V276，资料 5.3）。
    旧记录无 cat 字段 → 归入「偏好」。关闭/无数据/出错 → 空 dict。"""
    if not enabled():
        return {}
    try:
        out: dict[str, dict] = {}
        for k, v in _load(user_id).items():
            cat = _norm_cat(v.get("cat", DEFAULT_CATEGORY))
            out.setdefault(cat, {})[k] = v.get("v", "")
        return out
    except Exception:
        return {}


def inject_block(user_id: str) -> str:
    """生成系统提示注入段（≤800 字；关闭/无数据 → 空串）。

    V276：按场景类别分组呈现（资料 5.3）——偏好/行为模式/关注话题/历史事项各成一节，
    模型据此做更贴合场景的个性化。只有一类时退化为单节（与旧版观感一致）。
    """
    grouped = recall_grouped(user_id)
    if not grouped:
        return ""
    parts = ["## 用户长期记忆（跨会话，遵循并善用）"]
    for cat, label in CATEGORIES.items():   # 固定顺序：偏好→行为→话题→事项
        items = grouped.get(cat)
        if not items:
            continue
        parts.append(f"### {label}")
        parts.extend(f"- {k}: {v}" for k, v in sorted(items.items()))
    block = "\n".join(parts)
    return block[:MAX_INJECT_LEN]
