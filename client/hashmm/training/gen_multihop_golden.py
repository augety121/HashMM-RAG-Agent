"""hashmm/training/gen_multihop_golden.py
用 LLM 从企业语料造**多跳(桥接/对比)题** —— 必须查两段不同文档才能回答的中文问题 + 标准答案。

为什么需要它：
  单跳 SFT 教不出"把复杂问题拆成多次检索"的能力，所以 HASHMM_SEARCHR1 那条路在单跳下
  ≈ 单次检索（评测已证打平）。要让"模型驱动检索"真正比单次强，必须有这种"两跳"训练数据。

做法：
  从【两份不同文档】各取一段资料(A、B)，让 LLM 出一道**同时用到 A 和 B 才能答**的对比/综合题
  （如两家公司同一指标对比、求差额/倍数，或一项依赖另一项的桥接题），给出答案与两个子问题；
  落成带 ``hops`` 的金标准。build_sft_data.case_to_sft 见到 hops 会自动渲染成**两跳监督轨迹**
  （search→information→search→information→answer），无需额外参数。

用法（项目根目录，需 HASH_INDEX_DIR）：
    HASH_INDEX_DIR=/root/autodl-tmp/data/vector_index \
    python -m hashmm.training.gen_multihop_golden \
        --out /root/autodl-tmp/data/golden/golden_multihop.json --n 300
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

_PROMPT = (
    "你是企业知识库出题专家。下面是来自【两份不同企业文档】的两段资料。\n"
    "请基于这两段资料，出一道**必须同时用到资料A和资料B才能回答**的中文问题"
    "（例如：对两家公司的同一指标做对比、求差额或倍数；或一项数据依赖另一项的桥接问题），"
    "并给出标准答案，以及分别针对资料A、资料B的两个子问题。\n"
    "要求：问题必须真的需要两段资料，只看其中一段无法回答；答案简洁准确、含关键数字。\n"
    "只输出 JSON，不要任何多余文字、不要 markdown 代码块：\n"
    '{{"question":"...","answer":"...","subq_a":"...","subq_b":"..."}}\n\n'
    "【资料A · 来自 {fa}】\n{ta}\n\n【资料B · 来自 {fb}】\n{tb}\n"
)


def _chunk_text(c: dict) -> str:
    return (c.get("text") or c.get("content") or "").strip()


def _chunk_file(c: dict) -> str:
    return (c.get("filename") or c.get("source") or "").strip()


def _parse_json(s: str):
    """从 LLM 输出里稳健解析出 JSON 对象（容忍代码围栏/多余文字）。"""
    if not s:
        return None
    s = re.sub(r"```(?:json)?", "", s).replace("```", "").strip()
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def generate_multihop(chunks: list, llm_fn, n: int, seed: int = 0) -> list:
    """从不同文档配对生成多跳题。返回带 hops 的金标准列表（跨文档配对，保证真两跳）。"""
    rng = random.Random(seed)
    by_doc: dict[str, list] = {}
    for c in chunks:
        if len(_chunk_text(c)) < 40:  # 太短的段跳过
            continue
        fn = _chunk_file(c)
        if fn:
            by_doc.setdefault(fn, []).append(c)
    docs = list(by_doc.keys())
    out: list[dict] = []
    if len(docs) < 2:
        return out
    attempts = 0
    while len(out) < n and attempts < n * 4 + 20:
        attempts += 1
        da, db = rng.sample(docs, 2)
        ta = _chunk_text(rng.choice(by_doc[da]))[:800]
        tb = _chunk_text(rng.choice(by_doc[db]))[:800]
        try:
            resp = llm_fn(_PROMPT.format(fa=da, ta=ta, fb=db, tb=tb))
        except Exception:
            continue
        obj = _parse_json(resp if isinstance(resp, str) else str(resp))
        if not obj:
            continue
        q = (obj.get("question") or "").strip()
        a = (obj.get("answer") or "").strip()
        if not q or not a:
            continue
        out.append({
            "query": q,
            "reference_answer": a,
            "hops": [
                {"subq": (obj.get("subq_a") or q).strip(), "evidence": ta},
                {"subq": (obj.get("subq_b") or q).strip(), "evidence": tb},
            ],
            "supporting": [ta, tb],
            "source_docs": [da, db],
            "multihop": True,
        })
    return out


def main():
    ap = argparse.ArgumentParser(description="用 LLM 从企业语料造多跳(桥接/对比)题")
    ap.add_argument("--out", default="/root/autodl-tmp/data/golden/golden_multihop.json")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    from hashmm.training.gen_golden_from_corpus import _load_corpus_chunks, _get_llm_fn
    chunks = _load_corpus_chunks()
    if not chunks:
        print("[multihop] ✗ 没读到企业语料 chunk（确认在项目根目录、已设 HASH_INDEX_DIR）。")
        return
    docs = len({_chunk_file(c) for c in chunks if _chunk_file(c)})
    print(f"[multihop] 语料 {len(chunks)} 段，覆盖 {docs} 份文档；开始生成 {args.n} 道多跳题 ...")
    if docs < 2:
        print("[multihop] ✗ 文档少于 2 份，无法造跨文档多跳题。")
        return
    cases = generate_multihop(chunks, _get_llm_fn(), n=args.n, seed=args.seed)
    if not cases:
        print("[multihop] ✗ 没生成出题（检查后台 LLM 配置）。")
        return
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[multihop] ✓ 生成 {len(cases)} 道多跳题 → {args.out}")
    print("[multihop] 下一步：并入合并 → build_sft（多跳目标自动渲染）→ 训 -final-v2。")


if __name__ == "__main__":
    main()
