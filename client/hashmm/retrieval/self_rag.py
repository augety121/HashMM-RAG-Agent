"""hashmm/retrieval/self_rag.py
Self-RAG 自我批判 / 自适应检索 —— 盖在「选项A(模型驱动多跳检索 + deepseek 作答)」之上的一层"自评"。

为什么需要它（对标大厂"不幻觉"的硬门槛）：
  选项A 已经把多跳答对率做到 83.3%，但它"答完就交"。大厂级 RAG 还要会**自我批判**：
  答完先让强模型判断「答案是否被证据支撑、证据是否充分」——
    · 不充分 → 用改写后的更聚焦子查询**自动再检索一轮**（自适应检索）；
    · 多轮后仍不充分 → 返回「资料不足」而不是硬编（忠实度门控）。
  这对标 Self-RAG (Asai et al.) 的自反思、RAG-Gym 的过程意识，是把"会查"升级成"会查 + 会自查"。

设计要点：
  · 纯调 LLM（复用 ServiceRegistry，与评测判官 / 选项A 合成同源），无新依赖，可离线 mock 测。
  · 初轮走选项A（7B 多跳 + deepseek）；再检索轮只用检索桥 + deepseek 合成（不再跑 7B 循环，省时）。
  · 任何一步不可用都安全退化：拿不到 LLM 就直接返回选项A 的结果，绝不卡死、绝不假装成功。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

log = logging.getLogger("hashmm.self_rag")

_CRITIQUE_PROMPT = (
    "你是严格的答案审稿员。下面给你一个问题、基于检索资料得到的答案、以及检索到的资料。\n"
    "请客观判断两点：\n"
    "  1) supported：答案是否**完全由资料支撑**（没有编造、没有资料外的结论）；\n"
    "  2) sufficient：资料是否**足以回答**这个问题。\n"
    "若不足，请再给出一个用于补充检索的、更聚焦的中文子查询 refined_query（否则为 null）。\n"
    "只输出 JSON，不要任何多余文字、不要代码块：\n"
    '{{"supported": true/false, "sufficient": true/false, "refined_query": "..." 或 null, "reason": "简述"}}\n\n'
    "问题：{q}\n\n答案：{a}\n\n检索到的资料：\n{ev}\n"
)


def _get_llm():
    """取批判用 LLM（deepseek，与判官同源）。失败返回 None。"""
    try:
        from hashmm.api.core.services import ServiceRegistry
        if not getattr(ServiceRegistry, "_initialized", False):
            ServiceRegistry.init_fast()
        if not getattr(ServiceRegistry, "_heavy_initialized", False):
            ServiceRegistry.init_heavy()
        return lambda p: ServiceRegistry.call_llm(p)
    except Exception as e:  # noqa: BLE001
        log.warning("[self_rag] 取批判 LLM 失败：%s", e)
        return None


def _parse_json(s):
    """从 LLM 输出里稳健解析 JSON 对象（容忍代码围栏 / 多余文字）。"""
    if not s:
        return None
    s = re.sub(r"```(?:json)?", "", str(s)).replace("```", "").strip()
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _evidence_text(sources: list) -> str:
    return "\n\n".join(
        f"[{i}] {(s.get('text') or s.get('content') or '')}" for i, s in enumerate(sources or [], 1))


def critique(question: str, answer: str, sources: list, llm=None) -> Optional[dict]:
    """让强模型自评答案的「支撑度 + 充分度」，并在不足时给出补检子查询。

    返回 ``{"supported": bool, "sufficient": bool, "refined_query": str|None, "reason": str}``；
    LLM 不可用 / 解析失败时返回 None（调用方据此跳过自评、直接用原答案）。
    """
    llm = llm or _get_llm()
    if llm is None or not answer:
        return None
    try:
        raw = llm(_CRITIQUE_PROMPT.format(q=question, a=answer, ev=_evidence_text(sources)))
    except Exception as e:  # noqa: BLE001
        log.warning("[self_rag] 批判调用异常：%s", e)
        return None
    obj = _parse_json(raw)
    if not obj:
        return None
    rq = obj.get("refined_query")
    if isinstance(rq, str):
        rq = rq.strip() or None
    else:
        rq = None
    return {
        "supported": bool(obj.get("supported")),
        "sufficient": bool(obj.get("sufficient")),
        "refined_query": rq,
        "reason": str(obj.get("reason") or ""),
    }


def self_rag_answer(question: str, *, top_k: int = 5, max_hops: int = 3, max_rounds: int = 2,
                    policy=None, search_fn=None,
                    base_model=None, lora_dir=None, max_new=None) -> Optional[dict]:
    """Self-RAG 主流程：选项A 作答 → 自我批判 → 不足则自适应再检索 → 仍不足则忠实度门控。

    可传入 ``policy`` / ``search_fn`` 复用**外部已加载**的策略与检索桥（评测脚本用，避免再加载一个
    7B 导致显存翻倍）；不传则走 searchr1_serving 的懒加载单例（服务端/独立调用用）。

    返回 dict：
        answer / initial_answer   最终答案 / 自评前的选项A初答（对照用；不足时最终答案为"资料不足"式说明）
        sources                   累计证据（含再检索补进来的）
        grounded / confidence     是否通过自评 / 粗粒度不确定性置信度
        rounds / critique / trace 再检索轮数 / 最后一次自评 / 每轮动作
    策略不可用返回 None —— 调用方回退原路径。
    """
    from hashmm.retrieval import searchr1_serving as ss

    llm = _get_llm()
    sf = search_fn or ss.build_search_fn(top_k)
    # 第 0 轮：选项A（模型驱动多跳检索 + deepseek 作答）。复用注入的 policy（若有），否则懒加载。
    if policy is not None:
        try:
            r0 = policy.run_search_loop(question, sf, max_hops=max_hops, top_k=top_k) or {}
        except Exception as e:  # noqa: BLE001
            log.warning("[self_rag] run_search_loop 异常：%s", e)
            r0 = {}
        sources = list(r0.get("sources") or [])
        ds = ss.synthesize_with_deepseek(question, sources)
        answer = ds or r0.get("answer")
        answer_by = "deepseek" if ds else "7b"
        if not sources and answer is None:
            return None
    else:
        res = ss.answer_multihop(question, top_k=top_k, max_hops=max_hops,
                                 base_model=base_model, lora_dir=lora_dir, max_new=max_new)
        if res is None:
            return None
        sources = list(res.get("sources") or [])
        answer = res.get("answer")
        answer_by = res.get("answer_by")
    initial_answer = answer            # 选项A 自评前的初答（用于对照"加没加自评的差别"）
    trace = [{"round": 0, "action": "multihop", "n_sources": len(sources)}]

    crit = critique(question, answer, sources, llm)
    rounds = 0
    seen = {(s.get("text") or "")[:80] for s in sources}
    # 自评不通过且给了补检子查询 → 自适应再检索（仅检索 + deepseek 合成，不再跑 7B 循环）
    while (crit and not (crit["supported"] and crit["sufficient"])
           and crit.get("refined_query") and rounds < max_rounds):
        rounds += 1
        rq = crit["refined_query"]
        try:
            more = sf(rq) or []
        except Exception as e:  # noqa: BLE001
            log.warning("[self_rag] 再检索异常：%s", e)
            more = []
        added = 0
        for m in more:
            k = (m.get("text") or "")[:80]
            if k and k not in seen:
                sources.append(m)
                seen.add(k)
                added += 1
        trace.append({"round": rounds, "action": "re_search", "query": rq,
                      "added": added, "n_sources": len(sources)})
        new_ans = ss.synthesize_with_deepseek(question, sources)
        if new_ans:
            answer = new_ans
        crit = critique(question, answer, sources, llm)

    grounded = bool(crit and crit["supported"] and crit["sufficient"])
    # 忠实度门控：多轮后仍判定资料不充分 → 明确"资料不足"，不硬编（保留已知相关部分）
    if crit is not None and not grounded and not crit.get("sufficient"):
        kept = f"已知与之相关的部分：{answer}" if answer else ""
        answer = ("根据现有知识库资料，暂时无法确定该问题的完整答案。" + kept
                  + "（如需更准确的回答，请补充相关资料或换个问法）")

    # 不确定性置信度（粗粒度但诚实的信号，呼应 UncertaintyRAG 思想）：
    #   初轮即自评通过=最高；经再检索才通过=中；被门控/没法自评=低。
    if crit is None:
        confidence = None                      # 自评不可用，不假装有把握
    elif grounded:
        confidence = 1.0 if rounds == 0 else 0.7
    else:
        confidence = 0.3
    # 若证据带 rerank 分，纳入参考（取 top 分，0~1 截断）
    _scores = [s.get("score") for s in sources if isinstance(s.get("score"), (int, float))]
    retrieval_top = round(min(max(max(_scores), 0.0), 1.0), 4) if _scores else None

    return {
        "answer": answer,
        "initial_answer": initial_answer,
        "sources": sources,
        "grounded": grounded,
        "confidence": confidence,
        "retrieval_top_score": retrieval_top,
        "rounds": rounds,
        "critique": crit,
        "trace": trace,
        "answer_by": answer_by,
    }


def main():
    """命令行直接对单个问题跑 Self-RAG（需在项目根、设 HASH_INDEX_DIR，模型在 4090 上）。

    例：
        HASH_INDEX_DIR=/root/autodl-tmp/data/vector_index HASHMM_SEARCHR1_LORA=\\
            /root/autodl-tmp/models/qwen2.5-7b-hashmm-final-v2 CUDA_VISIBLE_DEVICES=0 \\
            python -m hashmm.retrieval.self_rag -q "网易少数股东权益是远见医疗营收的多少倍？"
    """
    import argparse
    ap = argparse.ArgumentParser(
        description="Self-RAG：对单个问题跑 选项A + 自我批判 + 自适应再检索 + 忠实度门控")
    ap.add_argument("--question", "-q", required=True, help="要问的问题")
    ap.add_argument("--top_k", type=int, default=5)
    ap.add_argument("--max_hops", type=int, default=3)
    ap.add_argument("--max_rounds", type=int, default=2, help="自评不足时最多再检索几轮")
    args = ap.parse_args()

    r = self_rag_answer(args.question, top_k=args.top_k,
                        max_hops=args.max_hops, max_rounds=args.max_rounds)
    if r is None:
        print("[self_rag] 策略不可用（无 GPU / 模型缺失 / 不在项目根），无法运行。")
        return
    print("\n问题：", args.question)
    print(f"通过自评(grounded)={r['grounded']}  置信度={r['confidence']}  "
          f"再检索轮数={r['rounds']}  证据条数={len(r['sources'])}")
    print("\n— 自评前初答（选项A）—\n", r.get("initial_answer"))
    print("\n— Self-RAG 最终答案 —\n", r["answer"])
    if r.get("critique"):
        print("\n— 自评详情 —\n", r["critique"])
    print("\n— 轨迹 —\n", r["trace"])


if __name__ == "__main__":
    main()
