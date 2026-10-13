"""discovery — 自发现（V103.31，准确版 §3.1 / P4②）。

让 Agent 不只被动答问，而是**主动扫描系统状态、找出"该做的活"并surface给用户**（你此前偏反应式）。
从几个现成、便宜、只读的信号里发现机会：KG 健康、检索质量、语料规模、是否有定时主动任务。
每条给优先级 + 建议动作。可注册成 scheduler action 定时跑（呼应「从定时任务起步」），也可按需调。

铁律：扫描全程**只读、永不抛错**；拿不到某信号就跳过那条，不影响其余。
"""
from __future__ import annotations

import time


def _kg_signal(findings: list) -> None:
    """KG 健康：有实体但 0 社区 → 建议构建社区检索；无实体 → 建议建图。"""
    try:
        from hashmm.kg.kg_retriever import get_kg_retriever
        kr = get_kg_retriever()
        if not getattr(kr, "is_available", False):
            findings.append({"kind": "kg", "priority": "medium", "title": "知识图谱尚未建立",
                             "detail": "未检索到已加载的图谱。导入文档并跑实体抽取后，可解锁 GraphRAG（实体/关系/社区融合检索）。",
                             "action": "导入文档 + 抽取实体", "action_kind": "goto_kb"})
            return
        st = kr.stats() or {}
        ents = int(st.get("kg_entities", 0) or 0)
        rels = int(st.get("kg_relations", 0) or 0)
        comms = 0
        try:
            g = getattr(kr, "_kg", None)
            if g is not None and hasattr(g, "stats"):
                gs = g.stats() or {}
                comms = int(gs.get("communities", 0) or 0)
                ents = int(gs.get("entities", ents) or ents)
        except Exception:
            pass
        if ents >= 50 and comms == 0:
            findings.append({"kind": "kg", "priority": "high", "title": "知识图谱可构建社区检索",
                             "detail": f"已有 {ents} 实体 / {rels} 关系，但社区数为 0。构建社区能开启全局检索（global mode），"
                                       f"显著提升跨文档的综合性问答。",
                             "action": "构建社区检索", "action_kind": "build_communities"})
        elif 0 < ents < 50:
            findings.append({"kind": "kg", "priority": "low", "title": "知识图谱规模较小",
                             "detail": f"当前仅 {ents} 实体。继续导入资料能让 GraphRAG 检索更有覆盖。",
                             "action": "继续导入文档", "action_kind": "goto_kb"})
    except Exception:
        pass


def _retrieval_signal(findings: list) -> None:
    """检索质量：不足率偏高 → 知识库可能有盲区。"""
    try:
        from hashmm import observability
        snap = observability.dashboard_snapshot() or {}
        rq = snap.get("retrieval_quality", {}) or {}
        ins = rq.get("insufficient_rate")
        if isinstance(ins, (int, float)) and ins >= 0.3:
            findings.append({"kind": "retrieval", "priority": "high", "title": "检索经常不足，知识库可能有盲区",
                             "detail": f"约 {round(ins * 100)}% 的查询检索结果不足。这些主题可能缺资料，"
                                       f"建议补充对应文档，或检查切分策略是否合适。",
                             "action": "补充资料 / 检查检索", "action_kind": "goto_kb"})
    except Exception:
        pass


def _proactive_signal(findings: list) -> None:
    """是否有定时主动任务：没有 → 建议设一个，让 Agent 不等你问就定期推。"""
    try:
        from hashmm import scheduler
        tasks = scheduler.list_tasks()
        if not tasks:
            findings.append({"kind": "proactive", "priority": "low", "title": "还没有定时主动任务",
                             "detail": "设一个每日「语料简报」或「KG 健康巡检」，让 Agent 定期把系统状态主动推给你——"
                                       "不等你问、提前为你做。",
                             "action": "创建每日语料简报", "action_kind": "create_digest"})
    except Exception:
        pass


def _corpus_signal(findings: list) -> None:
    """语料规模：为空 → 提示导入。"""
    try:
        from hashmm.api.core.services import ServiceRegistry
        ServiceRegistry.ensure_loaded()
        nd = int(getattr(ServiceRegistry, "n_docs", 0) or 0)
        if nd == 0:
            findings.append({"kind": "corpus", "priority": "medium", "title": "语料库为空",
                             "detail": "还没有文档。导入资料后 RAG 检索才能发挥作用。",
                             "action": "导入文档", "action_kind": "goto_kb"})
    except Exception:
        pass


def run_discovery() -> dict:
    """跑一次自发现。返回 {generated_at, findings:[{kind,priority,title,detail,action}]}（按优先级排序）。"""
    findings: list = []
    for sig in (_kg_signal, _retrieval_signal, _corpus_signal, _proactive_signal):
        try:
            sig(findings)
        except Exception:
            pass
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order.get(f.get("priority"), 3))
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "findings": findings}


def action_discovery(params: dict) -> str:
    """scheduler action：定时跑自发现，返回一句摘要（落到任务的 last_result）。只读。"""
    try:
        r = run_discovery()
        fs = r.get("findings", [])
        if not fs:
            return "自发现：暂无需要主动处理的事项"
        return f"自发现 {len(fs)} 项：" + "；".join(f.get("title", "") for f in fs[:3])
    except Exception as e:
        return f"discovery unavailable: {type(e).__name__}"
