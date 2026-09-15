"""本地隐私模式（对标腾讯 Marvis 隐私模式）。

开启后：
  1) 对话/消息**不再同步到云端 Supabase**，全部留在本机（代价：跨端历史不可用——这是隐私的取舍）。
  2) 若管理后台里配了**本地模型**（base_url 指向 localhost / 内网），自动**一键切到本地模型**并热重载，
     关闭时再切回原来的云端模型。没配本地模型就只断云同步、并如实告诉你 LLM 仍走云端。

状态（on + 之前的默认模型 id）持久化到 data/privacy_mode.json，重启保留；默认关 → 不开就跟以前完全一样。
所有「切模型」动作都包了 try/except：失败绝不影响主流程（最坏是没切成，chat 仍用当前模型）。
"""
from __future__ import annotations

import json
import os
import threading

_LOCK = threading.Lock()
_STATE: dict = {"on": None, "prev_default": None}  # on=None 表示未加载


def _path() -> str:
    base = os.environ.get("HASHMM_DB_PATH", "")
    d = os.path.dirname(base) if base else os.path.join(os.getcwd(), "data")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, "privacy_mode.json")


def _load_locked() -> None:
    if _STATE["on"] is not None:
        return
    on = os.environ.get("HASHMM_PRIVACY_MODE", "").strip().lower() in ("1", "true", "on", "yes")
    prev = None
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
            on = bool(d.get("on", on))
            prev = d.get("prev_default")
    except Exception:
        pass
    _STATE["on"] = on
    _STATE["prev_default"] = prev


def _persist_locked() -> None:
    try:
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump({"on": bool(_STATE["on"]), "prev_default": _STATE.get("prev_default")}, f)
    except Exception:
        pass


def _is_local_base(base: str) -> bool:
    base = (base or "").lower()
    if any(h in base for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal")):
        return True
    return base.startswith("http://192.168.") or base.startswith("http://10.") or base.startswith("http://172.")


def _find_local_model():
    try:
        from hashmm.api import database as db
        for m in (db.list_models() or []):
            if _is_local_base(str(m.get("base_url") or "")):
                return m
    except Exception:
        pass
    return None


def is_on() -> bool:
    with _LOCK:
        _load_locked()
        return bool(_STATE["on"])


def llm_is_local() -> bool:
    """当前默认模型 base_url 是否指向本机/内网。"""
    try:
        from hashmm.api import database as db
        m = db.get_default_model() or {}
        return _is_local_base(str(m.get("base_url") or ""))
    except Exception:
        return False


def local_model_available() -> bool:
    return _find_local_model() is not None


def _reload_llm() -> None:
    try:
        from hashmm.api.core.services import ServiceRegistry
        ServiceRegistry.reload_llm()
    except Exception:
        pass


def _switch_model(on: bool) -> None:
    """开 → 切到本地模型（记住原默认）；关 → 切回原默认。全程不抛错。"""
    try:
        from hashmm.api import database as db
        if on:
            lm = _find_local_model()
            if lm and lm.get("id"):
                cur = db.get_default_model() or {}
                cur_id = cur.get("id")
                # 只在「当前默认不是本地」时记住它，避免把本地记成 prev
                if cur_id and not _is_local_base(str(cur.get("base_url") or "")):
                    _STATE["prev_default"] = cur_id
                if cur_id != lm["id"]:
                    db.set_default_model(lm["id"])
                    _reload_llm()
        else:
            prev = _STATE.get("prev_default")
            if prev:
                db.set_default_model(prev)
                _reload_llm()
                _STATE["prev_default"] = None
    except Exception:
        pass


def set_on(on: bool) -> bool:
    on = bool(on)
    with _LOCK:
        _load_locked()
        _switch_model(on)        # best-effort 切模型（在锁内改 prev_default）
        _STATE["on"] = on
        _persist_locked()
    return on


def status() -> dict:
    return {
        "on": is_on(),
        "llm_local": llm_is_local(),
        "local_model_available": local_model_available(),
    }
