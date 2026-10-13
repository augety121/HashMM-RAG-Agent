"""hashmm/training/run_trained_retrieval.py — 端到端验证：训好的模型真正驱动你的知识库检索。

这是 P2 的收尾、也是整套方案的闭环验证。把三样东西串起来：
  1. 你训好的 LoRA 检索策略模型（SearchR1Policy）—— 负责「决定何时检索、检索什么、够没够」；
  2. AgenticRetriever 多跳回路 —— 负责把模型的 <search> 落地执行、汇总证据、按不确定性闸判停；
  3. 你真实的 BGE-M3 + FAISS + BM25 检索后端（kb_search_bridge）—— 负责返回**真实证据**。

跟 infer_lora 的区别：infer_lora 是模型独跑（<information> 是模型脑补的）；这里 <search> 会
真的打到你的知识库，证据是真的。这才是「模型决定检索 → 知识库给真证据 → 基于真证据答」的完整闭环。

用法（AutoDL，项目根目录；记得带上你知识库的索引目录）：
    HASH_INDEX_DIR=/root/autodl-tmp/data/vector_index \
    CUDA_VISIBLE_DEVICES=0 python -m hashmm.training.run_trained_retrieval \
        --model /root/autodl-tmp/models/Qwen2.5-7B-Instruct \
        --lora  /root/autodl-tmp/models/qwen2.5-7b-hashmm-lora \
        --q "网易2024年游戏业务收入是多少？" --top_k 5 --max_hops 3

输出会显示：每一跳模型决定搜什么、知识库真实返回了哪些来源、最终汇总到的证据。
"""
from __future__ import annotations

import argparse
import logging

logger = logging.getLogger("hashmm.training.run_trained_retrieval")


def _make_kb_search_fn(top_k: int):
    """把你真实的检索后端（kb_search_bridge）包装成 AgenticRetriever 要的 search_fn。

    search_fn(subquery) -> list[{text, filename, score, ...}]。失败返回空列表（回路安全降级）。
    """
    from hashmm.retriever_bridge import kb_search_bridge

    def search_fn(subquery: str) -> list:
        try:
            out = kb_search_bridge({"query": subquery, "top_k": top_k}) or {}
            results = out.get("results", []) or []
            # 回路按 text 字段读正文；kb_search_bridge 返回的是 content，做个映射
            norm = []
            for r in results:
                norm.append({
                    "text": r.get("content", r.get("text", "")),
                    "filename": r.get("filename", r.get("source", "")),
                    "page": r.get("page", -1),
                    "score": r.get("score", 0.0),
                    "id": f"{r.get('filename','')}|{r.get('page','')}",
                })
            return norm
        except Exception as e:
            logger.warning("kb_search failed: %s", e)
            return []
    return search_fn


def main():
    ap = argparse.ArgumentParser(description="端到端：训好的模型驱动真实知识库检索")
    ap.add_argument("--model", default="/root/autodl-tmp/models/Qwen2.5-7B-Instruct")
    ap.add_argument("--lora", default="/root/autodl-tmp/models/qwen2.5-7b-hashmm-lora")
    ap.add_argument("--q", default="网易2024年游戏业务收入是多少？", help="测试问题")
    ap.add_argument("--top_k", type=int, default=5, help="每次检索取几条")
    ap.add_argument("--max_hops", type=int, default=3, help="最多检索几轮")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # 1) 初始化你的真实检索后端（会加载 BGE-M3 + FAISS + BM25）
    print("[run] 初始化真实检索后端（BGE-M3 + FAISS + BM25）...")
    from hashmm.retriever_bridge import init_retriever
    try:
        init_retriever()
    except Exception as e:
        print(f"[run] 检索后端初始化失败：{e}\n  请确认带了 HASH_INDEX_DIR 且索引存在。")
        return
    search_fn = _make_kb_search_fn(args.top_k)

    # 先单独探一下检索后端是否正常
    probe = search_fn(args.q)
    print(f"[run] 检索后端自检：对问题检索到 {len(probe)} 条来源"
          + (f"，第一条来自 {probe[0].get('filename','?')}" if probe else "（空，检查索引/问题）"))

    # 2) 加载训好的策略模型
    print("[run] 加载训好的 LoRA 检索策略模型 ...")
    from hashmm.training.searchr1_policy import SearchR1Policy
    policy = SearchR1Policy(model_dir=args.model, lora_dir=args.lora)
    if not getattr(policy, "_ok", False):
        print("[run] 策略模型不可用（无 GPU 或加载失败）。本脚本意在验证模型，故退出。")
        return

    # 3) 原生 Search-R1 驱动：模型自己发 <search> → search_fn 真的去查你的知识库 → 注入真证据 → 续写
    print(f"\n[run] 开始端到端检索（原生 Search-R1 驱动，最多 {args.max_hops} 跳）...\n")
    result = policy.run_search_loop(args.q, search_fn, max_hops=args.max_hops, top_k=args.top_k)
    if result is None:
        print("[run] 驱动返回 None（策略不可用）。")
        return

    # 4) 展示闭环结果
    print("=" * 64)
    print("问题：", args.q)
    print(f"停止原因：{result.get('stopped_reason')}  | 跳数：{result.get('n_hops')}")
    print(f"模型发起的子查询：{['《'+str(s)+'》' for s in result.get('subqueries', [])]}")
    print("\n每一跳（模型决定搜什么 → 知识库真实返回）：")
    for step in result.get("trace", []):
        print(f"  hop{step['hop']} [{step['action']}] 查询=《{step['subquery']}》 "
              f"→ 累计来源 {step['n_sources_after']} 条")
    print("\n知识库真实检索到的证据（前 3 条）：")
    for s in result.get("sources", [])[:3]:
        print(f"  - {s.get('filename','?')}（score={s.get('score')}）：{(s.get('text','') or '')[:120]}")
    print(f"\n模型最终答案（基于真实证据）：{result.get('answer')!r}")
    print("=" * 64)
    print("说明：以上 <search> 是模型自己决定发起的，证据是从你知识库真实检索的，答案基于真证据——"
          "这就是「模型驱动检索 + 真实证据」的闭环。把这套接进 streaming 主路径，线上问答即可用上。")


if __name__ == "__main__":
    main()
