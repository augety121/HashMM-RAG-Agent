"""V103.48 — LLM 网关基座（对标 9Router 的多 provider 故障转移 + CC Switch 的多配置档切换）。

为什么自研而不是塞外来代码
--------------------------
项目里已经有相当完整的零件：``model_manager.py`` 支持 8 个 provider 且自带多 API Key
轮换（逗号分隔 + 401/403/429 自动换 key）；``model_router.py`` 按任务复杂度路由模型。
9Router / CC Switch 的核心能力——多 provider 智能转发 + 失败自动切换、多套 Key 配置一键
切换——大部分已具备。本模块只补齐这两个开源项目相对你现状真正缺的那一层，并把它们
统一成一个干净的基座，而不是把两份外部代码硬塞进来与现有逻辑打架。

本模块补齐的能力
----------------
1) **多 Provider 故障转移（9Router 的核心）**：一个逻辑模型可以配置一条「备援链」
   （primary → fallback1 → fallback2…）。主 provider 整体不可用（鉴权连环失败 / 限流 /
   连接失败 / 5xx）时，自动切到下一个 provider/模型，对调用方透明。现有的单 provider 内
   多 key 轮换继续在每个节点内部生效——两层互补：先轮 key，key 全挂再切 provider。
2) **配置档（CC Switch 的核心）**：把「一组 provider+key+base_url」存成命名 profile，
   可一键切换当前生效档（如 在 "deepseek主用" 和 "本地ollama离线" 之间切）。落盘到
   ``data/llm_profiles.json``，重启保留。

设计原则：包装而非替换。每个备援节点仍复用 ``make_llm_fn_from_model`` 产出的 llm_fn，
所以 stream / call / call_with_tools 三个接口、多 key 轮换、usage 记账全部原样保留。
故障转移只在「节点整体失败」时发生，正常路径零开销、零行为变化。
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from hashmm.utils import get_logger, log_suppressed

logger = get_logger("hashmm.llm_gateway")

_PROFILES_PATH = Path("data/llm_profiles.json")
_lock = threading.Lock()


# ── 故障转移判定 ───────────────────────────────────────────────────────────
# 这些错误意味着「这个 provider 节点整体不可用」，应切到备援节点，而不是重试同一个。
_FAILOVER_MARKERS = (
    "401", "403", "429", "500", "502", "503", "504",
    "timeout", "timed out", "connection", "unavailable",
    "rate limit", "overloaded", "insufficient_quota", "quota",
)


def _is_failover_error(err: Exception) -> bool:
    s = str(err).lower()
    return any(m in s for m in _FAILOVER_MARKERS)


@dataclass
class ProviderNode:
    """备援链上的一个节点：一套完整的模型配置（provider+model+key+base_url）。"""
    name: str
    config: dict                      # 传给 make_llm_fn_from_model 的 model_cfg
    _fn: Any = field(default=None, repr=False)

    def llm_fn(self):
        """惰性构建并缓存该节点的 llm_fn（复用现有 make_llm_fn_from_model）。"""
        if self._fn is None:
            from hashmm.api.model_manager import make_llm_fn_from_model
            self._fn = make_llm_fn_from_model(self.config)
        return self._fn


class FailoverLLM:
    """对外暴露与单个 llm_fn 完全一致的接口（stream / call / call_with_tools /
    quick_call），内部按备援链顺序尝试，节点整体失败时透明切到下一个。

    调用方（streaming.py / loop.py）无需任何改动即可获得故障转移能力——只要把
    ServiceRegistry.llm_fn 指向一个 FailoverLLM 实例即可。
    """

    def __init__(self, nodes: list[ProviderNode]):
        self.nodes = [n for n in nodes if n is not None]
        self._active_idx = 0  # 粘滞：成功过的节点优先，减少无谓切换

    # 属性透传：取首个可用节点的 model_name 等元信息
    @property
    def model_name(self) -> str:
        for n in self.nodes:
            fn = n.llm_fn()
            if fn is not None:
                return getattr(fn, "model_name", "") or getattr(fn, "model", "") or ""
        return ""

    def _ordered_nodes(self):
        """活动节点优先，其余按配置顺序。"""
        if not self.nodes:
            return []
        idx = self._active_idx if 0 <= self._active_idx < len(self.nodes) else 0
        return [self.nodes[idx]] + [n for i, n in enumerate(self.nodes) if i != idx]

    def _call_with_failover(self, method: str, *args, **kwargs):
        """对非流式方法（call / call_with_tools / quick_call）做故障转移。"""
        last_err = None
        for node in self._ordered_nodes():
            fn = node.llm_fn()
            if fn is None or not hasattr(fn, method):
                continue
            try:
                out = getattr(fn, method)(*args, **kwargs)
                self._active_idx = self.nodes.index(node)  # 记住成功节点
                return out
            except Exception as e:
                last_err = e
                if _is_failover_error(e) and len(self.nodes) > 1:
                    logger.warning(f"[Gateway] 节点 '{node.name}' 失败（{str(e)[:80]}），切到下一备援")
                    continue
                raise
        if last_err:
            raise last_err
        raise RuntimeError("没有可用的 LLM 节点")

    def stream(self, messages: list[dict], *, temp_override: float | None = None):
        """流式故障转移：一个节点在【产出任何 token 之前】失败 → 切下一个。
        若已经吐出 token 再断，则不静默切换（避免答案拼接错乱），按上游异常处理。
        """
        last_err = None
        for node in self._ordered_nodes():
            fn = node.llm_fn()
            if fn is None or not hasattr(fn, "stream"):
                continue
            produced = False
            try:
                for tok in fn.stream(messages, temp_override=temp_override):
                    produced = True
                    yield tok
                self._active_idx = self.nodes.index(node)
                return
            except Exception as e:
                last_err = e
                if not produced and _is_failover_error(e) and len(self.nodes) > 1:
                    logger.warning(f"[Gateway] 流式节点 '{node.name}' 起步失败（{str(e)[:80]}），切下一备援")
                    continue
                raise
        if last_err:
            raise last_err

    def call(self, prompt: str) -> str:
        return self._call_with_failover("call", prompt)

    def call_with_tools(self, messages: list[dict], tools=None, tool_choice: str = "auto"):
        return self._call_with_failover("call_with_tools", messages, tools, tool_choice)

    def quick_call(self, system: str, user: str, max_tokens: int = 150) -> str:
        # 部分节点 fn 可能没有 quick_call，_call_with_failover 会自动跳过到有的节点
        return self._call_with_failover("quick_call", system, user, max_tokens)

    def __call__(self, prompt: str) -> str:
        return self.call(prompt)


def build_failover_llm(model_cfgs: list[dict]) -> FailoverLLM | None:
    """从一串模型配置（按优先级排列）构建 FailoverLLM。空/全无效 → None。"""
    nodes = []
    for i, cfg in enumerate(model_cfgs or []):
        if not cfg:
            continue
        nodes.append(ProviderNode(name=cfg.get("name") or cfg.get("model") or f"node{i}", config=cfg))
    if not nodes:
        return None
    return FailoverLLM(nodes)


# ── 配置档（CC Switch 的核心）─────────────────────────────────────────────
@dataclass
class LLMProfile:
    """一个命名配置档：一组按优先级排列的 provider 配置 + 元信息。"""
    name: str
    providers: list[dict]             # [{name, provider, model, api_key, base_url, ...}, ...]
    note: str = ""


def _load_profiles() -> dict:
    try:
        if _PROFILES_PATH.exists():
            return json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log_suppressed(logger, e)
    return {"active": "", "profiles": {}}


def _save_profiles(data: dict) -> bool:
    try:
        _PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PROFILES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        log_suppressed(logger, e)
        return False


def list_profiles() -> dict:
    """返回 {active: name, profiles: {name: {...}}}。"""
    return _load_profiles()


def save_profile(name: str, providers: list[dict], note: str = "", *, set_active: bool = False) -> bool:
    """新增/更新一个配置档。set_active=True 同时切为当前生效档。"""
    if not name or not providers:
        return False
    with _lock:
        data = _load_profiles()
        data.setdefault("profiles", {})[name] = {"providers": providers, "note": note}
        if set_active or not data.get("active"):
            data["active"] = name
        return _save_profiles(data)


def delete_profile(name: str) -> bool:
    with _lock:
        data = _load_profiles()
        if name in data.get("profiles", {}):
            del data["profiles"][name]
            if data.get("active") == name:
                data["active"] = next(iter(data["profiles"]), "")
            return _save_profiles(data)
    return False


def switch_profile(name: str) -> bool:
    """一键切换当前生效配置档（CC Switch 的核心动作）。"""
    with _lock:
        data = _load_profiles()
        if name not in data.get("profiles", {}):
            return False
        data["active"] = name
        return _save_profiles(data)


def get_active_llm() -> FailoverLLM | None:
    """构建当前生效配置档对应的 FailoverLLM；无配置档则返回 None（调用方退回
    现有的单模型逻辑，零行为变化）。"""
    data = _load_profiles()
    active = data.get("active", "")
    prof = data.get("profiles", {}).get(active)
    if not prof:
        return None
    return build_failover_llm(prof.get("providers", []))
