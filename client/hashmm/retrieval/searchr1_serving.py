"""hashmm/retrieval/searchr1_serving.py
Search-R1 服务端适配器 —— 把训练好的策略 run_search_loop 封装成服务端可直接调用的单函数，
让 api 问答主路径能用上最终权重（qwen2.5-7b-hashmm-final）做「模型驱动检索 + 带出处作答」。

为什么需要它：
  api/streaming.py 现在走「检索流水线取证据 → 注入 → deepseek 合成答案」，多跳用老 AgenticRetriever。
  训练好的 run_search_loop（模型自己发 <search> → 真实检索 → 注入真证据 → 续写 <answer>）此前只在
  评测脚本里被调用。本模块是把它接进服务端的「桥」。

设计要点（重要，避免搞坏现有聊天）：
  - 懒加载：进程首次调用才把 LoRA 模型载上 GPU；不调用就不占显存，默认聊天路径完全不受影响。
  - 线程安全单例：并发请求共用一个已加载策略；加载失败只试一次，不反复卡 GPU。
  - search_fn 复用项目现成检索桥 kb_search_bridge（BGE-M3 + BM25 + rerank），与评测脚本同款归一化。
  - 不可用（无 GPU / 依赖缺失 / 权重目录不存在）时一律返回 None，调用方据此**回退到原 deepseek 合成路径**。

环境变量（都有默认值，可不设）：
  HASHMM_SEARCHR1_LORA    最终 LoRA 权重目录（默认 .../qwen2.5-7b-hashmm-final）
  HASHMM_BASE_MODEL       基座目录（默认 .../Qwen2.5-7B-Instruct）
  HASHMM_SEARCHR1_MAXNEW  单步最大生成 token（默认 384）
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

log = logging.getLogger("hashmm.searchr1_serving")

_DEFAULT_BASE = "/root/autodl-tmp/models/Qwen2.5-7B-Instruct"
_DEFAULT_LORA = "/root/autodl-tmp/models/qwen2.5-7b-hashmm-final"

_policy = None              # 已加载的 SearchR1Policy 单例
_load_attempted = False     # 是否已尝试过加载（失败后不再重试）
_lock = threading.Lock()


def _resolve(base_model, lora_dir, max_new):
    base = base_model or os.environ.get("HASHMM_BASE_MODEL", _DEFAULT_BASE)
    lora = lora_dir or os.environ.get("HASHMM_SEARCHR1_LORA", _DEFAULT_LORA)
    try:
        mn = int(max_new if max_new is not None else os.environ.get("HASHMM_SEARCHR1_MAXNEW", "384"))
    except (TypeError, ValueError):
        mn = 384
    return base, lora, mn


def get_policy(*, base_model=None, lora_dir=None, max_new=None):
    """懒加载训练好的策略（单例）。不可用返回 None，且只尝试加载一次以免每次请求都卡 GPU。"""
    global _policy, _load_attempted
    if _policy is not None:
        return _policy
    with _lock:
        if _policy is not None:
            return _policy
        if _load_attempted:
            return None
        _load_attempted = True
        base, lora, mn = _resolve(base_model, lora_dir, max_new)
        if not os.path.isdir(lora):
            log.warning("[searchr1] LoRA 目录不存在，禁用模型驱动检索，回退原路径：%s", lora)
            return None
        try:
            from hashmm.training.searchr1_policy import SearchR1Policy
            pol = SearchR1Policy(base, lora, max_new_tokens=mn)
        except Exception as e:  # noqa: BLE001 —— 加载失败必须回退，不能让聊天崩
            log.warning("[searchr1] 策略加载异常，回退原路径：%s", e)
            return None
        if not getattr(pol, "_ok", False):
            log.warning("[searchr1] 策略不可用（可能无 GPU 或依赖缺失），回退原路径")
            return None
        _policy = pol
        log.info("[searchr1] 模型驱动检索已就绪：%s + LoRA %s", base, lora)
        return _policy


def searchr1_available(**kw) -> bool:
    """是否可用（会触发一次懒加载）。供 streaming.py 在选路时判断。"""
    return get_policy(**kw) is not None


def build_search_fn(top_k: int = 5, *, acl=None,
                    principal: str | None = None,
                    document_scope: list[str] | None = None) -> Callable[[str], list]:
    """复用项目现成检索桥 kb_search_bridge，构造 run_search_loop 用的 search_fn。

    归一化字段与 eval_retrieval_policy._make_kb_search_fn 完全一致（text/filename/page/score）。
    """
    from hashmm.retriever_bridge import kb_search_bridge

    def search_fn(subquery: str) -> list:
        try:
            payload = {"query": subquery, "top_k": top_k}
            scoped_ctx = (
                {
                    "user_id": str(principal or ""),
                    "doc_filter": list(document_scope or []),
                }
                if principal is not None or document_scope is not None
                else None
            )
            if scoped_ctx is None:
                out = kb_search_bridge(payload) or {}
            else:
                try:
                    out = kb_search_bridge(payload, scoped_ctx) or {}
                except TypeError:
                    # A legacy one-argument bridge can only be tolerated when
                    # an explicit ACL will still filter every result below.
                    # Owner-only or user-selected scopes must never fall back
                    # to an unscoped bridge.
                    if acl is None or document_scope is not None:
                        raise
                    out = kb_search_bridge(payload) or {}
            results = out.get("results", []) or []
            if acl is not None:
                from hashmm.access_control import filter_results
                results = filter_results(results, acl, principal)
            norm = []
            for r in results:
                norm.append({
                    "text": r.get("content", r.get("text", "")),
                    "filename": r.get("filename", r.get("source", "")),
                    "page": r.get("page", -1),
                    "score": r.get("score", 0.0),
                })
            return norm
        except Exception as e:  # noqa: BLE001
            log.warning("[searchr1] 检索桥调用失败：%s", e)
            return []

    return search_fn


def answer_with_searchr1(question: str, *, top_k: int = 5, max_hops: int = 3,
                         search_fn: Optional[Callable[[str], list]] = None,
                         acl=None, principal: str | None = None,
                         document_scope: list[str] | None = None,
                         base_model=None, lora_dir=None, max_new=None) -> Optional[dict]:
    """用训练好的策略做一次「模型驱动检索 + 带出处作答」。

    返回 run_search_loop 的结果 dict（含 answer / sources / trace / stopped_reason / n_hops 等）；
    不可用或运行异常时返回 None —— 调用方据此回退到原 deepseek 合成路径。
    """
    pol = get_policy(base_model=base_model, lora_dir=lora_dir, max_new=max_new)
    if pol is None:
        return None
    sf = search_fn or build_search_fn(
        top_k,
        acl=acl,
        principal=principal,
        document_scope=document_scope,
    )
    try:
        return pol.run_search_loop(question, sf, max_hops=max_hops, top_k=top_k)
    except Exception as e:  # noqa: BLE001
        log.warning("[searchr1] run_search_loop 运行异常，回退原路径：%s", e)
        return None


def synthesize_with_deepseek(question: str, sources: list) -> Optional[str]:
    """用 deepseek 基于检索证据合成最终答案 —— 与评测里 hybrid 同款「一步步算清楚」提示词
    （该提示词在多跳测试题上把答对率从 26.7% 提到 83.3%，这里把它固化成产品可直接调用的函数）。
    失败 / 无证据返回 None。"""
    if not sources:
        return None
    try:
        from hashmm.api.core.services import ServiceRegistry
        if not getattr(ServiceRegistry, "_initialized", False):
            ServiceRegistry.init_fast()
        if not getattr(ServiceRegistry, "_heavy_initialized", False):
            ServiceRegistry.init_heavy()
    except Exception as e:  # noqa: BLE001
        log.warning("[searchr1] deepseek 初始化失败：%s", e)
        return None
    evidence = "\n\n".join(
        f"[{i}] {(s.get('text') or s.get('content') or '')}" for i, s in enumerate(sources, 1))
    prompt = (
        "你是严谨的企业知识库问答助手。只依据下面检索到的资料回答问题；"
        "需要计算时一步步算清楚，给出简洁准确、含关键数字的最终答案；资料不足就直说，不要编造。\n\n"
        f"问题：{question}\n\n检索到的资料：\n{evidence}\n\n最终答案："
    )
    try:
        return ServiceRegistry.call_llm(prompt)
    except Exception as e:  # noqa: BLE001
        log.warning("[searchr1] deepseek 合成失败：%s", e)
        return None


def answer_multihop(question: str, *, top_k: int = 5, max_hops: int = 3,
                    acl=None, principal: str | None = None,
                    base_model=None, lora_dir=None, max_new=None) -> Optional[dict]:
    """一站式「选项A」：7B 驱动(多跳)检索取证据 → deepseek 合成最终答案（经真机验证的 83.3% 路径）。

    返回 dict（含 answer / sources / trace / stopped_reason / n_hops / answer_by）；策略不可用返回 None。
    deepseek 不可用时自动退回 7B 自己的答案（仍带证据），answer_by 标明是谁作答。
    """
    res = answer_with_searchr1(question, top_k=top_k, max_hops=max_hops,
                               acl=acl, principal=principal,
                               base_model=base_model, lora_dir=lora_dir, max_new=max_new)
    if res is None:
        return None
    ds = synthesize_with_deepseek(question, res.get("sources") or [])
    res = dict(res)
    if ds:
        res["answer"] = ds
        res["answer_by"] = "deepseek"
    else:
        res["answer_by"] = "7b"
    return res


def reset_for_test():
    """仅供测试：清空单例与加载标记。"""
    global _policy, _load_attempted
    _policy = None
    _load_attempted = False
