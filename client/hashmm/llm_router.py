"""Cloud/local LLM routing — cost + privacy (Marvis-style).

The system has two LLM backends available on this hardware:
  - **cloud** (DeepSeek): strong reasoning, but every call is slow, paid, and
    sends the query off-box;
  - **local** (Qwen2.5-7B on the 4090, via build_hf_llm_fn): fast, free, private,
    plenty good for *simple* tasks.

Today every call goes to the cloud. This router sends simple, high-volume,
low-stakes tasks (title generation, query rewrite, keyword extraction, intent
classification, short summaries) to the LOCAL model, and keeps complex reasoning
(the main grounded answer) on the cloud. Result: lower cost, lower latency on the
cheap stuff, and — for privacy-sensitive tenants — the option to keep everything
on-box.

Design:
  - **Opt-in.** Local routing only activates when a local model path is
    configured (HASHMM_LOCAL_LLM_PATH) AND HASHMM_LLM_ROUTING=1. Otherwise every
    task uses the cloud fn exactly as before (behaviour-preserving).
  - **Lazy + singleton.** The local model loads once on first local-routed call
    (costs VRAM), then is reused. If loading fails, we fall back to cloud.
  - **Privacy override.** A tenant marked privacy_local=1 (or
    HASHMM_PRIVACY_LOCAL=1) forces ALL its tasks local — nothing leaves the box.
  - **Observable.** Each routed call is tagged local/cloud so the savings show up
    in the cost dashboard.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.llm_router")

# Task classes that are cheap enough to run locally. These are short, structured,
# tolerant of a smaller model, and high-volume — exactly where cloud cost adds up.
LOCAL_TASKS = frozenset({
    "title",          # conversation title generation
    "rewrite",        # query rewrite / coreference resolution
    "multiquery",     # generate query variants (corrective retrieval)
    "keyword",        # KG keyword extraction
    "intent",         # intent / query classification
    "summary",        # short session/community summaries
})
# Everything else (notably "answer" — the main grounded response) stays cloud.
CLOUD_TASKS = frozenset({"answer", "reasoning", "judge"})

# Human-readable labels for the configurable tasks (admin UI / 角色→后端 可视化配置).
TASK_LABELS = {
    "title": "对话标题生成", "rewrite": "查询改写 / 指代消解", "multiquery": "多查询变体（纠错检索）",
    "keyword": "图谱关键词抽取", "intent": "意图 / 查询分类", "summary": "会话 / 社区摘要",
    "answer": "最终带来源回答", "reasoning": "复杂推理", "judge": "评判 / 打分",
}


# ── F11: per-task routing override (configurable, aligns with LightRAG role-specific LLM) ──
# Admins can override the hardcoded LOCAL/CLOUD defaults via the `llm_task_routing`
# setting (DB/env), a JSON map {task: "local"|"cloud"|"auto"}. "auto" / missing =
# fall back to the hardcoded default below. Empty config = current behaviour exactly.
_routing_override_cache: dict | None = None
_routing_override_raw: str = ""


def _task_routing_override() -> dict:
    """Read the configurable task→backend map. Cached on the raw string so a
    settings change is picked up without a restart. Never raises (returns {})."""
    global _routing_override_cache, _routing_override_raw
    try:
        from hashmm.api import settings_store
        raw = settings_store.get_setting("llm_task_routing", "") or ""
    except Exception:
        raw = os.environ.get("HASHMM_LLM_TASK_ROUTING", "") or ""
    if raw == _routing_override_raw and _routing_override_cache is not None:
        return _routing_override_cache
    parsed: dict = {}
    if raw.strip():
        try:
            import json as _json
            d = _json.loads(raw)
            if isinstance(d, dict):
                parsed = {str(k): str(v).lower() for k, v in d.items()
                          if str(v).lower() in ("local", "cloud", "auto")}
        except Exception:
            parsed = {}
    _routing_override_raw = raw
    _routing_override_cache = parsed
    return parsed


def _backend_for_task(task: str) -> str:
    """Resolve the desired backend for a task: override → hardcoded default.
    Returns 'local' | 'cloud' (never 'auto' — auto resolves to the default)."""
    ov = _task_routing_override().get(task, "auto")
    if ov == "local":
        return "local"
    if ov == "cloud":
        return "cloud"
    # auto / unset → hardcoded default
    return "local" if task in LOCAL_TASKS else "cloud"


def routing_enabled() -> bool:
    return (os.environ.get("HASHMM_LLM_ROUTING", "0") == "1"
            and bool(os.environ.get("HASHMM_LOCAL_LLM_PATH", "")))


def get_task_routing_config() -> dict:
    """当前「任务→后端」路由配置（给管理后台可视化配）。每个任务给：当前设置 /
    实际生效后端 / 硬编码默认。Never raises。"""
    try:
        ov = _task_routing_override()
    except Exception:
        ov = {}
    tasks = []
    for t in list(LOCAL_TASKS) + list(CLOUD_TASKS):
        tasks.append({
            "task": t,
            "label": TASK_LABELS.get(t, t),
            "setting": ov.get(t, "auto"),          # local | cloud | auto（auto=随默认）
            "effective": _backend_for_task(t),     # local | cloud（实际生效）
            "default": "local" if t in LOCAL_TASKS else "cloud",
        })
    return {"enabled": routing_enabled(),
            "local_path_set": bool(_local_path()),
            "tasks": tasks}


def save_task_routing(routing: dict) -> dict:
    """持久化「任务→后端」覆盖到 llm_task_routing 设置（只存 local/cloud；auto/其它丢弃，
    等价于回退默认）。空表 = 清空覆盖 = 完全恢复硬编码默认。返回保存后的最新配置。Never raises。"""
    global _routing_override_cache, _routing_override_raw
    known = LOCAL_TASKS | CLOUD_TASKS
    clean = {str(k): str(v).lower() for k, v in (routing or {}).items()
             if str(k) in known and str(v).lower() in ("local", "cloud")}
    try:
        import json as _json
        from hashmm.api import settings_store
        settings_store.set_setting(
            "llm_task_routing",
            _json.dumps(clean, ensure_ascii=False) if clean else "")
    except Exception as e:
        log_suppressed(logger, e)
    # 立刻让缓存失效，下次 _task_routing_override 重新读取
    _routing_override_cache = None
    _routing_override_raw = ""
    return get_task_routing_config()


def _local_path() -> str:
    return os.environ.get("HASHMM_LOCAL_LLM_PATH", "")


# ── Lazy local-model singleton ──────────────────────────────────────
_local_fn: Callable[[str], str] | None = None
_local_lock = threading.Lock()
_local_failed = False


def get_local_fn() -> Callable[[str], str] | None:
    """Load (once) and return the local LLM fn, or None if unavailable.

    Thread-safe; the first caller pays the load cost. On failure we remember it
    and stop retrying (so we don't repeatedly stall on a bad path)."""
    global _local_fn, _local_failed
    if _local_fn is not None:
        return _local_fn
    if _local_failed:
        return None
    path = _local_path()
    if not path:
        return None
    with _local_lock:
        if _local_fn is not None:
            return _local_fn
        if _local_failed:
            return None
        try:
            from hashmm.kg.local_hf_llm import build_hf_llm_fn
            logger.info(f"[LLMRouter] loading local model: {path}")
            _local_fn = build_hf_llm_fn(path, max_new_tokens=512, temperature=0.2)
            logger.info("[LLMRouter] local model ready")
            return _local_fn
        except Exception as e:
            logger.warning(f"[LLMRouter] local model load failed ({type(e).__name__}); "
                           f"all tasks will use cloud")
            _local_failed = True
            return None


def _privacy_forced_local(user_id: str | None) -> bool:
    """A tenant (or global flag) may require everything stay on-box."""
    if os.environ.get("HASHMM_PRIVACY_LOCAL", "0") == "1":
        return True
    if not user_id:
        return False
    try:
        from hashmm import tenancy
        if not tenancy.multi_tenant_enabled():
            return False
        from hashmm.api import database as db
        tid = tenancy.resolve_tenant(db, user_id)
        t = tenancy.get_tenant(db, tid)
        return bool(t.get("privacy_local", 0))
    except Exception as _e:
        log_suppressed(logger, _e)
        return False


def route_llm(task: str, cloud_fn: Callable[[str], str] | None,
              user_id: str | None = None) -> tuple[Callable[[str], str] | None, str]:
    """Pick the LLM fn for a task. Returns (fn, backend) where backend is
    'local' or 'cloud'. Falls back to cloud whenever local isn't usable, so it is
    always safe to call.

    - privacy-forced tenant → local for everything (cloud only if local missing);
    - routing on + task in LOCAL_TASKS → local (fallback cloud);
    - otherwise → cloud (current behaviour).
    """
    privacy = _privacy_forced_local(user_id)

    if privacy:
        local = get_local_fn()
        if local is not None:
            return local, "local"
        # privacy required but local unavailable: we must NOT silently send data
        # to cloud. Caller should treat None as "refuse / degrade".
        logger.warning("[LLMRouter] privacy-local required but local model unavailable")
        return (cloud_fn, "cloud") if cloud_fn else (None, "none")

    if routing_enabled() and _backend_for_task(task) == "local":
        local = get_local_fn()
        if local is not None:
            return local, "local"

    return cloud_fn, "cloud"


def record_routing(task: str, backend: str) -> None:
    """Best-effort: tag the routing decision for the cost dashboard."""
    try:
        from hashmm import observability as obs
        if hasattr(obs, "record_llm_routing"):
            obs.record_llm_routing(task, backend)
    except Exception as _e:
        log_suppressed(logger, _e)
