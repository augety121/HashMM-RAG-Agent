#!/usr/bin/env python
"""Generate an evaluation golden set from the CURRENT indexed corpus.

Replaces hand-written 小米/网易 cases with corpus-grounded ones, so quality can
be measured on whatever the customer actually ingested. Factual cases use the
LLM (DeepSeek or local Qwen); refusal cases are built from the corpus vocab
(Phase 40) and need no LLM.

    # preview (does NOT touch your live golden set)
    python scripts/build_eval_set.py --local-hf /root/autodl-tmp/models/Qwen2.5-7B-Instruct
    # write to data/eval/golden_cases.json after you've reviewed the preview
    python scripts/build_eval_set.py --local-hf <model> --write
    # refusal-only (no LLM needed)
    python scripts/build_eval_set.py --no-llm --write
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a corpus-grounded eval golden set")
    ap.add_argument("--n-factual", type=int, default=40, help="生成多少 factual 用例（默认 40）。")
    ap.add_argument("--n-refusal", type=int, default=12, help="生成多少 refusal 用例（默认 12）。")
    ap.add_argument("--out", default="data/eval/golden_generated.json",
                    help="预览输出路径（默认 golden_generated.json，不动线上集）。")
    ap.add_argument("--write", action="store_true",
                    help="直接写入线上集 data/eval/golden_cases.json（覆盖！请先预览确认）。")
    ap.add_argument("--no-llm", action="store_true", help="只生成 refusal 用例（不调用 LLM）。")
    ap.add_argument("--local-hf", default="", help="本地 HF 模型目录（生成 factual 用例）。")
    ap.add_argument("--show", type=int, default=8, help="打印前 N 条预览（默认 8）。")
    args = ap.parse_args()

    # Resolve llm_fn (unless --no-llm).
    llm_fn = None
    if not args.no_llm:
        if args.local_hf:
            from hashmm.kg.local_hf_llm import build_hf_llm_fn
            llm_fn = build_hf_llm_fn(args.local_hf, max_new_tokens=512)
        else:
            from hashmm.api.model_manager import get_active_llm_fn
            llm_fn, _ = get_active_llm_fn()
        if not callable(llm_fn):
            print("⚠️ 没有可用 LLM；将只生成 refusal 用例（或用 --local-hf 指本地模型）。")

    # Load corpus + vocab.
    from hashmm.retrieval_pipeline import RetrievalPipeline
    from hashmm.corpus_vocab import load_corpus_vocab
    print("加载已索引切片…")
    rp = RetrievalPipeline(); rp.load()
    corpus = [{"text": m.get("text", ""), "doc_id": m.get("doc_id", ""),
               "filename": m.get("filename", "")}
              for m in (getattr(rp.bm25_index, "_corpus", []) or [])]
    vocab = load_corpus_vocab()
    if not vocab:
        print("ℹ️ 未找到语料词典；refusal 用例仍可生成，但建议先跑 scripts/build_corpus_vocab.py。")

    from hashmm.evaluation.eval_set_builder import build_eval_set, write_eval_set
    result = build_eval_set(
        corpus, llm_fn=llm_fn,
        n_factual=args.n_factual, n_refusal=args.n_refusal,
        corpus_vocab_terms=vocab,
    )
    meta = result["meta"]
    print(f"\n生成完成：factual={meta['n_factual']}  refusal={meta['n_refusal']}  "
          f"总计={meta['n_total']}  (LLM={'是' if meta['llm_used'] else '否'})")

    if args.show > 0:
        print(f"\n预览前 {args.show} 条：")
        for c in result["cases"][: args.show]:
            print(f"  [{c['category']}] {c['query']}"
                  + (f"  → {c.get('reference_answer', '')[:50]}" if c.get("reference_answer") else ""))

    out_path = "data/eval/golden_cases.json" if args.write else args.out
    if args.write:
        print("\n⚠️ --write：即将覆盖线上评测集 data/eval/golden_cases.json")
    write_eval_set(result, out_path)
    print(f"\n✅ 已写入 {out_path}（{meta['n_total']} 条）")
    if not args.write:
        print("这是预览文件，未动线上集。确认无误后加 --write 写入线上集。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
