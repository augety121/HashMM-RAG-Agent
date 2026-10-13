"""LLM prompt 缓存（P2-2）—— 对标 Codex prompt_cache_key 的应用层等价。

背景：Codex 用 provider 端的 prompt_cache_key 复用稳定前缀的 KV 缓存，降延迟/成本。
我们没有 provider 端 KV 控制，但能做**应用层的确定性任务结果缓存**：
对**确定性、低随机性的辅助 LLM 调用**（如 title/keyword/intent/rewrite/summary 这类，
同样的输入应得到同样的输出），缓存其结果，重复调用直接命中，省一次 LLM 往返。

安全设计：
- **只缓存确定性辅助任务**（_CACHEABLE_TASKS），**绝不缓存主答案**（answer/reasoning）——
  主答案应每次新鲜生成。
- **默认关**：HASHMM_PROMPT_CACHE 未开 → 直接透传，零行为变化。
- 复用现有 pipeline_cache 的 LRU+TTL 存储（不引入新依赖）。
- key 基于 (task, model, messages 内容) 的 sha256，输入完全一致才命中。
- 永不抛错：缓存层任何异常都退化为"直接调 LLM"。

用法：
    from hashmm.prompt_cache import cached_llm_call
    result = cached_llm_call("keyword", model, messages, lambda: real_llm_call(messages))
"""
from __future__ import annotations

import hashlib
import json
import os

# 可缓存的确定性辅助任务（与 llm_router 的 LOCAL_TASKS 思路一致）。
# 绝不含 answer/reasoning/judge —— 主答案永远新鲜生成。
_CACHEABLE_TASKS = frozenset({
    "title", "keyword", "intent", "rewrite", "multiquery", "summary", "classify",
})

def _ttl() -> int:
    try:
        from hashmm import settings
        return settings.get_int("HASHMM_PROMPT_CACHE_TTL", 3600)
    except Exception:
        return int(os.environ.get("HASHMM_PROMPT_CACHE_TTL", "3600") or "3600")


def cache_enabled() -> bool:
    try:
        from hashmm import settings
        return settings.get_bool("HASHMM_PROMPT_CACHE")
    except Exception:
        return os.environ.get("HASHMM_PROMPT_CACHE", "0").strip().lower() in {"1", "true", "yes", "on"}


def is_cacheable(task: str) -> bool:
    """只有确定性辅助任务可缓存；主答案类永不缓存。"""
    return task in _CACHEABLE_TASKS


def make_prompt_key(task: str, model: str, messages: list) -> str:
    """基于 task+model+messages 内容生成稳定 cache_key（对标 prompt_cache_key）。"""
    try:
        payload = json.dumps({"t": task, "m": model, "msgs": messages},
                             sort_keys=True, ensure_ascii=False)
    except Exception:
        payload = f"{task}:{model}:{messages}"
    return "promptcache:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def cached_llm_call(task: str, model: str, messages: list, call_fn):
    """对确定性辅助任务做结果缓存；其他情况直接调 call_fn。永不抛错。

    task: 任务类型（title/keyword/...）。仅 _CACHEABLE_TASKS 会缓存。
    call_fn: 无参 callable，返回 LLM 结果（命中缓存时不调用）。
    """
    # 关闭、或非确定性任务 → 直接透传（零变化）
    if not cache_enabled() or not is_cacheable(task):
        return call_fn()
    try:
        from hashmm import pipeline_cache as PC
        # 复用 get_or_compute：禁用透传/命中返回/计算缓存/异常不缓存，且只缓存非空结果
        return PC.get_or_compute(
            "promptcache",
            {"task": task, "model": model, "messages": messages},
            call_fn,
            ttl=_ttl(),
            should_cache=lambda v: bool(v),   # 不缓存空结果（避免缓存失败响应）
        )
    except Exception:
        return call_fn()


def stats() -> dict:
    """缓存概览。"""
    return {
        "enabled": cache_enabled(),
        "cacheable_tasks": sorted(_CACHEABLE_TASKS),
        "note": "只缓存确定性辅助任务，主答案(answer/reasoning)永不缓存",
    }
