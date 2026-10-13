"""hashmm/agent/tool_retrieval.py — 工具检索（Tool Retrieval，V310）。

对标大厂 agent 的关键能力：当可用工具很多时，**不再把全部工具塞进每次 LLM 调用**，
而是按当前查询【语义检索】最相关的 top-k 个工具。

为什么需要（这是 2026 年 agent 的共识，也是我给你讲过的"企业有 500 个 API 时的 Tool Retrieval"）：
  1. 成本：每个工具的 JSON schema 占几十到几百 token。30 个工具就是几千 token，每轮都付；
     接上用户自配 API + MCP server 后可能上百个，全塞进 prompt 是纯浪费。
  2. 准确率：工具越多，模型选错工具的概率越高（"迷失在工具海里"）。给它 8 个相关的比给
     100 个更容易选对。研究反复证明：相关工具子集 > 全量工具。

设计（三层，保证永不"因为检索把该有的工具弄没了"）：
  · 核心工具白名单：文件读写/shell/回复类高频工具【始终在】，不参与检索、不会被过滤掉。
  · 语义检索：用仓库已有的 BGE-M3 给每个工具的 "名字 + 描述" 编码，按 query 相似度取 top-k。
  · 关键词回退：没有编码器（GPU 不可用/模型没加载）时，退化为工具名/描述的关键词匹配，
    仍比全量塞好，且零依赖。

安全与稳健：
  · 工具集小于阈值（默认 ≤12）时【整体不检索】——本来就没几个，检索反而增加不确定性。
  · 任何异常都回退到"返回全部工具"（fail-open 到旧行为，绝不让 agent 拿不到工具）。
  · 检索结果做了缓存（按 query + 工具集指纹），同一轮多次调用不重复编码。

默认【关闭】，用 HASHMM_TOOL_RETRIEVAL=1 开启（或工具数超过 HASHMM_TOOL_RETRIEVAL_AUTO 自动开）。
这样对现有行为零影响，你可以在评测里单独打开看效果。
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from hashmm.utils import get_logger

logger = get_logger("hashmm.agent.tool_retrieval")

# 始终保留的核心工具（高频、跨任务通用；不参与检索、不被过滤）。
# 依据：这些是 agent 的"手脚"，几乎任何任务都可能用到，检索掉它们弊大于利。
CORE_TOOLS = {
    # 文件与执行（Terminal/SWE 类任务的命脉）
    "run_shell", "read_file", "read_file_range", "create_file", "str_replace",
    "edit_file", "list_files", "file_tree", "execute_code",
    # 知识与联网（★ V317 补：此前只有 kb_search。web_search/fetch_url 被裁掉的话
    # GAIA 这类"必须联网搜索"的任务会直接崩——没搜必错；memory_recall 是跨会话
    # 连续性的入口。这三个属于"裁掉就砍掉整类能力"，代价远大于省下的 schema token。）
    "kb_search", "web_search", "fetch_url", "memory_recall",
}

# 工具集小于此值就不检索（本来就不多，全给更稳）。
MIN_TOOLS_TO_RETRIEVE = int(os.environ.get("HASHMM_TOOL_RETRIEVAL_MIN", "12"))
# 检索返回的工具数（不含核心工具）。
DEFAULT_TOP_K = int(os.environ.get("HASHMM_TOOL_RETRIEVAL_TOPK", "8"))


def _tool_name(t: dict) -> str:
    return (t.get("function", {}) or {}).get("name", "") or t.get("name", "")


def _tool_text(t: dict) -> str:
    """工具的可检索文本 = 名字 + 描述（描述权重更高，重复一次名字增强）。"""
    fn = t.get("function", {}) or {}
    name = fn.get("name", "") or t.get("name", "")
    desc = fn.get("description", "") or t.get("description", "")
    # 参数名也带上一点（有时 query 提到的是参数概念，如 "url"/"path"）
    params = fn.get("parameters", {}) or {}
    prop_names = " ".join((params.get("properties", {}) or {}).keys())
    return f"{name} {name} {desc} {prop_names}".strip()


def _fingerprint(tools: list[dict]) -> str:
    names = "|".join(sorted(_tool_name(t) for t in tools))
    return hashlib.md5(names.encode("utf-8")).hexdigest()[:12]


class _SemanticIndex:
    """工具的语义索引（用 BGE-M3 编码工具描述）。按工具集指纹缓存，工具集不变就不重建。"""

    _fp: str | None = None
    _vecs = None            # np.ndarray (n_tools, dim)
    _names: list[str] = []

    @classmethod
    def _build(cls, tools: list[dict]) -> bool:
        try:
            import numpy as np  # noqa: F401
            from hashmm.encoder_pool import EncoderPool
            texts = [_tool_text(t) for t in tools]
            vecs = EncoderPool.encode_texts(texts)   # L2 normalized (n, dim)
            cls._vecs = vecs
            cls._names = [_tool_name(t) for t in tools]
            cls._fp = _fingerprint(tools)
            return True
        except Exception as e:  # noqa: BLE001  没有编码器/GPU → 交给关键词回退
            logger.debug(f"tool semantic index unavailable, fallback to keyword: {e}")
            return False

    @classmethod
    def rank(cls, query: str, tools: list[dict], top_k: int) -> list[str] | None:
        """返回按相关度排序的工具名（仅候选池，不含核心工具）。失败返回 None。"""
        fp = _fingerprint(tools)
        if cls._fp != fp or cls._vecs is None:
            if not cls._build(tools):
                return None
        try:
            import numpy as np
            from hashmm.encoder_pool import EncoderPool
            qv = EncoderPool.encode_query(query)      # (1, dim), normalized
            sims = (cls._vecs @ qv.reshape(-1)).astype(float)   # 余弦相似度（都已归一化）
            order = np.argsort(-sims)
            return [cls._names[i] for i in order[:top_k]]
        except Exception as e:  # noqa: BLE001
            logger.debug(f"tool semantic rank failed: {e}")
            return None


# 简易缓存：同一 query + 工具集只算一次。
_CACHE: dict[tuple[str, str], list[dict]] = {}


def _tokenize(text: str) -> set[str]:
    """分词（用于关键词回退）。英文按词，中文按 bigram（二元字组）。

    为什么中文要 bigram：中文没有空格分词，`\\w+` 会把整串中文当成一个"词"，
    "做PPT演示文稿" ↔ "生成PPT演示文稿" overlap 永远是 0。用相邻两字的 bigram
    （做P/PP/PT/演示/示文/文稿…）就能捕捉到重叠，不引入 jieba 依赖。
    """
    s = (text or "").lower()
    tokens: set[str] = set()
    # 英文/数字词
    for w in re.findall(r"[a-z0-9_]+", s):
        if w:
            tokens.add(w)
    # 中文单字 + bigram
    han = re.findall(r"[\u4e00-\u9fff]", s)
    tokens.update(han)                                   # 单字
    tokens.update(han[i] + han[i + 1] for i in range(len(han) - 1))   # bigram
    return tokens


def _keyword_rank(query: str, tools: list[dict], top_k: int) -> list[str]:
    """无编码器时的关键词回退：按 query 词与工具文本的重叠打分。"""
    q_tokens = _tokenize(query)
    scored = []
    for t in tools:
        t_tokens = _tokenize(_tool_text(t))
        overlap = len(q_tokens & t_tokens)
        # 名字直接出现在 query 里 → 强加分
        name = _tool_name(t).lower()
        bonus = 3 if name and name in (query or "").lower() else 0
        scored.append((overlap + bonus, _tool_name(t)))
    scored.sort(reverse=True)
    return [n for s, n in scored[:top_k] if s > 0]


def should_retrieve(tools: list[dict]) -> bool:
    """是否启用工具检索：显式开关 > 自动阈值 > 默认按工具规模自动判定。

    ★ V317：自动阈值从 9999（= 永远不会自动开，能力形同死代码）改为 30。
    依据指南 §二.5「企业有 500 个 API 时你需要的是工具检索」——本机内置 23 个工具
    还撑得住全量塞给模型，但工具列表是【内置 + 用户自配 + 每个 MCP server】三路
    合并的：接两个 MCP server 就可能破百。工具面一旦变大，全量塞进 prompt 的代价
    是实打实的（每个 schema 上百 token × 每轮都付；候选越多越容易选错工具）。
    30 这个阈值的含义：内置工具面（23）不受影响、零行为变化；一旦用户接了 MCP
    或自配大批工具，检索自动介入。仍可用 HASHMM_TOOL_RETRIEVAL=0 强制关闭。
    """
    flag = (os.environ.get("HASHMM_TOOL_RETRIEVAL") or "").strip().lower()
    if flag in ("1", "true", "on", "yes"):
        return len(tools) > MIN_TOOLS_TO_RETRIEVE
    if flag in ("0", "false", "off", "no"):
        return False
    auto = int(os.environ.get("HASHMM_TOOL_RETRIEVAL_AUTO", "30"))
    return len(tools) > auto and len(tools) > MIN_TOOLS_TO_RETRIEVE


def select_tools(query: str, tools: list[dict], top_k: int | None = None) -> list[dict]:
    """按 query 选出相关工具子集（核心工具始终包含）。

    返回的列表保持"核心工具 + 检索到的相关工具"，去重、保序（核心在前）。
    任何情况下都不会返回空——最差回退到全部工具（fail-open）。
    """
    if not tools:
        return tools
    if not should_retrieve(tools):
        return tools

    top_k = top_k or DEFAULT_TOP_K
    key = (f"{query}|{top_k}", _fingerprint(tools))
    if key in _CACHE:
        return _CACHE[key]

    try:
        by_name = {_tool_name(t): t for t in tools if _tool_name(t)}
        # 候选池 = 非核心工具（核心工具无条件保留，不占检索名额）
        candidates = [t for t in tools if _tool_name(t) not in CORE_TOOLS]

        ranked = _SemanticIndex.rank(query, candidates, top_k)
        if ranked is None:
            ranked = _keyword_rank(query, candidates, top_k)

        # 组装：核心工具（原序）+ 检索到的相关工具（相关度序），去重
        chosen_names: list[str] = []
        for t in tools:
            n = _tool_name(t)
            if n in CORE_TOOLS and n not in chosen_names:
                chosen_names.append(n)
        for n in ranked:
            if n not in chosen_names:
                chosen_names.append(n)

        result = [by_name[n] for n in chosen_names if n in by_name]
        # 兜底：只在结果异常地少（连一个非核心工具都没选出，说明检索完全失效）时才回退全部。
        # 注意不能用 len(CORE_TOOLS) 当阈值——候选池里的核心工具可能本就不全，会误触发。
        n_core_present = sum(1 for t in tools if _tool_name(t) in CORE_TOOLS)
        if len(result) <= n_core_present:   # 一个相关工具都没检索到 → 检索失效，回退全部
            _CACHE[key] = tools
            return tools
        _CACHE[key] = result
        logger.info("工具检索：%d 个工具 → 选出 %d 个（query=%.20s）",
                    len(tools), len(result), query)
        return result
    except Exception as e:  # noqa: BLE001  任何异常 → 回退全部工具，绝不让 agent 断手
        logger.debug(f"tool retrieval failed, using all tools: {e}")
        return tools


def select_tools_observed(query: str, tools: list[dict],
                          top_k: int | None = None,
                          schema_budget_chars: int | None = None) -> tuple[list[dict], dict]:
    """select_tools 的可观测版本：返回 (选中工具, observability)。

    V319：把工具检索纳入 Context Engine 统一可观测。observability 字段：
      total       原始工具数
      kept        保留的工具数
      retrieved   是否真的做了检索（工具面小/关闭时为 False，此时 kept==total）
      dropped     被裁掉的工具数
      core        保留的核心工具名
      picked      检索到的非核心工具名
      reason      人话说明
    """
    tools = list(tools or [])
    n = len(tools)
    obs = {"total": n, "kept": n, "retrieved": False, "dropped": 0,
           "core": [], "picked": [], "reason": ""}

    if not should_retrieve(tools):
        flag = (os.environ.get("HASHMM_TOOL_RETRIEVAL") or "").strip().lower()
        if flag in ("0", "false", "off", "no"):
            obs["reason"] = "工具检索已关闭（HASHMM_TOOL_RETRIEVAL=0）"
        else:
            auto = int(os.environ.get("HASHMM_TOOL_RETRIEVAL_AUTO", "30"))
            obs["reason"] = f"工具面 {n} 个（≤{auto}），全量给模型更稳，未裁剪"
        result = tools
        if schema_budget_chars:
            result = _fit_schema_budget(result, int(schema_budget_chars))
            if len(result) < n:
                obs.update({
                    "kept": len(result), "retrieved": True,
                    "dropped": n - len(result),
                    "reason": f"工具 schema 超出本轮预算，{n} 个 → 保留 {len(result)} 个",
                })
        return result, obs

    result = select_tools(query, tools, top_k)
    if len(result) >= n:                    # 回退全部（检索失效）或未裁剪
        obs["reason"] = f"工具检索未生效或回退全部（{n} 个工具）"
        return result, obs

    kept_names = {_tool_name(t) for t in result}
    obs.update({
        "kept": len(result), "retrieved": True, "dropped": n - len(result),
        "core": [_tool_name(t) for t in result if _tool_name(t) in CORE_TOOLS],
        "picked": [_tool_name(t) for t in result if _tool_name(t) not in CORE_TOOLS],
        "reason": f"工具面 {n} 个 → 检索保留 {len(result)} 个"
                  f"（核心 {sum(1 for nm in kept_names if nm in CORE_TOOLS)} + "
                  f"相关 {sum(1 for nm in kept_names if nm not in CORE_TOOLS)}），"
                  f"省下 {n - len(result)} 个工具的 schema token",
    })
    if schema_budget_chars:
        bounded = _fit_schema_budget(result, int(schema_budget_chars))
        if len(bounded) < len(result):
            obs["kept"] = len(bounded)
            obs["dropped"] = n - len(bounded)
            obs["reason"] += f"；按本轮 schema 预算进一步收敛为 {len(bounded)} 个"
            result = bounded
    return result, obs


def _fit_schema_budget(tools: list[dict], budget_chars: int) -> list[dict]:
    """Keep the selected order while bounding serialized schema size.

    Core tools are always retained.  The budget only removes trailing
    non-core tools selected by semantic rank.  This makes cost predictable
    without silently removing the Agent's basic read/write/search capability.
    """

    if budget_chars <= 0:
        return tools
    core = [tool for tool in tools if _tool_name(tool) in CORE_TOOLS]
    optional = [tool for tool in tools if _tool_name(tool) not in CORE_TOOLS]
    chosen = list(core)
    used = sum(len(json.dumps(tool, ensure_ascii=False, sort_keys=True)) for tool in chosen)
    for tool in optional:
        size = len(json.dumps(tool, ensure_ascii=False, sort_keys=True))
        if chosen and used + size > budget_chars:
            continue
        chosen.append(tool)
        used += size
    return chosen or tools[:1]


def clear_cache() -> None:
    _CACHE.clear()
    _SemanticIndex._fp = None
    _SemanticIndex._vecs = None
