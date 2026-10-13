"""v17 Phase 109 — terminal status dashboard ("is everything wired & ready?").

A single read-only command that snapshots the system before you test:
  * KG size (entities / relations / communities) and a health summary (Phase 93)
  * which agentic feature flags are ON (Phase 107)
  * whether the local Qwen is available for free loop routing (Phase 106)
  * retrieval index size (vectors / BM25 docs)

Read-only (no rebuild, no writes); every probe is guarded; never raises. The
formatter is pure and unit-tested; ``__main__`` collects the live snapshot.
"""
from __future__ import annotations

from typing import Any

from hashmm.utils import get_logger, log_suppressed
from hashmm.agent.smoke import feature_flags, _FLAGS

logger = get_logger("hashmm.agent.status")


def _kg_snapshot() -> dict:
    out = {"entities": None, "relations": None, "communities": None, "health": None}
    try:
        from hashmm.kg.storage import KGStorage
        st = KGStorage().get_stats() or {}
        out["entities"] = st.get("entities")
        out["relations"] = st.get("relations")
        out["communities"] = st.get("communities")
    except Exception as e:
        log_suppressed(logger, e)
    return out


def _kg_health() -> dict | None:
    try:
        from hashmm.kg.storage import KGStorage
        from hashmm.kg.kg_metrics import kg_health_report
        kg, _ = KGStorage().load()
        if kg is None or getattr(kg, "num_entities", 0) == 0:
            return None
        rep = kg_health_report(kg)
        noise = rep.get("noise", {}) or {}
        return {"avg_degree": rep.get("avg_degree"), "density": rep.get("density"),
                "components": rep.get("components"),
                "noise_rate": noise.get("rate")}
    except Exception as e:
        log_suppressed(logger, e)
        return None


def _index_snapshot() -> dict:
    out = {"vectors": None, "bm25_docs": None}
    # Read straight from disk (faiss read + pickle) — cheap, no encoder/GPU needed.
    try:
        from hashmm.vector_index import VectorIndex
        vi = VectorIndex()
        vi.load()
        out["vectors"] = vi.ntotal
    except Exception as e:
        log_suppressed(logger, e)
    try:
        from hashmm.retrieval_pipeline import BM25Index
        bm = BM25Index()
        bm.load()
        out["bm25_docs"] = getattr(bm, "size", None) or len(getattr(bm, "_corpus", []) or [])
    except Exception as e:
        log_suppressed(logger, e)
    # Fallback: if still empty, try the live chat pipeline (server process only)
    if out["vectors"] is None and out["bm25_docs"] is None:
        try:
            from hashmm.chat_retrieval import get_chat_retrieval
            pipe = getattr(get_chat_retrieval(), "_pipeline", None)
            if pipe is not None:
                corpus = getattr(getattr(pipe, "bm25_index", None), "_corpus", None)
                if corpus is not None:
                    out["bm25_docs"] = len(corpus)
        except Exception as e:
            log_suppressed(logger, e)
    return out


def _local_qwen_ready() -> bool:
    try:
        from hashmm.agent.local_routing import get_local_loop_llm
        return get_local_loop_llm() is not None
    except Exception:
        return False


def collect_status() -> dict:
    """Best-effort live status snapshot. Never raises."""
    return {
        "kg": _kg_snapshot(),
        "kg_health": _kg_health(),
        "index": _index_snapshot(),
        "flags": feature_flags(),
        "local_qwen_ready": _local_qwen_ready(),
    }


def _fmt(v):
    return "—" if v is None else v


def _recommendations(status: dict) -> list:
    """Actionable advice based on the live health snapshot. Pure; never raises."""
    tips = []
    try:
        kg = status.get("kg", {}) or {}
        h = status.get("kg_health") or {}
        ents = kg.get("entities") or 0
        comps = h.get("components")
        nrate = h.get("noise_rate")
        avg_deg = h.get("avg_degree")
        # 碎片化分两种成因，给不同建议（修复旧版"一律说正则建图"的误报）：
        if isinstance(comps, int) and ents and comps > ents * 0.3:
            dense = isinstance(avg_deg, (int, float)) and avg_deg >= 2.5
            if dense:
                # 高平均度 + 高碎片 → 多半是正则共现图：换 LLM 语义抽取更连贯
                tip = (f"图谱碎片化（{comps} 连通块 / {ents} 实体，平均度 {avg_deg}）：稠密但碎，"
                       "多半是正则建图。可带 HASHMM_KG_LLM_EXTRACT=1 + HASHMM_KG_LLM_HF_PATH "
                       "用本地 Qwen 语义抽取（kg_build_cli）重建，关系更连贯。")
            else:
                # 低平均度 + 高碎片 → LLM 精确但边稀疏：原地补连通，**不必重建**
                tip = (f"图谱碎片化（{comps} 连通块 / {ents} 实体，平均度 {avg_deg}）：边偏少导致连通低"
                       "（数学上 连通块≥实体−关系）。无需重建——跑 "
                       "`HASHMM_KG_RESOLVE_FUZZY=85 HASHMM_KG_RESOLVE_PINYIN=1 "
                       "HASHMM_KG_RESOLVE_DROP_NOISE=1 python -m hashmm.kg.entity_resolution` 调消解，"
                       "再 `python -m hashmm.kg.kg_connectivity` 共现补边，连通块会大降。")
            tips.append(tip)
        if isinstance(nrate, (int, float)) and nrate > 0.10:
            tips.append(f"噪声率偏高（{nrate}）：跑  HASHMM_KG_RESOLVE_DROP_NOISE=1 python -m hashmm.kg.entity_resolution  "
                        "合并变体并去掉日期/数字泄漏类噪声后再用。")
    except Exception:
        pass
    return tips


def format_status(status: dict) -> str:
    """Readable status report. Pure; never raises."""
    kg = status.get("kg", {}) or {}
    idx = status.get("index", {}) or {}
    flags = status.get("flags", {}) or {}
    h = status.get("kg_health")
    lines = ["==================== HashMM 状态面板 ===================="]
    lines.append(f"知识图谱：实体 {_fmt(kg.get('entities'))} / 关系 {_fmt(kg.get('relations'))} "
                 f"/ 社区 {_fmt(kg.get('communities'))}")
    if h:
        lines.append(f"图谱健康：平均度 {_fmt(h.get('avg_degree'))} / 密度 {_fmt(h.get('density'))} "
                     f"/ 连通块 {_fmt(h.get('components'))} / 噪声率 {_fmt(h.get('noise_rate'))}")
    else:
        lines.append("图谱健康：（未构建或无法读取）")
    lines.append(f"检索索引：向量 {_fmt(idx.get('vectors'))} / BM25 文档 {_fmt(idx.get('bm25_docs'))}")
    lines.append(f"本地 Qwen 循环：{'就绪 ✓' if status.get('local_qwen_ready') else '未就绪 ✗（循环将用付费模型；配置 HASHMM_KG_LLM_EXTRACT + HASHMM_KG_LLM_HF_PATH 启用免费本地）'}")
    desc = dict(_FLAGS)
    on = [desc[k] for k, v in flags.items() if v]
    off = [desc[k] for k, v in flags.items() if not v]
    lines.append("特性开 ✓：" + ("、".join(on) if on else "（全关，默认行为）"))
    lines.append("特性关 ✗：" + ("、".join(off) if off else "（无）"))
    tips = _recommendations(status)
    if tips:
        lines.append("—— 建议 ——")
        for t in tips:
            lines.append("• " + t)
    lines.append("=" * 56)
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_status(collect_status()))
