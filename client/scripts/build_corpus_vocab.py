#!/usr/bin/env python
"""Build the corpus vocabulary from the already-indexed BM25 corpus.

The refusal gate uses this vocab (data/corpus_vocab.json) to judge whether the
corpus covers a query's subject — data-driven, instead of the hard-coded brand
list. Run this once after indexing a new customer's documents (the batch-index
job also rebuilds it automatically; this CLI is for rebuilding standalone, e.g.
on your existing 22k-chunk index without re-ingesting).

    python scripts/build_corpus_vocab.py                 # default min_freq=3
    python scripts/build_corpus_vocab.py --min-freq 5    # stricter (large corpora)
    python scripts/build_corpus_vocab.py --show 40       # also print top terms
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Build corpus vocabulary from indexed chunks")
    ap.add_argument("--min-freq", type=int, default=3,
                    help="词必须出现在至少 N 个切片才保留（默认 3；大语料可调高）。")
    ap.add_argument("--show", type=int, default=20, help="额外打印前 N 个高频词（默认 20）。")
    ap.add_argument("--ascii-policy", choices=["drop", "smart"], default="drop",
                    help="纯英文词处理：drop=全丢(中文语料推荐,默认)；smart=保留品牌名、只丢术语/乱码。")
    args = ap.parse_args()

    from hashmm.retrieval_pipeline import RetrievalPipeline
    from hashmm.corpus_vocab import build_corpus_vocab, save_corpus_vocab, get_default_vocab

    print("加载已索引切片…")
    rp = RetrievalPipeline()
    rp.load()
    corpus = list(getattr(rp.bm25_index, "_corpus", []) or [])
    if not corpus:
        print("❌ 没有已索引切片。请先在「文档管理」索引文档。")
        return 2

    print(f"从 {len(corpus)} 个切片构建语料词典（min_freq={args.min_freq}, ascii={args.ascii_policy}）…")
    vocab = build_corpus_vocab(corpus, min_freq=args.min_freq, ascii_policy=args.ascii_policy)
    if not vocab:
        print("⚠️ 未抽到任何词（语料过小或全是样板）。未写入。")
        return 1
    save_corpus_vocab(vocab)
    get_default_vocab().reload()

    print(f"✅ 语料词典已写入 data/corpus_vocab.json：{len(vocab)} 词")
    if args.show > 0:
        # Show a sample (sorted by length desc as a rough salience proxy).
        sample = sorted(vocab, key=len, reverse=True)[: args.show]
        print(f"示例（{len(sample)}）：" + "  ".join(sample))
    print("\n拒答门控将自动使用它（无需重启）。换客户语料时重新索引会自动重建。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
