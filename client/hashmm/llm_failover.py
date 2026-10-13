"""llm_failover — 模型容灾链 + 每模型熔断器（V249，借鉴 OmniRoute 的 auto-fallback combo）。

OmniRoute 的核心可靠性做法（README/CLAUDE.md 原文可查）：
  ① 组合路由：主模型 配额尽/失败/超时 → 静默滑到链上的下一个模型；
  ② 每 provider 熔断器：CLOSED / OPEN / HALF_OPEN 三态，失败到阈值即熔断一段时间，
     冷却后放一条探针请求，成功即恢复——一个坏 provider 不拖慢所有请求。

适配到本项目（不照搬，做减法）：
  - 本项目已有多模型配置表（database.list_models / get_default_model）与统一出口
    get_active_llm_fn()——容灾链就包在这个出口上，**全后端零调用点改动**即生效。
  - 链 = [默认模型] + settings『model_fallbacks』（JSON 数组，元素为模型 id，顺序即优先级）。
  - 熔断参数保守：连续 3 次失败 → OPEN 60s；冷却后 HALF_OPEN 放行一条探针。
  - 只接管「纯文本调用」与 .chat / .quick_call（问答主链路的形态）；.stream /
    .call_with_tools 等流式/工具形态直通主模型不做切换——流中途换模型会产生
    拼接幻觉，宁可失败也不假装成功（严谨优先）。
  - 未配置 model_fallbacks 时行为与旧版完全一致（零风险默认关）。

对外：
  wrap_with_failover(fn, model)  -> (fn', model)   在 get_active_llm_fn 出口调用
  breaker_status()               -> {model_id: {...}}  给 /api/admin/models/health
  record_result(model_id, ok, latency_ms)          手动上报（wrap 内部也会调）
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.llm_failover")

# ── 熔断器（进程级；每模型一条记录）──
_FAIL_THRESHOLD = 3          # 连续失败达到即熔断
_OPEN_SECONDS = 60.0         # 熔断冷却时长
_LOCK = threading.Lock()
_BREAKERS: dict[str, dict] = {}   # id -> {fails, opened_until, half_open, last_ok_ms, last_err, ok_count, fail_count}


def _bk(mid: str) -> dict:
    b = _BREAKERS.get(mid)
    if b is None:
        b = {"fails": 0, "opened_until": 0.0, "half_open": False,
             "last_ok_ms": 0, "last_err": "", "ok_count": 0, "fail_count": 0}
        _BREAKERS[mid] = b
    return b


def record_result(model_id: str, ok: bool, latency_ms: int = 0, err: str = "") -> None:
    """上报一次调用结果，驱动熔断器状态机。永不抛错。"""
    try:
        with _LOCK:
            b = _bk(str(model_id))
            if ok:
                b["fails"] = 0
                b["opened_until"] = 0.0
                b["half_open"] = False
                b["ok_count"] += 1
                if latency_ms:
                    b["last_ok_ms"] = int(latency_ms)
            else:
                b["fails"] += 1
                b["fail_count"] += 1
                b["last_err"] = str(err)[:200]
                if b["half_open"]:          # 探针失败 → 直接再熔断一个周期
                    b["opened_until"] = time.time() + _OPEN_SECONDS
                    b["half_open"] = False
                    logger.warning(f"[failover] 模型 {model_id} 探针失败，续熔断 {_OPEN_SECONDS:.0f}s")
                elif b["fails"] >= _FAIL_THRESHOLD:
                    b["opened_until"] = time.time() + _OPEN_SECONDS
                    b["half_open"] = False
                    logger.warning(f"[failover] 模型 {model_id} 连续失败 {b['fails']} 次，熔断 {_OPEN_SECONDS:.0f}s")
    except Exception as e:
        log_suppressed(logger, e)


def _can_try(model_id: str) -> bool:
    """CLOSED 可试；OPEN 未到冷却跳过；冷却到期→进入 HALF_OPEN 只放一条探针，
    探针成功（record_result ok）恢复 CLOSED，失败则重新 OPEN 一个周期。"""
    with _LOCK:
        b = _bk(str(model_id))
        now = time.time()
        if b["opened_until"] <= 0:          # CLOSED
            return True
        if b["opened_until"] > now:         # OPEN 未到期
            return False
        if not b["half_open"]:              # 冷却到期：放行一条探针
            b["half_open"] = True
            return True
        return False                        # 探针在途/未回：其余请求继续等


def breaker_status() -> dict:
    """给 /api/admin/models/health：每个模型的熔断态与计数快照。"""
    out: dict[str, dict] = {}
    now = time.time()
    with _LOCK:
        for mid, b in _BREAKERS.items():
            state = "open" if b["opened_until"] > now else ("half_open" if b["half_open"] else "closed")
            out[mid] = {
                "state": state,
                "consecutive_fails": b["fails"],
                "open_remaining_s": max(0, int(b["opened_until"] - now)),
                "ok_count": b["ok_count"], "fail_count": b["fail_count"],
                "last_ok_latency_ms": b["last_ok_ms"], "last_error": b["last_err"],
            }
    return out


# ── 容灾链解析 ──
def fallback_ids() -> list[str]:
    """settings『model_fallbacks』：JSON 数组（模型 id，顺序即优先级）。解析失败＝空链。"""
    try:
        from hashmm.api import settings_store
        raw = (settings_store.get_setting("model_fallbacks", "") or "").strip()
        if not raw:
            return []
        arr = json.loads(raw)
        return [str(x) for x in arr if str(x).strip()][:5]
    except Exception as e:
        log_suppressed(logger, e)
        return []


def _resolve_chain(primary_model: dict | None) -> list[dict]:
    """[默认模型] + fallbacks（按 id 从模型表解析；跳过与默认重复的）。"""
    chain: list[dict] = []
    if primary_model:
        chain.append(primary_model)
    ids = fallback_ids()
    if not ids:
        return chain
    try:
        from hashmm.api import database as db
        by_id = {str(m.get("id")): m for m in (db.list_models() or [])}
        pid = str((primary_model or {}).get("id", ""))
        for mid in ids:
            if mid and mid != pid and mid in by_id:
                chain.append(by_id[mid])
    except Exception as e:
        log_suppressed(logger, e)
    return chain


def _mk_fn(model_cfg: dict):
    from hashmm.api.model_manager import make_llm_fn_from_model
    return make_llm_fn_from_model(model_cfg)


def _looks_empty(resp: Any) -> bool:
    return resp is None or (isinstance(resp, str) and not resp.strip())


def wrap_with_failover(fn: Callable | None, model: dict | None):
    """在 get_active_llm_fn 出口调用：无 fallbacks 配置或无主模型时原样返回（零变化）。
    有链时返回代理函数：纯文本调用 / .chat / .quick_call 依链切换；其余属性直通主模型。"""
    if fn is None or model is None:
        return fn, model
    chain = _resolve_chain(model)
    if len(chain) <= 1:
        return fn, model   # 没配容灾链：行为与旧版一致

    primary_id = str(model.get("id", model.get("model_name", "primary")))

    def _run_chain(op: str, *args, **kwargs):
        last_exc: Exception | None = None
        for cfg in chain:
            mid = str(cfg.get("id", cfg.get("model_name", "")))
            if not _can_try(mid):
                continue
            f = fn if mid == primary_id else _mk_fn(cfg)
            if f is None:
                record_result(mid, False, err="make_llm_fn 失败（配置无效）")
                continue
            target = f if op == "call" else getattr(f, op, None)
            if target is None:
                continue
            t0 = time.time()
            try:
                resp = target(*args, **kwargs)
                if _looks_empty(resp):
                    raise RuntimeError("空响应")
                record_result(mid, True, int((time.time() - t0) * 1000))
                if mid != primary_id:
                    logger.info(f"[failover] 主模型不可用，已切换到 {cfg.get('name') or mid}")
                return resp
            except Exception as e:   # noqa: BLE001 —— 容灾链的意义就是接住任意上游异常
                record_result(mid, False, int((time.time() - t0) * 1000), err=str(e))
                last_exc = e
                continue
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("容灾链上没有可用模型（全部熔断或配置无效）")

    def call(prompt: str) -> str:
        return _run_chain("call", prompt)

    def chat(messages: list, max_tok=None) -> str:
        return _run_chain("chat", messages, max_tok=max_tok) if max_tok is not None else _run_chain("chat", messages)

    def quick_call(system_prompt: str, user_prompt: str, max_tok: int = 200) -> str:
        return _run_chain("quick_call", system_prompt, user_prompt, max_tok=max_tok)

    # 流式 / 工具调用 / 计数等形态：直通主模型（流中途换模型＝拼接幻觉，宁失败不造假）
    for attr in (
        "stream", "call_with_tools", "stream_with_tools", "count_tokens", "client",
        "model_name", "provider", "provider_profile", "wire_api",
    ):
        v = getattr(fn, attr, None)
        if v is not None:
            setattr(call, attr, v)
    call.chat = chat                    # type: ignore[attr-defined]
    call.quick_call = quick_call        # type: ignore[attr-defined]
    call.failover_chain = [str(c.get("name") or c.get("id")) for c in chain]   # type: ignore[attr-defined]
    return call, model
