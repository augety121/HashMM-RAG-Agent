"""hashmm/local_semantic.py — 桌面端本地小模型嵌入客户端（V98，可选增强）。

后端以桌面 sidecar 模式运行时，桌面主进程可在壳层端口上提供
``POST /local/embed``（bge-small-zh INT8 · onnxruntime，模型由用户在
桌面"后端连接"页一键下载），并通过环境变量 ``HASHMM_LOCAL_EMBED_URL``
告知后端。本模块是该服务的 stdlib 客户端 + 检索候选的余弦重排融合，
由 retrieval_pipeline 的 Step 6.5 调用——让装不上 FlagEmbedding 的
轻量机型也能享受语义排序，全程不出本机。

铁律对齐：
- 默认关：env 未设 → 一切函数原样返回，行为零变化
- 永不抛错：任何网络 / 解析 / 计算异常 → 返回原输入
- 零新增依赖：urllib + json + math，无任何第三方
- 服务宕机熔断：单次失败后 30s 冷却期内不再发请求，不拖慢检索主链
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.request
from typing import Any, List, Optional, Sequence

ENV_KEY = "HASHMM_LOCAL_EMBED_URL"
_TIMEOUT = 3.0          # 本机回环，3s 封顶（正常 <100ms）
_COOLDOWN = 30.0        # 失败熔断窗口
_MAX_TEXTS = 32         # 单次请求文本上限（壳层硬限 64，留余量）
_MAX_CHARS = 1000       # 单条文本截断

_state = {"down_until": 0.0}


def _url() -> str:
    return (os.environ.get(ENV_KEY) or "").strip()


def available() -> bool:
    """env 已设且不在熔断冷却期。纯本地判断，不发网络请求。"""
    return bool(_url()) and time.time() >= _state["down_until"]


def embed_texts(texts: Sequence[str]) -> Optional[List[List[float]]]:
    """调本地服务取嵌入向量。失败返回 None 并进入冷却，永不抛错。"""
    url = _url()
    if not url or time.time() < _state["down_until"]:
        return None
    payload = [str(t or "")[:_MAX_CHARS] for t in list(texts)[:_MAX_TEXTS]]
    if not payload:
        return None
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"texts": payload}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        vecs = data.get("vectors") if isinstance(data, dict) else None
        if not data.get("ok") or not isinstance(vecs, list) or len(vecs) != len(payload):
            raise ValueError("bad response shape")
        return [[float(x) for x in v] for v in vecs]
    except Exception:
        _state["down_until"] = time.time() + _COOLDOWN
        return None


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    try:
        s = na = nb = 0.0
        for x, y in zip(a, b):
            s += x * y
            na += x * x
            nb += y * y
        if na <= 0.0 or nb <= 0.0:
            return 0.0
        return s / math.sqrt(na * nb)
    except Exception:
        return 0.0


def maybe_local_rerank(query: str, results: List[Any], top_k: int) -> List[Any]:
    """对检索候选做本地语义重排；任何前提不满足 → 原样返回（同一列表对象）。

    只重排头部候选 ``min(len, max(top_k*3, 24), _MAX_TEXTS-1)``，query 与候选
    合并为**一次**请求；融合分 ``blend = 0.5*原序归一 + 0.5*余弦``（RRF 分数
    尺度不一，用名次做先验更稳）。重排头部回写 score（保持头部单调），
    尾部原序拼回、分数不动。
    """
    try:
        if not available() or not query or len(results) <= 1:
            return results
        n = min(len(results), max(int(top_k) * 3, 24), _MAX_TEXTS - 1)
        cands = results[:n]
        texts = [str(query)[:_MAX_CHARS]] + [
            str(getattr(r, "text", "") or "")[:_MAX_CHARS] for r in cands
        ]
        vecs = embed_texts(texts)
        if not vecs or len(vecs) != len(texts):
            return results
        qv, cvs = vecs[0], vecs[1:]
        m = float(len(cands))
        scored = []
        for i, (r, cv) in enumerate(zip(cands, cvs)):
            orig = 1.0 - (i / m)                 # 原序归一先验
            blend = 0.5 * orig + 0.5 * _cosine(qv, cv)
            scored.append((blend, i, r))
        scored.sort(key=lambda t: (-t[0], t[1]))  # 同分保持原序（稳定）
        head: List[Any] = []
        for blend, _i, r in scored:
            try:
                r.score = round(float(blend), 4)
            except Exception:
                pass
            head.append(r)
        return head + results[n:]
    except Exception:
        return results


def _reset_for_tests() -> None:
    """测试钩子：清熔断状态（铁律 8：测试自管现场）。"""
    _state["down_until"] = 0.0
